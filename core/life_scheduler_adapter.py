"""Life Scheduler 的人格日程库运行时适配层。"""

from __future__ import annotations

import datetime
import inspect
from functools import wraps
from typing import Any, Callable

from .matcher import EventIdentity, PolicyDecision, parse_session_identity
from .runtime_helpers import find_plugin


LIFE_SCHEDULER_NAMES = {"astrbot_plugin_life_scheduler"}
RUNTIME_METHODS = {"on_llm_request", "life_show", "life_renew"}
WRAPPED_MARKER = "_user_policy_life_wrapped"
OWNER_ATTR = "_user_policy_life_owner"
ORIGINAL_ATTR = "_user_policy_life_original"


class LifeSchedulerPersonaAdapter:
    """使用本插件日程库替代已映射人格的全局日程。"""

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
        self.message = "未检测到 Life Scheduler。"
        self._originals: dict[str, Callable[..., Any]] = {}
        self._handler_originals: dict[str, Callable[..., Any]] = {}

    def configure(self) -> None:
        target, metadata = find_plugin(
            self.plugin.context,
            self.fallback_plugins,
            LIFE_SCHEDULER_NAMES,
        )
        if target is None:
            self.installed = False
            self.activated = False
            self.applied = False
            self.message = "未检测到 Life Scheduler。"
            return
        self.installed = True
        self.activated = bool(
            metadata.get("activated", True)
            if isinstance(metadata, dict)
            else getattr(metadata, "activated", True)
        )
        if not self.activated:
            self.applied = False
            self.message = "Life Scheduler 当前未启用。"
            return
        if not callable(getattr(target, "on_llm_request", None)):
            self.applied = False
            self.message = "当前 Life Scheduler 版本暂不兼容。"
            return
        if self.target is not None and self.target is not target:
            self._originals.clear()
            self._handler_originals.clear()
        self.target = target
        for name in RUNTIME_METHODS:
            current = getattr(target, name, None)
            if not callable(current):
                continue
            original = self._unwrap(current)
            self._originals[name] = original
            wrapped = self._wrap_method(original)
            self._mark_wrapper(wrapped, original)
            setattr(target, name, wrapped)
        self._wrap_registry_handlers(target)
        self.applied = True
        self.message = "已启用人格日程库；未映射人格继续使用原全局日程。"

    def restore(self) -> None:
        target = self.target
        if target is not None:
            for name, original in self._originals.items():
                try:
                    current = getattr(target, name, None)
                    if getattr(current, WRAPPED_MARKER, False):
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
                    if getattr(current, WRAPPED_MARKER, False):
                        handler.handler = original
                except Exception:
                    pass
        self._originals.clear()
        self._handler_originals.clear()
        self.target = None
        self.applied = False

    def report(self) -> dict[str, Any]:
        manager = getattr(self.plugin, "life_schedule_library", None)
        return {
            "installed": self.installed,
            "activated": self.activated,
            "applied": self.applied,
            "libraries": len(manager.libraries()) if manager else 0,
            "mapped_personas": len(manager.persona_map()) if manager else 0,
            "message": self.message,
        }

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

    @staticmethod
    def _registry_handlers(target: Any) -> list[Any]:
        module_path = str(getattr(target.__class__, "__module__", "") or "")
        if not module_path:
            return []
        try:
            from astrbot.core.star.star_handler import star_handlers_registry
        except Exception:
            return []

        getter = getattr(
            star_handlers_registry,
            "get_handlers_by_module_name",
            None,
        )
        if callable(getter):
            return list(getter(module_path) or [])
        return [
            handler
            for handler in star_handlers_registry
            if getattr(handler, "handler_module_path", "") == module_path
        ]

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
        values = [str(name or ""), str(full_name or "")]
        for method in RUNTIME_METHODS:
            for value in values:
                if (
                    value == method
                    or value.endswith(f".{method}")
                    or value.endswith(f"_{method}")
                    or value.endswith(f":{method}")
                ):
                    return True
        return False

    def _wrap_method(
        self,
        method: Callable[..., Any],
    ) -> Callable[..., Any]:
        name = str(getattr(method, "__name__", "") or "")
        if inspect.isasyncgenfunction(method):

            @wraps(method)
            async def asyncgen_wrapper(*args, **kwargs):
                event = self._find_event(args, kwargs)
                persona_id = self._persona_for_event(event)
                if not self._is_mapped(persona_id):
                    async for item in method(*args, **kwargs):
                        yield item
                    return
                date = self._business_now()
                extra = self._find_extra(args, kwargs)
                record = await self._record_or_generate(
                    persona_id,
                    date,
                    event,
                    extra if name == "life_renew" else "",
                    force=name == "life_renew",
                )
                if not record:
                    yield event.plain_result(
                        "当前人格日程暂不可用，请稍后再试。"
                    )
                    return
                if name == "life_renew" and extra:
                    yield event.plain_result(
                        f"已按补充要求重写人格日程：{extra}"
                    )
                yield event.plain_result(self._format_record(record))

            return asyncgen_wrapper

        @wraps(method)
        async def coroutine_wrapper(*args, **kwargs):
            event = self._find_event(args, kwargs)
            request = self._find_request(args, kwargs)
            persona_id = self._persona_for_event(event)
            if not self._is_mapped(persona_id) or request is None:
                return await method(*args, **kwargs)
            date = self._business_now()
            record = await self._record_or_generate(
                persona_id,
                date,
                event,
            )
            if not record or record.get("status") != "ok":
                return None
            injection = self._build_injection(record, date)
            if injection and injection not in str(
                getattr(request, "system_prompt", "") or ""
            ):
                request.system_prompt = (
                    str(getattr(request, "system_prompt", "") or "")
                    + injection
                )
            return None

        return coroutine_wrapper

    async def _record_or_generate(
        self,
        persona_id: str,
        date: datetime.datetime,
        event: Any,
        extra: str = "",
        *,
        force: bool = False,
    ) -> dict[str, Any] | None:
        manager = self.plugin.life_schedule_library
        record = manager.record_for_persona(persona_id, date)
        if record is not None and not force:
            return record
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        try:
            return await manager.generate(
                self.target,
                persona_id,
                date,
                umo,
                extra,
            )
        except Exception:
            return record

    def _build_injection(
        self,
        record: dict[str, Any],
        date: datetime.datetime,
    ) -> str:
        target = self.target
        module = inspect.getmodule(target.__class__) if target else None
        builder = getattr(module, "build_character_state_injection", None)
        if callable(builder):
            try:
                return str(
                    builder(
                        record.get("outfit", ""),
                        record.get("schedule", ""),
                        business_now=date,
                    )
                    or ""
                )
            except Exception:
                pass
        return (
            "\n\n【当前人格生活日程】\n"
            f"日期：{record.get('date', '')}\n"
            f"穿搭风格：{record.get('outfit_style') or '未设置'}\n"
            f"今日穿搭：{record.get('outfit') or '未设置'}\n"
            f"日程安排：\n{record.get('schedule') or '未设置'}"
        )

    def _business_now(self) -> datetime.datetime:
        target = self.target
        module = inspect.getmodule(target.__class__) if target else None
        resolver = getattr(module, "resolve_business_now", None)
        config = getattr(target, "config", {}) if target else {}
        if callable(resolver):
            try:
                return resolver(config.get("schedule_time"))
            except Exception:
                pass
        return datetime.datetime.now()

    def _persona_for_event(self, event: Any) -> str:
        decision = self._decision_for_event(event)
        persona_id = str(getattr(decision, "persona_id", "") or "")
        if persona_id:
            return persona_id
        if str(getattr(decision, "persona_mode", "") or "") == "auto":
            return self._auto_persona_for_decision(decision, event)
        return self._effective_persona_extra(event)

    def _decision_for_event(self, event: Any) -> PolicyDecision | None:
        if event is None:
            return None
        decision = self._cached_decision(event)
        if decision is not None:
            return decision
        matcher = getattr(self.plugin, "matcher", None)
        if matcher is None:
            return None
        identity = self._identity_for_event(event)
        if identity is None:
            return None
        try:
            if identity.is_private:
                return matcher.match_private_aliases(
                    identity,
                    self._identity_aliases(event),
                )
            return matcher.match(identity)
        except Exception:
            return None

    def _cached_decision(self, event: Any) -> PolicyDecision | None:
        getter = getattr(event, "get_extra", None)
        if not callable(getter):
            return None
        try:
            decision = getter(getattr(self.plugin, "EXTRA_DECISION", ""))
        except Exception:
            decision = None
        return decision if isinstance(decision, PolicyDecision) else None

    def _auto_persona_for_decision(
        self,
        decision: PolicyDecision | None,
        event: Any,
    ) -> str:
        manager = getattr(self.plugin, "auto_persona", None)
        if manager is None:
            return self._effective_persona_extra(event)
        getters: list[tuple[str, tuple[Any, ...]]] = []
        if decision is not None:
            getters.append(("record_for_decision", (decision,)))
            getters.append(("session_record_for_identity", (decision.identity,)))
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if umo:
            getters.append(("session_record", (umo,)))
        for name, args in getters:
            getter = getattr(manager, name, None)
            if not callable(getter):
                continue
            try:
                record = getter(*args)
            except Exception:
                continue
            persona_id = str(
                (record if isinstance(record, dict) else {}).get(
                    "persona_id",
                    "",
                )
                or ""
            )
            if persona_id:
                return persona_id
        return self._effective_persona_extra(event)

    @staticmethod
    def _effective_persona_extra(event: Any) -> str:
        getter = getattr(event, "get_extra", None)
        if not callable(getter):
            return ""
        for key in (
            "user_policy_effective_persona_id",
            "user_policy_auto_persona_resolved",
        ):
            try:
                value = getter(key)
            except Exception:
                value = ""
            if isinstance(value, str) and value:
                return value
        return ""

    def _identity_for_event(self, event: Any) -> EventIdentity | None:
        parsed = parse_session_identity(
            str(getattr(event, "unified_msg_origin", "") or "")
        )
        if parsed is not None:
            if parsed.is_private:
                user_id = self._event_user_id(event) or parsed.user_id
                return EventIdentity(
                    parsed.platform,
                    user_id,
                    chat_type="private",
                    role=self._event_role(event),
                )
            user_id = self._event_user_id(event)
            group_id = self._event_group_id(event) or parsed.group_id
            return EventIdentity(
                parsed.platform,
                user_id,
                group_id,
                "group",
                self._event_role(event),
            )
        is_private = self._event_is_private(event)
        platform = self._event_platform(event)
        user_id = self._event_user_id(event)
        group_id = "" if is_private else self._event_group_id(event)
        if not user_id and is_private:
            return None
        if not group_id and not is_private:
            return None
        return EventIdentity(
            platform,
            user_id,
            group_id,
            "private" if is_private else "group",
            self._event_role(event),
        )

    def _identity_aliases(self, event: Any) -> list[str]:
        aliases: list[str] = []
        self._append_identity_alias(
            aliases,
            str(getattr(event, "unified_msg_origin", "") or ""),
        )
        message = getattr(event, "message_obj", None)
        for attr in (
            "session_id",
            "session",
            "conversation_id",
            "user_id",
            "sender_id",
        ):
            self._append_identity_alias(aliases, getattr(message, attr, ""))
        sender = getattr(message, "sender", None)
        for attr in ("user_id", "sender_id", "id"):
            self._append_identity_alias(aliases, getattr(sender, attr, ""))
        return aliases

    @staticmethod
    def _append_identity_alias(aliases: list[str], value: Any) -> None:
        raw = str(value or "").strip()
        if not raw:
            return
        candidates = [raw]
        parsed = parse_session_identity(raw)
        if parsed is not None and parsed.is_private:
            candidates.append(parsed.user_id)
        parts = raw.split(":")
        if len(parts) >= 3:
            candidates.append(parts[-1])
        for candidate in list(candidates):
            if "!" in candidate:
                bang_parts = [
                    part.strip()
                    for part in candidate.split("!")
                    if part.strip()
                ]
                candidates.extend(bang_parts)
                if bang_parts:
                    candidates.append(bang_parts[-1])
        seen = set(aliases)
        for candidate in candidates:
            normalized = str(candidate or "").strip()
            if normalized and normalized not in seen:
                aliases.append(normalized)
                seen.add(normalized)

    @staticmethod
    def _event_is_private(event: Any) -> bool:
        checker = getattr(event, "is_private_chat", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                pass
        umo = str(getattr(event, "unified_msg_origin", "") or "").lower()
        return "friendmessage" in umo or "privatemessage" in umo

    @staticmethod
    def _event_platform(event: Any) -> str:
        for name in ("get_platform_name", "get_platform_id"):
            getter = getattr(event, name, None)
            if callable(getter):
                try:
                    value = getter()
                except Exception:
                    value = ""
                if value:
                    return str(value)
        parsed = parse_session_identity(
            str(getattr(event, "unified_msg_origin", "") or "")
        )
        return parsed.platform if parsed is not None else ""

    @staticmethod
    def _event_user_id(event: Any) -> str:
        getter = getattr(event, "get_sender_id", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                value = ""
            if value:
                return str(value)
        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        for attr in ("user_id", "sender_id", "id"):
            value = getattr(sender, attr, "")
            if value:
                return str(value)
        return ""

    @staticmethod
    def _event_group_id(event: Any) -> str:
        getter = getattr(event, "get_group_id", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                value = ""
            if value:
                return str(value)
        message = getattr(event, "message_obj", None)
        value = getattr(message, "group_id", "")
        if value:
            return str(value)
        group = getattr(message, "group", None)
        return str(getattr(group, "group_id", "") or "")

    @staticmethod
    def _event_role(event: Any) -> str:
        checker = getattr(event, "is_admin", None)
        if callable(checker):
            try:
                return "admin" if checker() else "member"
            except Exception:
                pass
        return "member"

    def _is_mapped(self, persona_id: str) -> bool:
        return bool(
            persona_id
            and self.plugin.life_schedule_library.library_id_for_persona(
                persona_id
            )
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

    @staticmethod
    def _find_request(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        for value in [*args, *kwargs.values()]:
            if hasattr(value, "system_prompt"):
                return value
        return None

    @staticmethod
    def _find_extra(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        if kwargs.get("extra"):
            return str(kwargs["extra"])
        for value in reversed(args):
            if isinstance(value, str):
                return value
        return ""

    @staticmethod
    def _format_record(record: dict[str, Any]) -> str:
        return (
            f"📅 {record.get('date', '')}\n"
            f"👗 今日穿搭：{record.get('outfit') or '未设置'}\n"
            f"📝 日程安排：\n{record.get('schedule') or '未设置'}"
        )
