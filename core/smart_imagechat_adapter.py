"""Smart ImageChat Hub 的人格图库运行时适配层。"""

from __future__ import annotations

import contextvars
import inspect
import logging
import os
import shutil
from functools import wraps
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

from .policy_store import (
    SMART_IMAGE_TAG_MAX,
    SMART_IMAGE_TAGS_PER_IMAGE_MAX,
    SMART_IMAGE_SMART_NAMESPACE,
    default_smart_image_isolation,
)


log = logging.getLogger(__name__)

SMART_PLUGIN_NAMES = {"astrbot_plugin_smart_imagechat_hub"}
WRAPPED_MARKER = "_user_policy_smart_image_wrapped"
OWNER_ATTR = "_user_policy_smart_image_owner"
ORIGINAL_ATTR = "_user_policy_smart_image_original"
ADAPTER_ATTR = "_user_policy_smart_image_adapter"


class SmartImageChatPersonaAdapter:
    """按当前人格替换 Smart ImageChat Hub 的发图候选。"""

    def __init__(
        self,
        plugin: Any,
        data_dir: Path,
        fallback_plugins: dict[str, Any] | None = None,
    ):
        self.plugin = plugin
        self.data_dir = Path(data_dir)
        self.fallback_plugins = fallback_plugins or {}
        self.root_dir = self.data_dir / "smart_image_libraries"
        self.pool_dir = self.root_dir / "pool"
        self.backup_dir = self.root_dir / "backups"
        self.target: Any | None = None
        self.target_metadata: Any | None = None
        self.enabled = False
        self.installed = False
        self.activated = False
        self.applied = False
        self.message = "智能图片人格图库隔离尚未启用。"
        self._original: Callable[..., Any] | None = None
        self._active_library: contextvars.ContextVar[str | None] = (
            contextvars.ContextVar(
                "user_policy_smart_image_library",
                default=None,
            )
        )
        self._depth: contextvars.ContextVar[int] = contextvars.ContextVar(
            "user_policy_smart_image_depth",
            default=0,
        )

    def configure(self) -> None:
        self.enabled = bool(self._config().get("enabled", False))
        target = self._find_target()
        if target is None:
            self.restore()
            self.installed = False
            self.activated = False
            self.message = "未检测到已加载的 Smart ImageChat Hub。"
            return

        self.installed = True
        self.activated = self._is_activated(target)
        if not self.activated:
            self.restore()
            self.message = "Smart ImageChat Hub 当前未启用。"
            return
        if not self.enabled:
            self.restore()
            self.target = target
            self.message = "智能图片人格图库隔离未启用。"
            return

        compatible, reason = self._is_compatible(target)
        if not compatible:
            self.restore()
            self.target = target
            self.message = reason
            return

        if self.target is not None and self.target is not target:
            self.restore()
        self.target = target
        current = getattr(target, "_library_candidates")
        owner = getattr(current, OWNER_ATTR, None)
        if owner is self:
            self.applied = True
            return
        if owner is not None:
            self.applied = False
            self.message = "Smart ImageChat Hub 已由其他适配器接管，未重复包裹。"
            return

        original = self._unwrap(current)
        wrapped = self._wrap_candidates(original)
        setattr(wrapped, WRAPPED_MARKER, True)
        setattr(wrapped, OWNER_ATTR, self)
        setattr(wrapped, ORIGINAL_ATTR, original)
        setattr(target, "_library_candidates", wrapped)
        setattr(target, ADAPTER_ATTR, self)
        self._original = original
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.pool_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_orphan_mirrors()
        self.applied = True
        self.message = (
            "已启用 Smart ImageChat Hub 人格图库隔离；"
            "未映射人格继续使用原插件全局图库。"
        )

    def restore(self) -> None:
        target = self.target
        if target is not None:
            current = getattr(target, "_library_candidates", None)
            if getattr(current, OWNER_ATTR, None) is self and self._original:
                try:
                    setattr(target, "_library_candidates", self._original)
                except Exception as exc:
                    log.warning(
                        "[用户策略] 恢复 Smart ImageChat Hub 候选入口失败：%s",
                        exc,
                    )
            if getattr(target, ADAPTER_ATTR, None) is self:
                try:
                    delattr(target, ADAPTER_ATTR)
                except Exception:
                    pass
        self._clear_mirror_root(target)
        self._original = None
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
            "pool_dir": str(self.pool_dir),
        }

    def smart_global_tags(self) -> list[str]:
        """只读 Smart ImageChat Hub 原插件的公用特征标签。"""

        target = self.target or self._find_target()
        if target is None:
            return []
        getter = getattr(target, "_global_tags", None)
        if callable(getter):
            try:
                return self._normalize_tags(getter())
            except Exception as exc:
                log.warning("[用户策略] 读取 Smart 公用标签失败：%s", exc)
        config_getter = getattr(target, "_config_get", None)
        if callable(config_getter):
            try:
                return self._normalize_tags(
                    config_getter("library_builder.global_tags", [])
                )
            except Exception as exc:
                log.warning("[用户策略] 读取 Smart 配置公用标签失败：%s", exc)
        config = self._value(target, "config", None)
        return self._normalize_tags(
            self._nested_value(config, "library_builder.global_tags", [])
        )

    def bind_event_persona(self, event: Any, decision: Any) -> None:
        persona_id = str(getattr(decision, "persona_id", "") or "").strip()
        if not persona_id and getattr(decision, "persona_mode", "") == "auto":
            manager = getattr(self.plugin, "auto_persona", None)
            recorder = getattr(manager, "record_for_decision", None)
            if callable(recorder):
                try:
                    record = recorder(decision)
                    persona_id = str(
                        (record or {}).get("persona_id", "") or ""
                    ).strip()
                except Exception:
                    persona_id = ""
        namespace = self.resolve_namespace_for_persona(persona_id)
        self._active_library.set(namespace)

    def resolve_namespace_for_persona(self, persona_id: str) -> str:
        pid = str(persona_id or "").strip()
        if not pid:
            return SMART_IMAGE_SMART_NAMESPACE
        library_id = self._persona_library_map().get(pid)
        if not library_id or library_id not in self._libraries():
            return SMART_IMAGE_SMART_NAMESPACE
        return str(library_id)

    def _wrap_candidates(
        self,
        original: Callable[..., Any],
    ) -> Callable[..., Any]:
        @wraps(original)
        def wrapper(*args, **kwargs):
            base = original(*args, **kwargs)
            if self._depth.get() > 0:
                return base
            library_id = self._active_library.get()
            if (
                not self._config().get("enabled", False)
                or not library_id
                or library_id == SMART_IMAGE_SMART_NAMESPACE
            ):
                return base
            token = self._depth.set(self._depth.get() + 1)
            try:
                candidates = self._build_library_candidates(library_id)
                return candidates or base
            except Exception as exc:
                log.warning(
                    "[用户策略] 生成 Smart ImageChat Hub 人格候选失败，"
                    "已回退原图库：%s",
                    exc,
                )
                return base
            finally:
                self._depth.reset(token)

        return wrapper

    def _build_library_candidates(
        self,
        library_id: str,
    ) -> list[dict[str, Any]]:
        target = self.target
        library = self._libraries().get(str(library_id), {})
        images = library.get("images", {}) if isinstance(library, dict) else {}
        if target is None or not isinstance(images, dict):
            return []
        namespace = self.namespace_for_library(library_id)
        candidates = []
        for digest, item in images.items():
            if not isinstance(item, dict):
                continue
            extension = str(item.get("ext", "") or "").strip().lower()
            source = self.pool_dir / f"{digest}.{extension}"
            if not source.is_file():
                continue
            rel_path = (
                f"files/user_policy_persona/{namespace}/"
                f"{digest}.{extension}"
            )
            mirror = target._abs_plugin_data_path(rel_path)
            self._ensure_mirror_file(source, mirror)
            tags = list(item.get("tags", []) or [])
            candidates.append(
                {
                    "id": target._image_id(rel_path),
                    "filename": str(
                        item.get("filename") or source.name
                    ),
                    "rel_path": rel_path,
                    "tags": tags,
                    "auto_tags": tags,
                    "manual_tags": tags,
                    "selected_global_tags": [],
                }
            )
        return candidates

    @staticmethod
    def namespace_for_library(library_id: str) -> str:
        digest = sha256(
            str(library_id or "").encode("utf-8")
        ).hexdigest()[:16]
        return f"library_{digest}"

    @staticmethod
    def _ensure_mirror_file(source: Path, mirror: Path) -> None:
        if mirror.is_file():
            try:
                # Pool files are immutable and content-addressed. Matching sizes
                # therefore mean an existing copy/hardlink is already current.
                if mirror.stat().st_size == source.stat().st_size:
                    return
            except OSError:
                pass
        mirror.parent.mkdir(parents=True, exist_ok=True)
        temporary = mirror.with_suffix(mirror.suffix + ".tmp")
        if temporary.exists():
            temporary.unlink()
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copy2(source, temporary)
        os.replace(temporary, mirror)

    def _cleanup_orphan_mirrors(self) -> None:
        target = self.target
        if target is None:
            return
        root = Path(target.data_dir) / "files" / "user_policy_persona"
        if not root.is_dir():
            return
        valid = {
            self.namespace_for_library(library_id)
            for library_id in self._libraries()
        }
        for child in root.iterdir():
            if child.is_dir() and child.name not in valid:
                shutil.rmtree(child, ignore_errors=True)

    def _clear_mirror_root(self, target: Any | None = None) -> None:
        target = target or self.target
        if target is None:
            return
        root = Path(target.data_dir) / "files" / "user_policy_persona"
        if root.is_dir():
            shutil.rmtree(root, ignore_errors=True)

    def _config(self) -> dict[str, Any]:
        store = getattr(self.plugin, "store", None)
        if store is None:
            return default_smart_image_isolation()
        value = store.config.get("smart_image_isolation", {})
        if not isinstance(value, dict):
            return default_smart_image_isolation()
        return {**default_smart_image_isolation(), **value}

    def _libraries(self) -> dict[str, Any]:
        store = getattr(self.plugin, "store", None)
        value = (
            store.config.get("smart_image_libraries", {})
            if store is not None
            else {}
        )
        return value if isinstance(value, dict) else {}

    def _persona_library_map(self) -> dict[str, str]:
        store = getattr(self.plugin, "store", None)
        value = (
            store.config.get("smart_image_persona_library_map", {})
            if store is not None
            else {}
        )
        return value if isinstance(value, dict) else {}

    @classmethod
    def _normalize_tags(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            raw_values = (
                value.replace("，", ",")
                .replace("、", ",")
                .replace("；", ",")
                .replace(";", ",")
                .replace("\n", ",")
                .split(",")
            )
        elif isinstance(value, list):
            raw_values = []
            for item in value:
                raw_values.extend(cls._normalize_tags(item))
        else:
            return []
        tags = []
        for raw in raw_values:
            tag = str(raw or "").strip(" \t\r\n\"'`，。；;、")
            if not tag or tag in tags:
                continue
            tags.append(tag[:SMART_IMAGE_TAG_MAX])
            if len(tags) >= SMART_IMAGE_TAGS_PER_IMAGE_MAX:
                break
        return tags

    @classmethod
    def _nested_value(cls, source: Any, path: str, default: Any = None) -> Any:
        current = source
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part, default)
            else:
                current = getattr(current, part, default)
            if current is default:
                return default
        return current

    def _find_target(self) -> Any | None:
        self.target_metadata = None
        loaded = []
        getter = getattr(self.plugin.context, "get_all_stars", None)
        if callable(getter):
            try:
                stars = getter() or []
                loaded.extend(
                    stars.values() if isinstance(stars, dict) else stars
                )
            except Exception as exc:
                log.warning(
                    "[用户策略] 读取 Smart ImageChat Hub 实例失败：%s",
                    exc,
                )
        loaded.extend(self.fallback_plugins.values())
        for item in loaded:
            instance = self._instance(item)
            name = str(self._value(item, "name", "") or "").strip()
            if not name and instance is not None:
                name = str(self._value(instance, "name", "") or "").strip()
            if name not in SMART_PLUGIN_NAMES:
                continue
            self.target_metadata = item
            return instance or item
        return None

    def _is_activated(self, target: Any) -> bool:
        metadata = self.target_metadata or self._metadata(target)
        return bool(self._value(metadata, "activated", True))

    @staticmethod
    def _is_compatible(target: Any) -> tuple[bool, str]:
        required = (
            "_library_candidates",
            "_index",
            "data_dir",
            "_abs_plugin_data_path",
            "_image_id",
            "_norm_rel_path",
            "_collection_pool_snapshot",
            "_collection_pool_item_by_id",
            "_discard_pending_collection_images",
        )
        missing = [name for name in required if not hasattr(target, name)]
        method = getattr(target, "_library_candidates", None)
        if method is not None and inspect.iscoroutinefunction(method):
            missing.append("_library_candidates 同步接口")
        if missing:
            return (
                False,
                "Smart ImageChat Hub 当前版本暂不兼容，缺少："
                + "、".join(missing),
            )
        return True, ""

    @staticmethod
    def _unwrap(method: Callable[..., Any]) -> Callable[..., Any]:
        original = getattr(method, ORIGINAL_ATTR, None)
        return original if callable(original) else method

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
        return item if hasattr(item, "_library_candidates") else None

    @staticmethod
    def _value(source: Any, name: str, default: Any = None) -> Any:
        if isinstance(source, dict):
            return source.get(name, default)
        return getattr(source, name, default)
