"""meme_manager 的人格表情包库隔离适配层。"""

from __future__ import annotations

import asyncio
import contextvars
import importlib
import inspect
import logging
from contextlib import contextmanager
from functools import wraps
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

from .policy_store import (
    MEME_DEFAULT_LIBRARY_ID,
    MEME_MANAGER_NAMESPACE,
    default_meme_isolation,
)


log = logging.getLogger(__name__)

MEME_PLUGIN_NAMES = {"meme_manager", "astrbot_plugin_meme_manager"}
EFFECTIVE_PERSONA_EVENT_KEY = "user_policy_effective_persona_id"
PERSONA_RESULT_EVENT_KEY = "user_policy_persona_result"
WRAPPED_MARKER = "_user_policy_meme_wrapped"
OWNER_ATTR = "_user_policy_meme_owner"
ORIGINAL_ATTR = "_user_policy_meme_original"
RUNTIME_METHODS = (
    "inject_meme_prompt",
    "resp",
    "handle_upload_image",
    "list_emotions",
    "add_category_command",
    "upload_meme",
    "restore_default_memes_command",
    "clear_category_command",
    "clear_all_emojis_command",
    "delete_category_command",
    "check_sync_status",
    "show_library_stats",
    "sync_to_remote",
    "sync_from_remote",
    "overwrite_to_remote",
    "overwrite_from_remote",
)
DEPRECATED_RUNTIME_METHODS = (
    "on_decorating_result",
    "after_message_sent",
)


