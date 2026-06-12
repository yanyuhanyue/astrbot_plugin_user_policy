"""按人格隔离 AstrBot 对话历史，并保存稳定的对话绑定。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from .matcher import PolicyDecision


BINDING_SCHEMA_VERSION = 1
DEFAULT_PERSONA_KEY = "__astrbot_default__"


class MemoryIsolationManager:
    """只切换当前 LLM 请求使用的 AstrBot 对话，不接管第三方记忆库。"""

    def __init__(self, context: Any, data_dir: Path):
        self.context = context
        self.path = data_dir.resolve() / "memory_bindings.json"
        self.lock = asyncio.Lock()
        self.bindings: dict[str, Any] = {
            "schema_version": BINDING_SCHEMA_VERSION,
            "bindings": {},
        }
        self.load()

    def load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        bindings = raw.get("bindings")
        if isinstance(bindings, dict):
            self.bindings = {
                "schema_version": BINDING_SCHEMA_VERSION,
                "bindings": bindings,
            }

    async def apply(
        self,
        event: Any,
        request: Any,
        decision: PolicyDecision,
    ) -> None:
        """将本次请求切换到当前人格对应的独立对话。"""

        scope_key, scope_type = self._scope(decision)
        persona_key = decision.persona_id or DEFAULT_PERSONA_KEY
        namespace_scope = (
            scope_key
            if decision.memory_isolation
            else self._shared_scope(decision)
        )
        namespace_key = persona_key if decision.memory_isolation else "__shared__"
        namespace = self._namespace(namespace_scope, namespace_key)
        event.set_extra(
            "user_policy_memory_isolation",
            bool(decision.memory_isolation),
        )
        event.set_extra("user_policy_memory_namespace", namespace)

        manager = getattr(self.context, "conversation_manager", None)
        conversation = getattr(request, "conversation", None)
        current_cid = str(getattr(conversation, "cid", "") or "")
        umo = self._unified_msg_origin(event)
        if (
            manager is None
            or not current_cid
            or not umo
            or not callable(getattr(manager, "get_conversation", None))
        ):
            return

        async with self.lock:
            all_bindings = self.bindings.setdefault("bindings", {})
            entry = all_bindings.get(scope_key)
            changed = False

            if not decision.memory_isolation:
                if scope_type == "member" or not isinstance(entry, dict):
                    return
                shared_cid = str(entry.get("shared_conversation_id", "") or "")
                target = await self._get_conversation(
                    manager,
                    umo,
                    shared_cid,
                )
                if target is None:
                    return
                await self._select_for_session(
                    manager,
                    umo,
                    shared_cid,
                    scope_type,
                )
                self._apply_conversation(request, target)
                event.set_extra(
                    "user_policy_conversation_id",
                    shared_cid,
                )
                return

            if not isinstance(entry, dict):
                entry = {
                    "shared_conversation_id": current_cid,
                    "personas": {},
                }
                all_bindings[scope_key] = entry
                changed = True

            personas = entry.setdefault("personas", {})
            persona_meta = entry.setdefault("persona_meta", {})
            target_cid = str(personas.get(persona_key, "") or "")
            target = await self._get_conversation(
                manager,
                umo,
                target_cid,
            )
            target_bound_persona = str(
                getattr(target, "persona_id", "") or ""
            ).strip()
            if (
                target is not None
                and decision.persona_id
                and target_bound_persona
                and target_bound_persona != decision.persona_id
            ):
                personas.pop(persona_key, None)
                persona_meta.pop(persona_key, None)
                target_cid = ""
                target = None
                changed = True

            if target is None:
                first_persona = not bool(personas)
                current_persona_id = str(
                    getattr(conversation, "persona_id", "") or ""
                ).strip()
                should_copy_history = self._should_copy_initial_history(
                    first_persona,
                    decision.persona_id,
                    current_persona_id,
                )
                content = (
                    deepcopy(self._history_from_request(request))
                    if should_copy_history
                    else []
                )
                previous_cid = await self._current_conversation_id(
                    manager,
                    umo,
                    current_cid,
                )
                target_cid = await manager.new_conversation(
                    umo,
                    self._platform_id(event, decision),
                    content=content,
                    title=self._conversation_title(decision.persona_id),
                    persona_id=decision.persona_id or None,
                )
                target = await self._get_conversation(
                    manager,
                    umo,
                    target_cid,
                )
                if scope_type == "member" and previous_cid:
                    await manager.switch_conversation(umo, previous_cid)
                personas[persona_key] = target_cid
                persona_meta[persona_key] = {
                    "persona_id": decision.persona_id or "",
                    "seed": "copied" if should_copy_history else "empty",
                    "copied_from_persona_id": current_persona_id,
                }
                changed = True

            if target is None:
                return
            await self._select_for_session(
                manager,
                umo,
                target_cid,
                scope_type,
            )
            self._apply_conversation(request, target)
            event.set_extra(
                "user_policy_conversation_id",
                target_cid,
            )
            if changed:
                self._atomic_write()

    async def reset_persona_binding(
        self,
        decision: PolicyDecision,
        persona_id: str,
    ) -> str:
        """解除当前规则范围内某个人格的隔离会话绑定。"""

        scope_key, _scope_type = self._scope(decision)
        persona_key = persona_id or DEFAULT_PERSONA_KEY
        async with self.lock:
            entry = self.bindings.setdefault("bindings", {}).get(scope_key)
            if not isinstance(entry, dict):
                return ""
            personas = entry.get("personas", {})
            if not isinstance(personas, dict):
                return ""
            old_cid = str(personas.pop(persona_key, "") or "")
            meta = entry.get("persona_meta", {})
            if isinstance(meta, dict):
                meta.pop(persona_key, None)
            if old_cid:
                self._atomic_write()
            return old_cid

    @staticmethod
    def _should_copy_initial_history(
        first_persona: bool,
        target_persona_id: str,
        current_persona_id: str,
    ) -> bool:
        if not first_persona:
            return False
        target = str(target_persona_id or "").strip()
        current = str(current_persona_id or "").strip()
        if not target:
            return True
        return not current or current == target

    @staticmethod
    def _scope(decision: PolicyDecision) -> tuple[str, str]:
        identity = decision.identity
        platform = identity.platform or "unknown"
        if identity.is_private:
            return f"private:{platform}:{identity.user_id}", "private"

        member_rule = decision.user_rule or {}
        has_member_scope = (
            str(member_rule.get("persona_mode", "inherit")) != "inherit"
        ) or bool(member_rule.get("persona_id")) or (
            member_rule.get("memory_isolation") is not None
        )
        if has_member_scope:
            return (
                f"member:{platform}:{identity.group_id}:{identity.user_id}",
                "member",
            )
        return f"group:{platform}:{identity.group_id}", "group"

    @staticmethod
    def _shared_scope(decision: PolicyDecision) -> str:
        identity = decision.identity
        platform = identity.platform or "unknown"
        if identity.is_private:
            return f"private:{platform}:{identity.user_id}"
        return f"group:{platform}:{identity.group_id}"

    @staticmethod
    def _namespace(scope_key: str, persona_key: str) -> str:
        digest = hashlib.sha256(
            f"{scope_key}\0{persona_key}".encode("utf-8")
        ).hexdigest()
        return f"user-policy:{digest[:32]}"

    @staticmethod
    def _history_from_request(request: Any) -> list[dict[str, Any]]:
        contexts = getattr(request, "contexts", None)
        if isinstance(contexts, list):
            return deepcopy(contexts)
        return MemoryIsolationManager._history_from_conversation(
            getattr(request, "conversation", None)
        )

    @staticmethod
    def _history_from_conversation(conversation: Any) -> list[dict[str, Any]]:
        history = getattr(conversation, "history", [])
        if isinstance(history, list):
            return deepcopy(history)
        if isinstance(history, str):
            try:
                decoded = json.loads(history)
            except json.JSONDecodeError:
                return []
            if isinstance(decoded, list):
                return decoded
        return []

    @classmethod
    def _apply_conversation(cls, request: Any, conversation: Any) -> None:
        request.conversation = conversation
        request.contexts = cls._history_from_conversation(conversation)

    @staticmethod
    async def _get_conversation(
        manager: Any,
        umo: str,
        conversation_id: str,
    ) -> Any:
        if not conversation_id:
            return None
        return await manager.get_conversation(umo, conversation_id)

    @staticmethod
    async def _current_conversation_id(
        manager: Any,
        umo: str,
        fallback: str,
    ) -> str:
        getter = getattr(manager, "get_curr_conversation_id", None)
        if not callable(getter):
            return fallback
        return str(await getter(umo) or fallback)

    @staticmethod
    async def _select_for_session(
        manager: Any,
        umo: str,
        conversation_id: str,
        scope_type: str,
    ) -> None:
        if scope_type == "member":
            return
        switcher = getattr(manager, "switch_conversation", None)
        if callable(switcher):
            await switcher(umo, conversation_id)

    @staticmethod
    def _unified_msg_origin(event: Any) -> str:
        value = getattr(event, "unified_msg_origin", "")
        if callable(value):
            try:
                value = value()
            except Exception:
                value = ""
        return str(value or "")

    @staticmethod
    def _platform_id(event: Any, decision: PolicyDecision) -> str:
        getter = getattr(event, "get_platform_id", None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return str(value)
            except Exception:
                pass
        return decision.identity.platform or "unknown"

    @staticmethod
    def _conversation_title(persona_id: str) -> str:
        name = persona_id or "AstrBot 默认人格"
        return f"用户策略：{name}"

    def _atomic_write(self) -> None:
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                self.bindings,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def bound_conversation_id(
        self,
        scope_key: str,
        persona_id: str,
    ) -> str:
        """返回指定人格已绑定的 AstrBot 对话 ID。"""

        entry = self.bindings.get("bindings", {}).get(scope_key, {})
        if not isinstance(entry, dict):
            return ""
        persona_key = persona_id or DEFAULT_PERSONA_KEY
        personas = entry.get("personas", {})
        if not isinstance(personas, dict):
            return ""
        return str(personas.get(persona_key, "") or "")
