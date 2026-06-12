"""Proactive Chat 的人格与隔离会话适配层。"""

from __future__ import annotations

import json
from functools import wraps
from typing import Any, Callable

from .matcher import EventIdentity, parse_session_identity
from .runtime_helpers import find_plugin


PROACTIVE_CHAT_NAMES = {"astrbot_plugin_proactive_chat"}
PERSONA_PROMPT_MARKER = "【用户策略主动消息人格】"
PERSONA_EXTRA_MARKER = "【人格主动消息补充要求】"


class ProactiveChatPersonaAdapter:
    """让后台主动消息沿用最后有效人格和隔离后的对话历史。"""

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
        self.message = "未检测到 Proactive Chat。"
        self._original: Callable[..., Any] | None = None

    def configure(self) -> None:
        target, metadata = find_plugin(
            self.plugin.context,
            self.fallback_plugins,
            PROACTIVE_CHAT_NAMES,
        )
        if target is None:
            self.restore()
            self.installed = False
            self.activated = False
            self.applied = False
            self.message = "未检测到 Proactive Chat。"
            return
        self.installed = True
        self.activated = bool(
            metadata.get("activated", True)
            if isinstance(metadata, dict)
            else getattr(metadata, "activated", True)
        )
        prepare = getattr(target, "_prepare_llm_request", None)
        if not self.activated:
            self.restore()
            self.applied = False
            self.message = "Proactive Chat 当前未启用。"
            return
        if not callable(prepare):
            self.restore()
            self.applied = False
            self.message = "当前 Proactive Chat 版本暂不兼容。"
            return
        if self.target is not None and self.target is not target:
            self.restore()
            prepare = getattr(target, "_prepare_llm_request", None)
        self.target = target
        owner = getattr(prepare, "_user_policy_proactive_owner", None)
        if (
            getattr(prepare, "_user_policy_proactive_wrapped", False)
            and owner is not self
        ):
            original = getattr(
                prepare,
                "_user_policy_proactive_original",
                None,
            )
            if callable(original):
                prepare = original
        if owner is not self:
            wrapped = self._wrap_prepare(prepare)
            setattr(wrapped, "_user_policy_proactive_wrapped", True)
            setattr(wrapped, "_user_policy_proactive_owner", self)
            setattr(wrapped, "_user_policy_proactive_original", prepare)
            self._original = prepare
            setattr(target, "_prepare_llm_request", wrapped)
        self.applied = True
        self.message = (
            "固定人格按当前用户策略生效；自动人格沿用会话最后选择，"
            "并复用隔离上下文。"
        )

    def report(self) -> dict[str, Any]:
        prompts = self._prompts()
        return {
            "installed": self.installed,
            "activated": self.activated,
            "applied": self.applied,
            "configured_personas": len(prompts),
            "message": self.message,
        }

    def restore(self) -> None:
        if self.target is not None and self._original is not None:
            current = getattr(
                self.target,
                "_prepare_llm_request",
                None,
            )
            if getattr(
                current,
                "_user_policy_proactive_owner",
                None,
            ) is self:
                setattr(
                    self.target,
                    "_prepare_llm_request",
                    self._original,
                )
        self.target = None
        self._original = None
        self.applied = False

    def _wrap_prepare(
        self,
        method: Callable[..., Any],
    ) -> Callable[..., Any]:
        @wraps(method)
        async def wrapper(session_id: str, *args, **kwargs):
            prepared = await method(session_id, *args, **kwargs)
            if not isinstance(prepared, dict):
                return prepared
            record = self._persona_record(session_id)
            persona_id = str(record.get("persona_id", "") or "")
            if not persona_id:
                return prepared
            prompt = await self.plugin.personas.get_persona_prompt(persona_id)
            if prompt:
                prepared["system_prompt"] = (
                    f"{PERSONA_PROMPT_MARKER}\n{prompt}"
                )
            extra = str(self._prompts().get(persona_id, "") or "").strip()
            if extra:
                prepared["system_prompt"] = (
                    str(prepared.get("system_prompt", "") or "").rstrip()
                    + f"\n\n{PERSONA_EXTRA_MARKER}\n{extra}"
                )

            if bool(record.get("memory_isolation", False)):
                await self._apply_isolated_history(
                    prepared,
                    str(prepared.get("session_id", "") or session_id),
                    record,
                    persona_id,
                )
            life_manager = getattr(
                self.plugin,
                "life_schedule_library",
                None,
            )
            if life_manager is not None:
                injection = life_manager.injection_for_persona(persona_id)
                if injection:
                    prepared["system_prompt"] = (
                        str(prepared.get("system_prompt", "") or "").rstrip()
                        + injection
                    )
            return prepared

        return wrapper

    def _persona_record(self, session_id: str) -> dict[str, Any]:
        manager = getattr(self.plugin, "auto_persona", None)
        matcher = getattr(self.plugin, "matcher", None)
        identity = self._parse_identity(session_id)
        decision = None
        if matcher is not None and identity is not None:
            try:
                decision = matcher.match_private_aliases(
                    identity,
                    self._identity_aliases(session_id),
                ) if identity.is_private else matcher.match(identity)
            except Exception:
                decision = None

        # 当前固定策略优先，避免旧状态或目标插件默认人格覆盖用户配置。
        if (
            decision is not None
            and decision.policy_configured
            and decision.persona_mode == "fixed"
            and decision.persona_id
        ):
            return self._fixed_record(manager, decision)

        record = {}
        getter = getattr(manager, "session_record", None)
        if callable(getter):
            try:
                record = getter(session_id)
            except Exception:
                record = {}
        recorded_identity = self._identity_from_record(record)
        if matcher is not None and recorded_identity is not None:
            try:
                recorded_decision = matcher.match(recorded_identity)
            except Exception:
                recorded_decision = None
            if (
                recorded_decision is not None
                and recorded_decision.policy_configured
            ):
                if (
                    recorded_decision.persona_mode == "fixed"
                    and recorded_decision.persona_id
                ):
                    return self._fixed_record(
                        manager,
                        recorded_decision,
                        record,
                    )
                if recorded_decision.persona_mode == "default":
                    return {}
        if record.get("persona_id"):
            return record

        identity_getter = getattr(
            manager,
            "session_record_for_identity",
            None,
        )
        if identity is not None and callable(identity_getter):
            try:
                record = identity_getter(identity)
            except Exception:
                record = {}
            if record.get("persona_id"):
                return record

        decision_getter = getattr(manager, "record_for_decision", None)
        if decision is not None and callable(decision_getter):
            try:
                return decision_getter(decision)
            except Exception:
                pass
        return record if isinstance(record, dict) else {}

    @staticmethod
    def _identity_from_record(
        record: Any,
    ) -> EventIdentity | None:
        if not isinstance(record, dict):
            return None
        chat_type = str(record.get("chat_type", "") or "")
        if chat_type not in {"private", "group"}:
            return None
        user_id = str(record.get("user_id", "") or "")
        group_id = str(record.get("group_id", "") or "")
        if chat_type == "private" and not user_id:
            return None
        if chat_type == "group" and not group_id:
            return None
        return EventIdentity(
            platform=str(record.get("platform", "") or ""),
            user_id=user_id,
            group_id=group_id,
            chat_type=chat_type,
        )

    @staticmethod
    def _fixed_record(
        manager: Any,
        decision: Any,
        base: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = dict(base or {})
        getter = getattr(manager, "record_for_decision", None)
        if callable(getter):
            try:
                saved = getter(decision)
                if isinstance(saved, dict):
                    record.update(saved)
            except Exception:
                pass
        scope_key = str(record.get("scope_key", "") or "")
        scope_getter = getattr(manager, "scope_key", None)
        if not scope_key and callable(scope_getter):
            try:
                scope_key = str(scope_getter(decision) or "")
            except Exception:
                scope_key = ""
        return {
            **record,
            "persona_id": str(decision.persona_id),
            "persona_mode": "fixed",
            "memory_isolation": bool(decision.memory_isolation),
            "livingmemory_isolation": bool(
                decision.livingmemory_isolation
            ),
            "scope_key": scope_key,
        }

    @staticmethod
    def _parse_identity(session_id: str) -> EventIdentity | None:
        return parse_session_identity(session_id)

    @staticmethod
    def _identity_aliases(session_id: str) -> list[str]:
        aliases = []
        raw = str(session_id or "").strip()
        if not raw:
            return aliases
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
        seen = set()
        for candidate in candidates:
            normalized = str(candidate or "").strip()
            if normalized and normalized not in seen:
                aliases.append(normalized)
                seen.add(normalized)
        return aliases

    async def _apply_isolated_history(
        self,
        prepared: dict[str, Any],
        session_id: str,
        record: dict[str, Any],
        persona_id: str,
    ) -> None:
        isolation = getattr(self.plugin, "memory_isolation", None)
        if isolation is None:
            return
        conversation_id = isolation.bound_conversation_id(
            str(record.get("scope_key", "") or ""),
            persona_id,
        )
        if not conversation_id:
            prepared["history"] = []
            return
        manager = getattr(self.plugin.context, "conversation_manager", None)
        getter = getattr(manager, "get_conversation", None)
        if not callable(getter):
            return
        try:
            conversation = await getter(session_id, conversation_id)
        except Exception:
            return
        if conversation is None:
            return
        history = getattr(conversation, "history", [])
        if isinstance(history, str):
            try:
                history = json.loads(history)
            except json.JSONDecodeError:
                history = []
        if isinstance(history, list):
            prepared["history"] = history
            prepared["conv_id"] = conversation_id

    def _prompts(self) -> dict[str, str]:
        store = getattr(self.plugin, "store", None)
        config = (
            store.config.get("proactive_chat_persona_prompts", {})
            if store is not None
            else {}
        )
        prompts = config.get("prompts", {}) if isinstance(config, dict) else {}
        return prompts if isinstance(prompts, dict) else {}
