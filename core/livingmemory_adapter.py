"""LivingMemory 的按用户策略人格隔离适配层。"""

from __future__ import annotations

import contextvars
import inspect
from functools import wraps
from typing import Any, Callable

from .runtime_helpers import find_plugin, wrap_registry_handlers


LIVINGMEMORY_NAMES = {"LivingMemory", "astrbot_plugin_livingmemory"}
RUNTIME_METHODS = {"handle_memory_recall", "handle_memory_reflection"}


class LivingMemoryPersonaAdapter:
    """仅为当前消息覆盖 LivingMemory 的 persona_id 过滤参数。"""

    def __init__(
        self,
        plugin: Any,
        fallback_plugins: dict[str, Any] | None = None,
    ):
        self.plugin = plugin
        self.fallback_plugins = fallback_plugins or {}
        self.target: Any | None = None
        self.installed = False
        self.activated = False
        self.applied = False
        self.message = "未检测到 LivingMemory。"
        self._active: contextvars.ContextVar[tuple[bool, str] | None] = (
            contextvars.ContextVar(
                "user_policy_livingmemory_scope",
                default=None,
            )
        )
        self._originals: dict[str, Callable[..., Any]] = {}
        self._handler_originals: dict[str, Callable[..., Any]] = {}
        self._engine_originals: dict[str, Callable[..., Any]] = {}
        self._engine_ready = False

    def configure(self) -> None:
        target, metadata = find_plugin(
            self.plugin.context,
            self.fallback_plugins,
            LIVINGMEMORY_NAMES,
        )
        if target is None:
            self.installed = False
            self.activated = False
            self.applied = False
            self.message = "未检测到 LivingMemory。"
            return
        self.installed = True
        self.activated = bool(
            getattr(metadata, "activated", True)
            if not isinstance(metadata, dict)
            else metadata.get("activated", True)
        )
        if not self.activated:
            self.applied = False
            self.message = "LivingMemory 当前未启用。"
            return
        if not all(callable(getattr(target, name, None)) for name in RUNTIME_METHODS):
            self.applied = False
            self.message = "当前 LivingMemory 版本暂不兼容。"
            return
        if self.target is not None and self.target is not target:
            self._originals.clear()
            self._handler_originals.clear()
            self._engine_originals.clear()
            self._engine_ready = False
        self.target = target
        for name in RUNTIME_METHODS:
            original = getattr(target, name)
            if name in self._originals or getattr(
                original,
                "_user_policy_livingmemory_wrapped",
                False,
            ):
                continue
            wrapped = self._wrap_entry(original)
            setattr(wrapped, "_user_policy_livingmemory_wrapped", True)
            setattr(wrapped, "_user_policy_livingmemory_original", original)
            self._originals[name] = original
            setattr(target, name, wrapped)
        wrap_registry_handlers(
            target,
            RUNTIME_METHODS,
            self._wrap_entry,
            "_user_policy_livingmemory_wrapped",
            self._handler_originals,
        )
        self.applied = True
        self.message = "已按用户策略人格隔离 LivingMemory 的召回与写入。"

    def restore(self) -> None:
        target = self.target
        if target is not None:
            for name, original in self._originals.items():
                try:
                    current = getattr(target, name, None)
                    if getattr(current, "_user_policy_livingmemory_wrapped", False):
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
                    if getattr(current, "_user_policy_livingmemory_wrapped", False):
                        handler.handler = original
                except Exception:
                    pass
            engine = self._memory_engine(target)
            if engine is not None:
                for name, original in self._engine_originals.items():
                    try:
                        current = getattr(engine, name, None)
                        if getattr(
                            current,
                            "_user_policy_livingmemory_engine_wrapped",
                            False,
                        ):
                            setattr(engine, name, original)
                    except Exception:
                        pass
        self._originals.clear()
        self._handler_originals.clear()
        self._engine_originals.clear()
        self._engine_ready = False
        self.target = None
        self.applied = False

    def report(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "activated": self.activated,
            "applied": self.applied,
            "enabled": True,
            "message": self.message,
        }

    def _wrap_entry(
        self,
        method: Callable[..., Any],
    ) -> Callable[..., Any]:
        @wraps(method)
        async def wrapper(*args, **kwargs):
            event = self._find_event(args, kwargs)
            scope = self._scope_for_event(event)
            await self._ensure_engine_wrapped()
            token = self._active.set(scope)
            try:
                return await method(*args, **kwargs)
            finally:
                self._active.reset(token)

        return wrapper

    async def _ensure_engine_wrapped(self) -> None:
        if self._engine_ready:
            return
        target = self.target
        if target is None:
            return
        ensure = getattr(target, "_ensure_plugin_ready", None)
        if callable(ensure):
            try:
                await ensure()
            except Exception:
                pass
        initializer = getattr(target, "initializer", None)
        engine = self._memory_engine(target)
        if engine is None:
            return
        for name in ("search_memories", "add_memory"):
            original = getattr(engine, name, None)
            if not callable(original) or name in self._engine_originals:
                continue
            wrapped = self._wrap_engine_method(name, original)
            self._engine_originals[name] = original
            setattr(engine, name, wrapped)
        self._engine_ready = all(
            name in self._engine_originals
            or getattr(
                getattr(engine, name, None),
                "_user_policy_livingmemory_engine_wrapped",
                False,
            )
            for name in ("search_memories", "add_memory")
        )

    def _wrap_engine_method(
        self,
        name: str,
        method: Callable[..., Any],
    ) -> Callable[..., Any]:
        @wraps(method)
        async def wrapper(*args, **kwargs):
            scope = self._active.get()
            if scope is not None:
                isolation, persona_id = scope
                if isolation and persona_id:
                    kwargs["persona_id"] = persona_id
                elif not isolation:
                    kwargs["persona_id"] = None
            return await method(*args, **kwargs)

        setattr(wrapper, "_user_policy_livingmemory_engine_wrapped", True)
        setattr(wrapper, "_user_policy_livingmemory_engine_original", method)
        return wrapper

    @staticmethod
    def _memory_engine(target: Any) -> Any | None:
        initializer = getattr(target, "initializer", None)
        engine = getattr(initializer, "memory_engine", None)
        if engine is not None:
            return engine
        event_handler = getattr(target, "event_handler", None)
        return getattr(event_handler, "memory_engine", None)

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

    def _scope_for_event(
        self,
        event: Any,
    ) -> tuple[bool, str] | None:
        if event is None:
            return None
        getter = getattr(event, "get_extra", None)
        decision = (
            getter(getattr(self.plugin, "EXTRA_DECISION", ""))
            if callable(getter)
            else None
        )
        if decision is None or not bool(
            getattr(decision, "policy_configured", False)
        ):
            return None
        return (
            bool(getattr(decision, "livingmemory_isolation", True)),
            str(getattr(decision, "persona_id", "") or ""),
        )

    @staticmethod
    def _find_event(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        for value in [*args, *kwargs.values()]:
            if hasattr(value, "get_extra") and hasattr(
                value,
                "unified_msg_origin",
            ):
                return value
        return None
