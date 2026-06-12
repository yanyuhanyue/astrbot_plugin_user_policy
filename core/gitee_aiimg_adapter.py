"""Gitee AI Image 的人格生图效果适配层。"""

from __future__ import annotations

import inspect
import logging
from functools import wraps
from typing import Any, Callable

from .policy_store import default_gitee_aiimg_effects


log = logging.getLogger(__name__)

GITEE_AIIMG_PLUGIN_NAMES = {"astrbot_plugin_gitee_aiimg", "gitee_aiimg"}
EFFECTIVE_PERSONA_EVENT_KEY = "user_policy_effective_persona_id"
PERSONA_RESULT_EVENT_KEY = "user_policy_persona_result"
PROMPT_ARGUMENTS = {"prompt", "text", "query", "content", "description"}
RUNTIME_METHOD_HINTS = (
    "aiimg",
    "txt2img",
    "text2img",
    "generate",
    "draw",
    "image",
    "edit",
    "selfie",
    "video",
    "tool",
)


class GiteeAiimgPersonaAdapter:
    """在不修改 gitee_aiimg 源码的前提下追加人格生图效果。"""

    def __init__(
        self,
        plugin: Any,
        fallback_plugins: dict[str, Any] | None = None,
    ):
        self.plugin = plugin
        self.fallback_plugins = fallback_plugins or {}
        self.enabled = False
        self.installed = False
        self.activated = False
        self.applied = False
        self.message = "Gitee AI Image 人格效果尚未启用。"
        self.target: Any | None = None
        self.target_metadata: Any | None = None
        self._originals: dict[str, Callable[..., Any]] = {}
        self._handler_originals: dict[str, Callable[..., Any]] = {}

    def configure(self) -> None:
        config = self._config()
        self.enabled = bool(config.get("enabled", False))
        if not self.enabled:
            self.applied = False
            self.message = "Gitee AI Image 人格效果未启用。"
            return

        target = self._find_target()
        if target is None:
            self.installed = False
            self.activated = False
            self.applied = False
            self.message = "未检测到已加载的 Gitee AI Image。"
            return

        self.installed = True
        self.activated = self._is_activated(target)
        if not self.activated:
            self.applied = False
            self.message = "Gitee AI Image 当前未启用。"
            return

        wrapped = self._wrap_methods(target)
        wrapped += self._wrap_registry_handlers(target)
        self.target = target
        self.applied = wrapped > 0 or bool(self._originals or self._handler_originals)
        self.message = (
            "已启用按用户策略人格追加生图效果。"
            if self.applied
            else "未找到可安全包裹的 Gitee AI Image 生图入口。"
        )

    def report(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "installed": self.installed,
            "activated": self.activated,
            "applied": self.applied,
            "message": self.message,
            "effects_count": len(self._config().get("effects", {})),
            "wrapped_method_count": len(self._originals),
            "wrapped_handler_count": len(self._handler_originals),
            "wrapped_methods": sorted(self._originals),
        }

    def effect_for_event(self, event: Any) -> str:
        persona_id = self._persona_id_for_event(event)
        if not persona_id:
            return ""
        return str(self._config().get("effects", {}).get(persona_id, "") or "").strip()

    def _persona_id_for_event(self, event: Any) -> str:
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
        return persona_id

    def _config(self) -> dict[str, Any]:
        if getattr(self.plugin, "store", None) is None:
            return default_gitee_aiimg_effects()
        config = self.plugin.store.config.get("gitee_aiimg_persona_effects", {})
        if isinstance(config, dict):
            return {**default_gitee_aiimg_effects(), **config}
        return default_gitee_aiimg_effects()

    def _find_target(self) -> Any | None:
        loaded = []
        getter = getattr(self.plugin.context, "get_all_stars", None)
        if callable(getter):
            try:
                stars = getter() or []
                loaded.extend(stars.values() if isinstance(stars, dict) else stars)
            except Exception as exc:
                log.warning("[用户策略] 读取 Gitee AI Image 实例失败：%s", exc)
        loaded.extend(self.fallback_plugins.values())

        for item in loaded:
            metadata = item
            instance = self._instance(item)
            name = str(self._value(metadata, "name", "") or "").strip()
            if not name and instance is not None:
                name = str(self._value(instance, "name", "") or "").strip()
            if name not in GITEE_AIIMG_PLUGIN_NAMES:
                continue
            self.target_metadata = metadata
            return instance or item
        return None

    def _is_activated(self, target: Any) -> bool:
        metadata = self.target_metadata or self._metadata(target)
        return bool(self._value(metadata, "activated", True))

    def _wrap_methods(self, target: Any) -> int:
        if getattr(target, "_user_policy_gitee_aiimg_adapter", None) is self:
            return 0
        if getattr(target, "_user_policy_gitee_aiimg_adapter", None) is not None:
            self.message = "Gitee AI Image 已存在其他用户策略适配器，跳过重复包裹。"
            return 0

        count = 0
        for name in dir(target):
            if name.startswith("_") or not self._looks_like_runtime_method(name):
                continue
            method = getattr(target, name, None)
            if not callable(method):
                continue
            self._originals[name] = method
            wrapped = self._wrap_method(method)
            setattr(wrapped, "_user_policy_gitee_aiimg_wrapped", True)
            setattr(wrapped, "_user_policy_gitee_aiimg_original", method)
            setattr(target, name, wrapped)
            count += 1
        if count:
            setattr(target, "_user_policy_gitee_aiimg_adapter", self)
        return count

    def _wrap_registry_handlers(self, target: Any) -> int:
        module_path = str(getattr(target.__class__, "__module__", "") or "")
        if not module_path:
            return 0
        try:
            from astrbot.core.star.star_handler import star_handlers_registry
        except Exception:
            return 0

        getter = getattr(star_handlers_registry, "get_handlers_by_module_name", None)
        if callable(getter):
            handlers = list(getter(module_path) or [])
        else:
            handlers = [
                handler
                for handler in star_handlers_registry
                if getattr(handler, "handler_module_path", "") == module_path
            ]

        count = 0
        for handler in handlers:
            original = getattr(handler, "handler", None)
            if not callable(original):
                continue
            key = str(getattr(handler, "handler_full_name", "") or id(handler))
            if key in self._handler_originals:
                continue
            name = str(getattr(handler, "handler_name", "") or "")
            if name and not self._looks_like_runtime_method(name):
                continue
            wrapped = self._wrap_method(original)
            setattr(wrapped, "_user_policy_gitee_aiimg_wrapped", True)
            setattr(wrapped, "_user_policy_gitee_aiimg_original", original)
            self._handler_originals[key] = original
            handler.handler = wrapped
            count += 1
        return count

    def restore(self) -> None:
        target = self.target
        if target is not None:
            for name, original in self._originals.items():
                try:
                    current = getattr(target, name, None)
                    if getattr(current, "_user_policy_gitee_aiimg_wrapped", False):
                        setattr(target, name, original)
                except Exception:
                    pass
            for handler in self._registry_handlers(target):
                key = str(getattr(handler, "handler_full_name", "") or id(handler))
                original = self._handler_originals.get(key)
                if original is None:
                    continue
                try:
                    current = getattr(handler, "handler", None)
                    if getattr(current, "_user_policy_gitee_aiimg_wrapped", False):
                        handler.handler = original
                except Exception:
                    pass
            if getattr(target, "_user_policy_gitee_aiimg_adapter", None) is self:
                try:
                    delattr(target, "_user_policy_gitee_aiimg_adapter")
                except Exception:
                    pass
        self._originals.clear()
        self._handler_originals.clear()
        self.target = None
        self.applied = False

    def _wrap_method(self, method: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.isasyncgenfunction(method):

            @wraps(method)
            async def asyncgen_wrapper(*args, **kwargs):
                call = self._decorate_call(args, kwargs)
                try:
                    async for item in method(*call.args, **call.kwargs):
                        yield item
                finally:
                    call.restore()

            return asyncgen_wrapper

        if inspect.isgeneratorfunction(method):

            @wraps(method)
            def generator_wrapper(*args, **kwargs):
                call = self._decorate_call(args, kwargs)
                try:
                    yield from method(*call.args, **call.kwargs)
                finally:
                    call.restore()

            return generator_wrapper

        if inspect.iscoroutinefunction(method):

            @wraps(method)
            async def coroutine_wrapper(*args, **kwargs):
                call = self._decorate_call(args, kwargs)
                try:
                    return await method(*call.args, **call.kwargs)
                finally:
                    call.restore()

            return coroutine_wrapper

        @wraps(method)
        def sync_wrapper(*args, **kwargs):
            call = self._decorate_call(args, kwargs)
            try:
                return method(*call.args, **call.kwargs)
            finally:
                call.restore()

        return sync_wrapper

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

    def _decorate_call(self, args: tuple[Any, ...], kwargs: dict[str, Any]):
        if not self._config().get("enabled", False):
            return _DecoratedCall(args, kwargs, lambda: None)
        event = self._find_event(args, kwargs)
        if event is None:
            return self._decorate_prompt_kwargs(args, kwargs, "")
        effect = self.effect_for_event(event)
        if not effect:
            return _DecoratedCall(args, kwargs, lambda: None)
        call = self._decorate_prompt_kwargs(args, kwargs, effect)
        if call.changed:
            return call
        return self._decorate_event_message(args, kwargs, event, effect)

    def _decorate_prompt_kwargs(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        effect: str,
    ):
        if not effect:
            return _DecoratedCall(args, kwargs, lambda: None)
        updated = dict(kwargs)
        changed = False
        for name in PROMPT_ARGUMENTS:
            value = updated.get(name)
            if isinstance(value, str) and value.strip():
                updated[name] = self._append_effect(value, effect)
                changed = True
        return _DecoratedCall(args, updated, lambda: None, changed)

    def _decorate_event_message(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        event: Any,
        effect: str,
    ):
        originals: list[tuple[Any, str, Any]] = []
        for owner, name in self._event_message_slots(event):
            value = getattr(owner, name, None)
            if not isinstance(value, str) or not value.strip():
                continue
            originals.append((owner, name, value))
            setattr(owner, name, self._append_effect(value, effect))
        method_restore = self._override_event_message_getter(event, effect)

        def restore() -> None:
            for owner, name, value in originals:
                try:
                    setattr(owner, name, value)
                except Exception:
                    pass
            method_restore()

        return _DecoratedCall(args, kwargs, restore, bool(originals) or method_restore.changed)

    def _override_event_message_getter(self, event: Any, effect: str):
        getter = getattr(event, "get_message_str", None)
        if not callable(getter):
            return _RestoreAction(lambda: None, False)
        try:
            original_message = str(getter() or "")
        except Exception:
            return _RestoreAction(lambda: None, False)
        if not original_message.strip():
            return _RestoreAction(lambda: None, False)
        decorated_message = self._append_effect(original_message, effect)
        if decorated_message == original_message:
            return _RestoreAction(lambda: None, False)
        own_attrs = getattr(event, "__dict__", {})
        had_own_attr = isinstance(own_attrs, dict) and "get_message_str" in own_attrs

        def decorated_get_message_str(*_args, **_kwargs):
            return decorated_message

        try:
            setattr(event, "get_message_str", decorated_get_message_str)
        except Exception:
            return _RestoreAction(lambda: None, False)

        def restore() -> None:
            try:
                if had_own_attr:
                    setattr(event, "get_message_str", getter)
                else:
                    delattr(event, "get_message_str")
            except Exception:
                try:
                    setattr(event, "get_message_str", getter)
                except Exception:
                    pass

        return _RestoreAction(restore, True)

    @staticmethod
    def _persona_from_extra(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("persona_id", "") or "").strip()
        return str(value or "").strip()

    @staticmethod
    def _event_message_slots(event: Any) -> list[tuple[Any, str]]:
        slots = []
        for name in ("message_str", "message", "text", "content"):
            if hasattr(event, name):
                slots.append((event, name))
        message_obj = getattr(event, "message_obj", None)
        if message_obj is not None:
            for name in ("message_str", "message", "text", "content"):
                if hasattr(message_obj, name):
                    slots.append((message_obj, name))
        return slots

    @staticmethod
    def _append_effect(prompt: str, effect: str) -> str:
        marker = "人格生图效果"
        if marker in prompt:
            return prompt
        return f"{prompt}\n\n{marker}：{effect}"

    @staticmethod
    def _find_event(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any | None:
        for value in [*args, *kwargs.values()]:
            if hasattr(value, "get_sender_id") and hasattr(value, "get_extra"):
                return value
            if hasattr(value, "unified_msg_origin") and hasattr(value, "message_obj"):
                return value
        return None

    @staticmethod
    def _looks_like_runtime_method(name: str) -> bool:
        lowered = name.casefold()
        return any(hint in lowered for hint in RUNTIME_METHOD_HINTS)

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
        return item

    @staticmethod
    def _value(source: Any, name: str, default: Any = None) -> Any:
        if isinstance(source, dict):
            return source.get(name, default)
        return getattr(source, name, default)


class _DecoratedCall:
    def __init__(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        restore: Callable[[], None],
        changed: bool = False,
    ):
        self.args = args
        self.kwargs = kwargs
        self.restore = restore
        self.changed = changed


class _RestoreAction:
    def __init__(self, callback: Callable[[], None], changed: bool):
        self.callback = callback
        self.changed = changed

    def __call__(self) -> None:
        self.callback()