class MemeManagerPersonaAdapter:
    """在不修改 meme_manager 源码的前提下临时切换表情库路径。"""

    def __init__(
        self,
        plugin: Any,
        data_dir: Path,
        fallback_plugins: dict[str, Any] | None = None,
    ):
        self.plugin = plugin
        self.data_dir = Path(data_dir)
        self.fallback_plugins = fallback_plugins or {}
        self.root_dir = self.data_dir / "meme_persona_libraries"
        self.enabled = False
        self.installed = False
        self.activated = False
        self.applied = False
        self.message = "表情包库隔离将自动适配。"
        self.target: Any | None = None
        self.target_metadata: Any | None = None
        self._lock = asyncio.Lock()
        self._active_namespace: contextvars.ContextVar[str] = (
            contextvars.ContextVar("user_policy_meme_namespace", default="default")
        )
        self._library_depth: contextvars.ContextVar[int] = (
            contextvars.ContextVar("user_policy_meme_depth", default=0)
        )
        self._originals: dict[str, Callable[..., Any]] = {}
        self._handler_originals: dict[str, Callable[..., Any]] = {}
        self._default_paths: dict[str, Path] = {}

    def configure(self) -> None:
        config = self._config()
        self.enabled = bool(config.get("enabled", False))
        if not self.enabled:
            self.restore()
            self.refresh_default_library_paths()
            self.message = "表情包库隔离未启用。"
            return

        target = self._find_target()
        if target is None:
            self.restore()
            self.installed = False
            self.activated = False
            self.applied = False
            self.message = "未检测到已加载的 meme_manager。"
            return

        if self.target is not None and self.target is not target:
            metadata = self.target_metadata
            self.restore()
            self.target_metadata = metadata

        self.installed = True
        self.activated = self._is_activated(target)
        if not self.activated:
            self.restore()
            self.applied = False
            self.message = "meme_manager 当前未启用。"
            return

        compatible, reason = self._is_compatible(target)
        if not compatible:
            self.restore()
            self.applied = False
            self.message = reason
            return

        self.target = target
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._remember_default_paths(target)
        self._restore_deprecated_wrappers(target)
        self._wrap_methods(target)
        self._wrap_registry_handlers(target)
        if not self._originals and not self._handler_originals:
            self.applied = False
            if getattr(target, "_user_policy_meme_adapter", None) is not self:
                self.message = "meme_manager 已由其他适配器接管，未重复包裹。"
            else:
                self.message = "meme_manager 已启用，但未找到可安全包裹的运行入口。"
            return
        self.applied = True
        self.message = (
            "已启用按用户策略人格隔离表情包库"
            f"（实例方法 {len(self._originals)} 个，消息处理器 "
            f"{len(self._handler_originals)} 个）；"
            "meme_manager 自带 WebUI 仍管理默认库。"
        )

    def restore(self) -> None:
        """恢复被本适配器包裹的方法，供关闭、卸载和热重载使用。"""

        target = self.target
        if target is not None:
            for name, original in self._originals.items():
                try:
                    setattr(target, name, original)
                except Exception as exc:
                    log.warning("[用户策略] 恢复 meme_manager 方法 %s 失败：%s", name, exc)

            for handler in self._registry_handlers(target):
                key = str(getattr(handler, "handler_full_name", "") or id(handler))
                original = self._handler_originals.get(key)
                if original is None:
                    continue
                try:
                    handler.handler = original
                except Exception as exc:
                    log.warning("[用户策略] 恢复 meme_manager 处理器 %s 失败：%s", key, exc)

            if getattr(target, "_user_policy_meme_adapter", None) is self:
                try:
                    delattr(target, "_user_policy_meme_adapter")
                except Exception:
                    try:
                        setattr(target, "_user_policy_meme_adapter", None)
                    except Exception:
                        pass

        self._originals.clear()
        self._handler_originals.clear()
        self.target = None
        self.applied = False

    def report(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "installed": self.installed,
            "activated": self.activated,
            "applied": self.applied,
            "message": self.message,
            "root_dir": str(self.root_dir),
            "wrapped_method_count": len(self._originals),
            "wrapped_handler_count": len(self._handler_originals),
            "wrapped_methods": sorted(self._originals),
        }

    @staticmethod
    def namespace_for_persona(persona_id: str) -> str:
        value = str(persona_id or "").strip()
        if not value:
            return "default"
        digest = sha256(value.encode("utf-8")).hexdigest()[:16]
        return f"persona_{digest}"

    def library_paths(self, namespace: str) -> dict[str, Path]:
        namespace = namespace or "default"
        if namespace == MEME_MANAGER_NAMESPACE:
            return self._meme_manager_paths()
        base = self.root_dir / namespace
        return {
            "data_dir": base,
            "memes_dir": base / "memes",
            "data_path": base / "memes_data.json",
            "init_marker": base / ".default_memes_initialized",
            "readonly": False,
        }

    def _meme_manager_paths(self) -> dict[str, Any]:
        """Meme Manager 真实库路径(只读)。优先读活动模块,回退捕获值。"""

        if not self._has_default_paths():
            self.refresh_default_library_paths()
        memes_dir = self._default_paths.get("MEMES_DIR")
        data_path = self._default_paths.get("MEMES_DATA_PATH")
        if memes_dir is None or data_path is None:
            placeholder = self.root_dir / "__meme_manager_unavailable__"
            return {
                "data_dir": placeholder,
                "memes_dir": placeholder / "memes",
                "data_path": placeholder / "memes_data.json",
                "init_marker": None,
                "readonly": True,
            }
        memes_dir = Path(memes_dir)
        data_path = Path(data_path)
        return {
            "data_dir": memes_dir.parent,
            "memes_dir": memes_dir,
            "data_path": data_path,
            "init_marker": None,
            "readonly": True,
        }

    def refresh_default_library_paths(self) -> bool:
        """只探测 Meme Manager 默认图库路径,不包裹运行时方法。"""

        target = self.target or self._find_target()
        if target is None:
            self.installed = False
            self.activated = False
            return self._has_default_paths()

        self.installed = True
        self.activated = self._is_activated(target)
        paths = self._read_default_paths(target)
        if not paths:
            return self._has_default_paths()
        self.target = target
        self._default_paths = paths
        return True

    def ensure_library(self, namespace: str) -> dict[str, Path]:
        """返回指定 namespace 的库目录;只读(Meme Manager)库不创建/写入。"""

        paths = self.library_paths(namespace)
        if not paths.get("readonly"):
            self._ensure_library(paths)
        return paths

    def namespace_for_event(self, event: Any) -> str:
        decision = None
        getter = getattr(event, "get_extra", None)
        if callable(getter):
            decision = getter(getattr(self.plugin, "EXTRA_DECISION", ""))
        if decision is None:
            creator = getattr(self.plugin, "_get_or_create_decision", None)
            if callable(creator):
                try:
                    decision = creator(event)
                except Exception:
                    decision = None
        persona_id = str(getattr(decision, "persona_id", "") or "").strip()
        if not persona_id and callable(getter):
            persona_id = self._persona_from_extra(
                getter(EFFECTIVE_PERSONA_EVENT_KEY),
            )
        if not persona_id and callable(getter):
            persona_id = self._persona_from_extra(
                getter(getattr(self.plugin, "EXTRA_PERSONA_RESULT", "")),
            )
        if not persona_id and callable(getter):
            persona_id = self._persona_from_extra(
                getter(PERSONA_RESULT_EVENT_KEY),
            )
        return self.resolve_namespace_for_persona(persona_id)

    def resolve_namespace_for_persona(self, persona_id: str) -> str:
        """按「人格→命名库」映射解析运行时使用的库 namespace。

        未映射、映射到默认库、或映射到已删除的库时,回退到 Meme Manager
        的只读真实库。
        """

        pid = str(persona_id or "").strip()
        if not pid:
            return MEME_MANAGER_NAMESPACE
        lib_id = self._persona_library_map().get(pid)
        if not lib_id or lib_id in {MEME_DEFAULT_LIBRARY_ID, MEME_MANAGER_NAMESPACE}:
            return MEME_MANAGER_NAMESPACE
        if lib_id not in self._libraries():
            return MEME_MANAGER_NAMESPACE
        return lib_id

    def _libraries(self) -> dict[str, Any]:
        if getattr(self.plugin, "store", None) is None:
            return {}
        value = self.plugin.store.config.get("meme_libraries", {})
        return value if isinstance(value, dict) else {}

    def _persona_library_map(self) -> dict[str, Any]:
        if getattr(self.plugin, "store", None) is None:
            return {}
        value = self.plugin.store.config.get("meme_persona_library_map", {})
        return value if isinstance(value, dict) else {}

    def _config(self) -> dict[str, Any]:
        if getattr(self.plugin, "store", None) is None:
            return default_meme_isolation()
        config = self.plugin.store.config.get("meme_manager_isolation", {})
        if isinstance(config, dict):
            merged = {**default_meme_isolation(), **config}
            merged["enabled"] = True
            return merged
        return default_meme_isolation()

    def _find_target(self) -> Any | None:
        self.target_metadata = None
        loaded = []
        getter = getattr(self.plugin.context, "get_all_stars", None)
        if callable(getter):
            try:
                stars = getter() or []
                loaded.extend(stars.values() if isinstance(stars, dict) else stars)
            except Exception as exc:
                log.warning("[用户策略] 读取 meme_manager 实例失败：%s", exc)
        loaded.extend(self.fallback_plugins.values())

        for item in loaded:
            metadata = item
            instance = self._instance(item)
            name = str(self._value(metadata, "name", "") or "").strip()
            if not name and instance is not None:
                name = str(self._value(instance, "name", "") or "").strip()
            if name not in MEME_PLUGIN_NAMES:
                continue
            self.target_metadata = metadata
            return instance or item
        return None

    def _is_activated(self, target: Any) -> bool:
        metadata = self.target_metadata or self._metadata(target)
        return bool(self._value(metadata, "activated", True))

    def _is_compatible(self, target: Any) -> tuple[bool, str]:
        required = ("category_manager", "_reload_personas")
        missing = [name for name in required if not hasattr(target, name)]
        module = inspect.getmodule(target.__class__)
        if module is None:
            missing.append("模块对象")
        if missing:
            return False, "meme_manager 结构不匹配，缺少：" + "、".join(missing)
        if not self._read_default_paths(target):
            return False, "meme_manager 未暴露 MEMES_DIR/MEMES_DATA_PATH，无法安全切库。"
        return True, ""

    def _remember_default_paths(self, target: Any) -> None:
        paths = self._read_default_paths(target)
        if paths:
            self._default_paths = paths

    def _has_default_paths(self) -> bool:
        return (
            self._default_paths.get("MEMES_DIR") is not None
            and self._default_paths.get("MEMES_DATA_PATH") is not None
        )

    def _read_default_paths(self, target: Any) -> dict[str, Path] | None:
        for source in self._path_sources(target):
            memes_dir = getattr(source, "MEMES_DIR", None)
            data_path = getattr(source, "MEMES_DATA_PATH", None)
            if memes_dir is None or data_path is None:
                continue
            try:
                return {
                    "MEMES_DIR": Path(memes_dir),
                    "MEMES_DATA_PATH": Path(data_path),
                }
            except TypeError:
                continue
        return None

    def _path_sources(self, target: Any) -> list[Any]:
        candidates = [target]
        module = inspect.getmodule(target.__class__)
        if module is not None:
            candidates.append(module)
            package_names = [
                str(getattr(module, "__package__", "") or "").strip(),
                str(getattr(target.__class__, "__module__", "") or "")
                .rpartition(".")[0]
                .strip(),
            ]
            for package in dict.fromkeys(name for name in package_names if name):
                for suffix in (
                    "config",
                    "utils",
                    "init",
                    "backend.models",
                    "backend.category_manager",
                ):
                    imported = self._module(f"{package}.{suffix}")
                    if imported is None and suffix == "config":
                        try:
                            imported = importlib.import_module(f"{package}.{suffix}")
                        except Exception:
                            imported = None
                    if imported is not None:
                        candidates.append(imported)
        for name in ("config", "settings"):
            value = getattr(target, name, None)
            if value is not None:
                candidates.append(value)

        result = []
        seen = set()
        for item in candidates:
            if item is None or id(item) in seen:
                continue
            seen.add(id(item))
            result.append(item)
        return result

    def _wrap_methods(self, target: Any) -> None:
        for name in RUNTIME_METHODS:
            current = getattr(target, name, None)
            if not callable(current):
                continue
            original = self._unwrap(current)
            self._originals[name] = original
            wrapped = self._wrap_method(original)
            self._mark_wrapper(wrapped, original)
            setattr(target, name, wrapped)
        setattr(target, "_user_policy_meme_adapter", self)

    def _wrap_registry_handlers(self, target: Any) -> None:
        for handler in self._registry_handlers(target):
            name = str(getattr(handler, "handler_name", "") or "")
            full_name = str(getattr(handler, "handler_full_name", "") or "")
            current = getattr(handler, "handler", None)
            if not self._is_runtime_handler(name, full_name) or not callable(current):
                continue
            key = str(getattr(handler, "handler_full_name", "") or id(handler))
            original = self._unwrap(current)
            self._handler_originals[key] = original
            wrapped = self._wrap_method(original)
            self._mark_wrapper(wrapped, original)
            handler.handler = wrapped

    def _restore_deprecated_wrappers(self, target: Any) -> None:
        """清理由旧版本误包裹的发送/装饰钩子。"""

        for name in DEPRECATED_RUNTIME_METHODS:
            current = getattr(target, name, None)
            if not callable(current):
                continue
            original = self._unwrap(current)
            if original is not current:
                try:
                    setattr(target, name, original)
                except Exception as exc:
                    log.warning(
                        "[用户策略] 清理 meme_manager 旧方法包裹 %s 失败：%s",
                        name,
                        exc,
                    )

        for handler in self._registry_handlers(target):
            name = str(getattr(handler, "handler_name", "") or "")
            full_name = str(getattr(handler, "handler_full_name", "") or "")
            current = getattr(handler, "handler", None)
            if (
                not self._is_deprecated_runtime_handler(name, full_name)
                or not callable(current)
            ):
                continue
            original = self._unwrap(current)
            if original is current:
                continue
            try:
                handler.handler = original
            except Exception as exc:
                key = str(getattr(handler, "handler_full_name", "") or id(handler))
                log.warning(
                    "[用户策略] 清理 meme_manager 旧处理器包裹 %s 失败：%s",
                    key,
                    exc,
                )

    @staticmethod
    def _registry_handlers(target: Any) -> list[Any]:
        module_path = str(getattr(target.__class__, "__module__", "") or "")
        if not module_path:
            return []
        try:
            from astrbot.core.star.star_handler import star_handlers_registry
        except Exception:
            return []

        getter = getattr(star_handlers_registry, "get_handlers_by_module_name", None)
        if callable(getter):
            return list(getter(module_path) or [])
        return [
            handler
            for handler in star_handlers_registry
            if getattr(handler, "handler_module_path", "") == module_path
        ]

    def _wrap_method(self, method: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.isasyncgenfunction(method):

            @wraps(method)
            async def asyncgen_wrapper(*args, **kwargs):
                if not self._config().get("enabled", False):
                    async for item in method(*args, **kwargs):
                        yield item
                    return
                event = self._find_event(args, kwargs)
                namespace = self._namespace_for_call(event)
                items = []
                if self._library_depth.get() > 0:
                    with self._using_library(namespace):
                        async for item in method(*args, **kwargs):
                            items.append(item)
                else:
                    async with self._lock:
                        with self._using_library(namespace):
                            async for item in method(*args, **kwargs):
                                items.append(item)
                for item in items:
                    yield item

            return asyncgen_wrapper

        if inspect.isgeneratorfunction(method):

            @wraps(method)
            def generator_wrapper(*args, **kwargs):
                if not self._config().get("enabled", False):
                    yield from method(*args, **kwargs)
                    return
                event = self._find_event(args, kwargs)
                namespace = self._namespace_for_call(event)
                with self._using_library(namespace):
                    items = list(method(*args, **kwargs))
                yield from items

            return generator_wrapper

        if inspect.iscoroutinefunction(method):

            @wraps(method)
            async def coroutine_wrapper(*args, **kwargs):
                if not self._config().get("enabled", False):
                    return await method(*args, **kwargs)
                event = self._find_event(args, kwargs)
                namespace = self._namespace_for_call(event)
                if self._library_depth.get() > 0:
                    with self._using_library(namespace):
                        return await method(*args, **kwargs)
                async with self._lock:
                    with self._using_library(namespace):
                        return await method(*args, **kwargs)

            return coroutine_wrapper

        @wraps(method)
        def sync_wrapper(*args, **kwargs):
            if not self._config().get("enabled", False):
                return method(*args, **kwargs)
            event = self._find_event(args, kwargs)
            namespace = self._namespace_for_call(event)
            with self._using_library(namespace):
                return method(*args, **kwargs)

        return sync_wrapper

    def _namespace_for_call(self, event: Any | None) -> str:
        if event is None:
            return MEME_MANAGER_NAMESPACE
        return self.namespace_for_event(event)

    @contextmanager
    def _using_library(self, namespace: str):
        target = self.target
        if target is None:
            yield
            return

        module = inspect.getmodule(target.__class__)
        if module is None:
            yield
            return

        token = self._active_namespace.set(namespace or "default")
        depth_token = self._library_depth.set(self._library_depth.get() + 1)
        paths = self.library_paths(namespace)
        if not paths.get("readonly"):
            self._ensure_library(paths)
        touched = self._switch_module_paths(module, paths)
        category_manager = getattr(target, "category_manager", None)
        original_descriptions = getattr(category_manager, "descriptions", None)
        original_sync = self._switch_img_sync(target, paths["memes_dir"])
        try:
            if category_manager is not None and hasattr(
                category_manager,
                "reload_descriptions",
            ):
                category_manager.reload_descriptions()
            reload_personas = getattr(target, "_reload_personas", None)
            if callable(reload_personas):
                reload_personas()
            yield
        finally:
            self._restore_img_sync(original_sync)
            self._restore_module_paths(touched)
            if category_manager is not None:
                try:
                    if original_descriptions is not None:
                        category_manager.descriptions = original_descriptions
                    elif hasattr(category_manager, "reload_descriptions"):
                        category_manager.reload_descriptions()
                except Exception:
                    pass
            try:
                reload_personas = getattr(target, "_reload_personas", None)
                if callable(reload_personas):
                    reload_personas()
            except Exception:
                pass
            self._library_depth.reset(depth_token)
            self._active_namespace.reset(token)

    def _ensure_library(self, paths: dict[str, Path]) -> None:
        paths["memes_dir"].mkdir(parents=True, exist_ok=True)
        if paths["data_path"].exists():
            return
        if not self._config().get("copy_default_descriptions", True):
            paths["data_path"].write_text("{}", encoding="utf-8")
            return
        source = self._default_paths.get("MEMES_DATA_PATH")
        if source and source.is_file():
            paths["data_path"].write_text(
                source.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            return
        paths["data_path"].write_text("{}", encoding="utf-8")

    def _switch_module_paths(
        self,
        main_module: Any,
        paths: dict[str, Path],
    ) -> list[tuple[Any, str, Any]]:
        replacements = {
            "MEMES_DIR": paths["memes_dir"],
            "MEMES_DATA_PATH": paths["data_path"],
            "DEFAULT_MEMES_INIT_MARKER": paths["init_marker"],
        }
        # 只读(Meme Manager)库没有 init_marker;None 表示「保持不变」,
        # 不要把 meme_manager 原模块的标记清空。
        replacements = {
            name: value for name, value in replacements.items() if value is not None
        }
        modules = [main_module]
        package = str(getattr(main_module, "__package__", "") or "")
        for suffix in (
            "config",
            "utils",
            "init",
            "backend.models",
            "backend.category_manager",
        ):
            imported = self._module(f"{package}.{suffix}")
            if imported is not None:
                modules.append(imported)

        touched = []
        seen = set()
        for module in modules:
            if id(module) in seen:
                continue
            seen.add(id(module))
            for name, value in replacements.items():
                if hasattr(module, name):
                    touched.append((module, name, getattr(module, name)))
                    setattr(module, name, value)
        return touched

    @staticmethod
    def _restore_module_paths(touched: list[tuple[Any, str, Any]]) -> None:
        for module, name, value in reversed(touched):
            setattr(module, name, value)

    @staticmethod
    def _switch_img_sync(target: Any, memes_dir: Path) -> dict[str, Any] | None:
        img_sync = getattr(target, "img_sync", None)
        if img_sync is None:
            return None
        original = {"sync": img_sync, "local_dir": getattr(img_sync, "local_dir", None)}
        try:
            img_sync.local_dir = Path(memes_dir)
            if hasattr(img_sync, "sync_manager"):
                original["sync_manager_local_dir"] = getattr(
                    img_sync.sync_manager,
                    "local_dir",
                    None,
                )
                img_sync.sync_manager.local_dir = Path(memes_dir)
        except Exception:
            pass
        return original

    @staticmethod
    def _restore_img_sync(original: dict[str, Any] | None) -> None:
        if not original:
            return
        img_sync = original["sync"]
        try:
            img_sync.local_dir = original.get("local_dir")
            if hasattr(img_sync, "sync_manager") and "sync_manager_local_dir" in original:
                img_sync.sync_manager.local_dir = original.get("sync_manager_local_dir")
        except Exception:
            pass

    @staticmethod
    def _find_event(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any | None:
        for value in [*args, *kwargs.values()]:
            if hasattr(value, "get_sender_id") and hasattr(value, "get_extra"):
                return value
            if hasattr(value, "unified_msg_origin") and hasattr(value, "message_obj"):
                return value
        return None

    @staticmethod
    def _unwrap(method: Callable[..., Any]) -> Callable[..., Any]:
        original = getattr(method, ORIGINAL_ATTR, None)
        return original if callable(original) else method

    def _mark_wrapper(
        self,
        wrapped: Callable[..., Any],
        original: Callable[..., Any],
    ) -> None:
        setattr(wrapped, WRAPPED_MARKER, True)
        setattr(wrapped, OWNER_ATTR, self)
        setattr(wrapped, ORIGINAL_ATTR, original)

    @staticmethod
    def _is_runtime_handler(name: str, full_name: str = "") -> bool:
        return MemeManagerPersonaAdapter._handler_matches_methods(
            name,
            full_name,
            RUNTIME_METHODS,
        )

    @staticmethod
    def _is_deprecated_runtime_handler(name: str, full_name: str = "") -> bool:
        return MemeManagerPersonaAdapter._handler_matches_methods(
            name,
            full_name,
            DEPRECATED_RUNTIME_METHODS,
        )

    @staticmethod
    def _handler_matches_methods(
        name: str,
        full_name: str,
        methods: tuple[str, ...],
    ) -> bool:
        values = [str(name or ""), str(full_name or "")]
        for method in methods:
            for value in values:
                if (
                    value == method
                    or value.endswith(f".{method}")
                    or value.endswith(f"_{method}")
                    or value.endswith(f":{method}")
                ):
                    return True
        return False

    @staticmethod
    def _persona_from_extra(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("persona_id", "") or "").strip()
        return str(value or "").strip()

    @staticmethod
    def _module(name: str) -> Any | None:
        try:
            import sys

            return sys.modules.get(name)
        except Exception:
            return None

    @classmethod
    def _metadata(cls, item: Any) -> Any:
        for name in ("metadata", "star_metadata", "star"):
            value = getattr(item, name, None)
            if value is not None:
                return value
        return item

    @staticmethod
    def _instance(item: Any) -> Any | None:
        for name in ("star_cls", "instance", "star", "plugin", "obj"):
            value = getattr(item, name, None)
            if value is not None and not isinstance(value, type):
                return value
        return item if hasattr(item, "_reload_personas") else None

    @staticmethod
    def _value(source: Any, name: str, default: Any = None) -> Any:
        if isinstance(source, dict):
            return source.get(name, default)
        return getattr(source, name, default)
