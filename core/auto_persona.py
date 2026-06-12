"""自动人格选择与后台会话人格状态。"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .matcher import EventIdentity, PolicyDecision


STATE_SCHEMA_VERSION = 1
AUTO_PERSONA_EVENT_KEY = "user_policy_auto_persona_resolved"
EFFECTIVE_PERSONA_EVENT_KEY = "user_policy_effective_persona_id"
SELECTED_PROVIDER_EVENT_KEY = "selected_provider"
SELECTED_MODEL_EVENT_KEY = "selected_model"

SELECTOR_SYSTEM_PROMPT = """你是 AstrBot 的人格选择器。
请根据用户最新消息，从管理员给出的候选人格中选择最合适的一个。
只返回严格 JSON，不要输出解释、Markdown 或其他文字：
{"persona_id":"候选人格ID"}
人格 ID 必须来自候选列表。"""


class AutoPersonaManager:
    """按规则调用独立模型选人格，并保存会话最后有效人格。"""

    def __init__(
        self,
        context: Any,
        data_dir: Path,
        config_getter: Callable[[], dict[str, Any]],
        persona_adapter: Any,
    ):
        self.context = context
        self.path = Path(data_dir).resolve() / "auto_persona_state.json"
        self.config_getter = config_getter
        self.persona_adapter = persona_adapter
        self.lock = asyncio.Lock()
        self._write_task: asyncio.Task[None] | None = None
        self._dirty = False
        self._write_delay = 0.05
        self.state: dict[str, Any] = {
            "schema_version": STATE_SCHEMA_VERSION,
            "scopes": {},
            "sessions": {},
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
        scopes = raw.get("scopes")
        sessions = raw.get("sessions")
        if isinstance(scopes, dict) and isinstance(sessions, dict):
            self.state = {
                "schema_version": STATE_SCHEMA_VERSION,
                "scopes": scopes,
                "sessions": sessions,
            }

    async def resolve(
        self,
        event: Any,
        decision: PolicyDecision,
        message: str,
        *,
        allow_selection: bool = True,
    ) -> str:
        """解析本次消息最终人格，并更新后台会话映射。"""

        scope_key = self.scope_key(decision)
        umo = self._umo(event)
        persona_id = str(decision.persona_id or "").strip()
        if decision.persona_mode == "auto":
            if allow_selection:
                persona_id = await self._select(
                    event,
                    scope_key,
                    decision.auto_persona,
                    message,
                )
            else:
                candidate_ids = [
                    str(item)
                    for item in decision.auto_persona.get(
                        "persona_ids",
                        [],
                    )
                    if str(item)
                ]
                persona_id = self._fallback(scope_key, candidate_ids)
            decision.persona_id = persona_id
            suffix = "自动选择" if allow_selection else "沿用当前人格"
            decision.match_source = f"{decision.match_source} · {suffix}"
            self._apply_provider_decision(
                event,
                auto_rule=decision.auto_persona,
                persona_id=persona_id,
                fallback_provider_id=decision.provider_id,
            )
        elif decision.provider_id:
            self._apply_provider_id(event, decision.provider_id)

        await self._remember(
            scope_key,
            umo,
            decision,
            persona_id,
        )
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            setter(AUTO_PERSONA_EVENT_KEY, True)
            setter(EFFECTIVE_PERSONA_EVENT_KEY, persona_id)
        return persona_id

    async def remember_effective(
        self,
        event: Any,
        decision: PolicyDecision,
    ) -> None:
        await self._remember(
            self.scope_key(decision),
            self._umo(event),
            decision,
            str(decision.persona_id or "").strip(),
        )

    def session_record(self, session_id: str) -> dict[str, Any]:
        session = self.state.get("sessions", {}).get(str(session_id), {})
        if not isinstance(session, dict):
            return {}
        scope_key = str(session.get("scope_key", "") or "")
        scope = self.state.get("scopes", {}).get(scope_key, {})
        if not isinstance(scope, dict):
            scope = {}
        return {
            **session,
            **scope,
            "scope_key": scope_key,
        }

    def session_record_for_identity(
        self,
        identity: EventIdentity,
    ) -> dict[str, Any]:
        """按会话身份补查记录，兼容主动聊天替换平台前缀。"""

        sessions = self.state.get("sessions", {})
        if not isinstance(sessions, dict):
            return {}
        matches: list[tuple[str, dict[str, Any]]] = []
        for session_id, session in sessions.items():
            if not isinstance(session, dict):
                continue
            if str(session.get("chat_type", "")) != identity.chat_type:
                continue
            if identity.is_private:
                target_matches = (
                    str(session.get("user_id", "")) == identity.user_id
                )
            else:
                target_matches = (
                    str(session.get("group_id", "")) == identity.group_id
                )
            if target_matches:
                matches.append((str(session_id), session))
        if not matches:
            return {}

        platform_matches = [
            item
            for item in matches
            if str(item[1].get("platform", "")) == identity.platform
        ]
        if platform_matches:
            return self.session_record(platform_matches[-1][0])

        scope_keys = {
            str(session.get("scope_key", "") or "")
            for _, session in matches
        }
        if len(scope_keys) == 1:
            return self.session_record(matches[-1][0])
        return {}

    def record_for_decision(self, decision: PolicyDecision) -> dict[str, Any]:
        scope_key = self.scope_key(decision)
        record = self.state.get("scopes", {}).get(scope_key, {})
        if not isinstance(record, dict):
            return {}
        return {**record, "scope_key": scope_key}

    @staticmethod
    def scope_key(decision: PolicyDecision) -> str:
        identity = decision.identity
        platform = identity.platform or "unknown"
        if identity.is_private:
            return f"private:{platform}:{identity.user_id}"
        member_mode = str(
            (decision.user_rule or {}).get("persona_mode")
            or (
                "fixed"
                if (decision.user_rule or {}).get("persona_id")
                else "inherit"
            )
        )
        if member_mode != "inherit":
            return (
                f"member:{platform}:{identity.group_id}:{identity.user_id}"
            )
        return f"group:{platform}:{identity.group_id}"

    async def _select(
        self,
        event: Any,
        scope_key: str,
        auto_rule: dict[str, Any],
        message: str,
    ) -> str:
        candidate_ids = [
            str(item)
            for item in auto_rule.get("persona_ids", [])
            if str(item)
        ]
        if len(candidate_ids) < 2:
            return self._fallback(scope_key, candidate_ids)

        try:
            persona_items = await self.persona_adapter.list_personas()
        except Exception:
            return self._fallback(scope_key, candidate_ids)
        personas = {
            str(item.get("persona_id", "")): item
            for item in persona_items
        }
        candidate_ids = [
            item for item in candidate_ids if item in personas
        ]
        if not candidate_ids:
            return self._fallback(scope_key, [])

        scenario = str(auto_rule.get("scenario", "") or "").strip()
        lines = []
        persona_settings = auto_rule.get("persona_settings", {})
        for index, persona_id in enumerate(candidate_ids, start=1):
            persona = personas[persona_id]
            setting = (
                persona_settings.get(persona_id, {})
                if isinstance(persona_settings, dict)
                else {}
            )
            lines.append(
                f"{index}. ID：{persona_id}\n"
                f"   名称：{persona.get('name') or persona_id}\n"
                f"   人格描述：{setting.get('persona_desc') or persona.get('prompt_summary') or '未填写'}\n"
                f"   适用场景：{setting.get('scenario_desc') or scenario or '未填写'}"
            )
        selector = self.config_getter().get("auto_persona_selector", {})
        extra_prompt = str(selector.get("extra_prompt", "") or "").strip()
        user_prompt = (
            "候选人格：\n"
            + "\n".join(lines)
            + f"\n\n管理员场景说明：{scenario or '无'}"
            + f"\n附加要求：{extra_prompt or '无'}"
            + f"\n\n用户最新消息：\n{message or '（空消息）'}"
        )

        provider_id = str(
            auto_rule.get("selector_provider_id", "")
            or selector.get("provider_id", "")
            or ""
        ).strip()
        if not provider_id:
            getter = getattr(
                self.context,
                "get_current_chat_provider_id",
                None,
            )
            if callable(getter):
                try:
                    try:
                        result = getter(umo=self._umo(event))
                    except TypeError:
                        result = getter(self._umo(event))
                    if inspect.isawaitable(result):
                        result = await result
                    provider_id = str(result or "")
                except Exception:
                    provider_id = ""
        if not provider_id:
            return self._fallback(scope_key, candidate_ids)

        timeout = int(selector.get("timeout_seconds", 8) or 8)
        kwargs = {
            "chat_provider_id": provider_id,
            "prompt": user_prompt,
            "system_prompt": SELECTOR_SYSTEM_PROMPT,
        }
        model = str(selector.get("model", "") or "").strip()
        generator = getattr(self.context, "llm_generate", None)
        if not callable(generator):
            return self._fallback(scope_key, candidate_ids)
        if model:
            try:
                parameters = inspect.signature(generator).parameters
                if "model" in parameters:
                    kwargs["model"] = model
            except (TypeError, ValueError):
                pass
        try:
            response = await asyncio.wait_for(
                generator(**kwargs),
                timeout=timeout,
            )
            raw = str(
                getattr(response, "completion_text", "")
                or getattr(response, "text", "")
                or ""
            ).strip()
            selected = self._parse_response(raw, set(candidate_ids))
            if selected:
                return selected
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        return self._fallback(scope_key, candidate_ids)

    def _apply_provider_decision(
        self,
        event: Any,
        *,
        auto_rule: dict[str, Any],
        persona_id: str,
        fallback_provider_id: str = "",
    ) -> None:
        settings = auto_rule.get("persona_settings", {})
        setting = (
            settings.get(persona_id, {})
            if isinstance(settings, dict)
            else {}
        )
        provider_id = str(
            setting.get("provider_id", "")
            or fallback_provider_id
            or ""
        ).strip()
        self._apply_provider_id(event, provider_id)

    def _apply_provider_id(self, event: Any, provider_id: str) -> None:
        if not provider_id:
            return
        getter = getattr(self.context, "get_provider_by_id", None)
        try:
            provider = getter(provider_id) if callable(getter) else None
        except Exception:
            provider = None
        if provider is None:
            return
        setter = getattr(event, "set_extra", None)
        if not callable(setter):
            return
        setter(SELECTED_PROVIDER_EVENT_KEY, provider_id)
        model_getter = getattr(provider, "get_model", None)
        try:
            model = model_getter() if callable(model_getter) else ""
        except Exception:
            model = ""
        if model:
            setter(SELECTED_MODEL_EVENT_KEY, str(model))

    def _fallback(self, scope_key: str, candidates: list[str]) -> str:
        previous = self.state.get("scopes", {}).get(scope_key, {})
        previous_id = (
            str(previous.get("persona_id", "") or "")
            if isinstance(previous, dict)
            else ""
        )
        if previous_id and (not candidates or previous_id in candidates):
            return previous_id
        return candidates[0] if candidates else ""

    async def _remember(
        self,
        scope_key: str,
        umo: str,
        decision: PolicyDecision,
        persona_id: str,
    ) -> None:
        if not decision.policy_configured and not persona_id:
            return
        async with self.lock:
            scopes = self.state.setdefault("scopes", {})
            scope_record = {
                "persona_id": persona_id,
                "persona_mode": decision.persona_mode,
                "memory_isolation": bool(decision.memory_isolation),
                "livingmemory_isolation": bool(
                    decision.livingmemory_isolation
                ),
            }
            previous_scope = scopes.get(scope_key, {})
            previous_comparable = (
                {
                    key: previous_scope.get(key)
                    for key in scope_record
                }
                if isinstance(previous_scope, dict)
                else {}
            )
            scope_changed = previous_comparable != scope_record
            if scope_changed:
                scopes[scope_key] = {
                    **scope_record,
                    "updated_at": int(time.time()),
                }
            session_changed = False
            if umo:
                identity = decision.identity
                session_record = {
                    "scope_key": scope_key,
                    "platform": identity.platform,
                    "user_id": identity.user_id,
                    "group_id": identity.group_id,
                    "chat_type": identity.chat_type,
                }
                sessions = self.state.setdefault("sessions", {})
                session_changed = sessions.get(umo) != session_record
                if session_changed:
                    sessions[umo] = session_record
            if scope_changed or session_changed:
                self._dirty = True
                self._schedule_write()

    def _schedule_write(self) -> None:
        if self._write_task is None or self._write_task.done():
            self._write_task = asyncio.create_task(self._write_worker())

    async def _write_worker(self) -> None:
        await asyncio.sleep(self._write_delay)
        while True:
            async with self.lock:
                if not self._dirty:
                    return
                snapshot = deepcopy(self.state)
                self._dirty = False
            try:
                await asyncio.to_thread(self._atomic_write, snapshot)
            except OSError:
                async with self.lock:
                    self._dirty = True
                return

    async def flush(self) -> None:
        """等待合并写盘完成，并强制保存尚未落盘的状态。"""

        task = self._write_task
        if task is not None and task is not asyncio.current_task():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        async with self.lock:
            if not self._dirty:
                return
            snapshot = deepcopy(self.state)
            self._dirty = False
        try:
            await asyncio.to_thread(self._atomic_write, snapshot)
        except OSError:
            async with self.lock:
                self._dirty = True

    @staticmethod
    def _parse_response(raw: str, valid_ids: set[str]) -> str:
        candidates = [raw]
        start = raw.find("{")
        end = raw.rfind("}")
        if 0 <= start < end:
            candidates.append(raw[start : end + 1])
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            persona_id = str(payload.get("persona_id", "") or "").strip()
            if persona_id in valid_ids:
                return persona_id
        return ""

    def _atomic_write(self, state: dict[str, Any] | None = None) -> None:
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                state if state is not None else self.state,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    @staticmethod
    def _umo(event: Any) -> str:
        value = getattr(event, "unified_msg_origin", "")
        if callable(value):
            try:
                value = value()
            except Exception:
                value = ""
        return str(value or "")
