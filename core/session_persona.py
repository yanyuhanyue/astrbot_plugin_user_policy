"""AstrBot 会话人格诊断与重置。"""

from __future__ import annotations

import inspect
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SessionPersonaDiagnostics:
    """当前 AstrBot 会话的人格来源信息。"""

    unified_msg_origin: str = ""
    conversation_id: str = ""
    conversation_persona_id: str = ""
    default_persona_id: str = ""


@dataclass(frozen=True)
class SessionPersonaResetResult:
    """新建未绑定人格会话的结果。"""

    ok: bool
    message: str
    old_conversation_id: str = ""
    new_conversation_id: str = ""
    copied_messages: int = 0
    conversation_persona_id: str = ""


class AstrBotSessionPersonaManager:
    """只通过 AstrBot 公开对象能力处理当前会话人格。"""

    def __init__(self, context: Any):
        self.context = context

    async def diagnostics(self, unified_msg_origin: str) -> SessionPersonaDiagnostics:
        manager = getattr(self.context, "conversation_manager", None)
        umo = str(unified_msg_origin or "").strip()
        if manager is None or not umo:
            return SessionPersonaDiagnostics(unified_msg_origin=umo)

        conversation_id = await self._current_conversation_id(manager, umo)
        conversation = await self._get_conversation(
            manager,
            umo,
            conversation_id,
        )
        bound_persona = str(
            self._value(conversation, "persona_id", "") or ""
        ).strip()
        default_persona = await self._default_persona_id(umo)
        return SessionPersonaDiagnostics(
            unified_msg_origin=umo,
            conversation_id=conversation_id,
            conversation_persona_id=bound_persona,
            default_persona_id=default_persona,
        )

    async def reset_to_global_default(
        self,
        unified_msg_origin: str,
        platform_id: str = "",
        *,
        copy_history: bool = True,
    ) -> SessionPersonaResetResult:
        """新建一个 persona_id 为 None 的当前会话，使全局默认重新生效。"""

        manager = getattr(self.context, "conversation_manager", None)
        umo = str(unified_msg_origin or "").strip()
        if manager is None:
            return SessionPersonaResetResult(
                False,
                "当前 AstrBot 未提供 conversation_manager。",
            )
        creator = getattr(manager, "new_conversation", None)
        if not callable(creator):
            return SessionPersonaResetResult(
                False,
                "当前 AstrBot 会话管理器不支持新建会话。",
            )
        if not umo:
            return SessionPersonaResetResult(
                False,
                "当前事件缺少 unified_msg_origin，无法定位会话。",
            )

        old_cid = await self._current_conversation_id(manager, umo)
        old_conversation = await self._get_conversation(manager, umo, old_cid)
        history = (
            self._history_from_conversation(old_conversation)
            if copy_history
            else []
        )
        try:
            new_cid, copied_messages = await self._new_conversation(
                creator,
                umo,
                platform_id,
                history,
            )
        except TypeError as exc:
            return SessionPersonaResetResult(
                False,
                f"当前 AstrBot 会话接口不兼容：{exc}",
                old_conversation_id=old_cid,
            )
        except Exception as exc:
            return SessionPersonaResetResult(
                False,
                f"新建未绑定人格会话失败：{exc}",
                old_conversation_id=old_cid,
            )

        new_cid = str(
            self._value(new_cid, "cid", new_cid) or ""
        ).strip()
        if not new_cid:
            return SessionPersonaResetResult(
                False,
                "AstrBot 未返回新会话 ID。",
                old_conversation_id=old_cid,
            )

        switcher = getattr(manager, "switch_conversation", None)
        if callable(switcher):
            try:
                await self._call(switcher, umo, new_cid)
            except Exception:
                pass

        new_conversation = await self._get_conversation(manager, umo, new_cid)
        bound_persona = str(
            self._value(new_conversation, "persona_id", "") or ""
        ).strip()
        if bound_persona:
            return SessionPersonaResetResult(
                False,
                "新会话仍带有 AstrBot 会话绑定人格，当前版本暂无法清除。",
                old_conversation_id=old_cid,
                new_conversation_id=new_cid,
                copied_messages=copied_messages,
                conversation_persona_id=bound_persona,
            )

        return SessionPersonaResetResult(
            True,
            "已新建未绑定人格的当前会话。",
            old_conversation_id=old_cid,
            new_conversation_id=new_cid,
            copied_messages=copied_messages,
        )

    async def _new_conversation(
        self,
        creator: Any,
        umo: str,
        platform_id: str,
        history: list[dict[str, Any]],
    ) -> tuple[Any, int]:
        title = "用户策略：跟随 AstrBot 全局默认人格"
        platform = platform_id or None
        variants = [
            (
                (umo, platform),
                {
                    "content": deepcopy(history),
                    "title": title,
                    "persona_id": None,
                },
                len(history),
            ),
            (
                (umo, platform),
                {"content": deepcopy(history), "title": title},
                len(history),
            ),
            (
                (umo, platform),
                {"content": deepcopy(history), "persona_id": None},
                len(history),
            ),
            ((umo, platform), {"content": deepcopy(history)}, len(history)),
            ((umo, platform), {"persona_id": None}, 0),
            ((umo, platform), {}, 0),
            (
                (umo,),
                {
                    "content": deepcopy(history),
                    "title": title,
                    "persona_id": None,
                },
                len(history),
            ),
            ((umo,), {"content": deepcopy(history)}, len(history)),
            ((umo,), {"persona_id": None}, 0),
            ((umo,), {}, 0),
        ]
        errors = []
        for args, kwargs, copied_messages in variants:
            try:
                result = await self._call(creator, *args, **kwargs)
                return result, copied_messages
            except TypeError as exc:
                errors.append(str(exc))
                continue
        message = errors[-1] if errors else "unknown signature"
        raise TypeError(message)

    async def _default_persona_id(self, umo: str) -> str:
        manager = getattr(self.context, "persona_manager", None)
        getter = getattr(manager, "get_default_persona_v3", None)
        if callable(getter):
            try:
                persona = await self._call(getter, umo)
            except Exception:
                persona = None
            persona_id = str(
                self._value(
                    persona,
                    "persona_id",
                    self._value(persona, "id", ""),
                )
                or ""
            ).strip()
            if persona_id:
                return persona_id

        config_getter = getattr(self.context, "get_config", None)
        if not callable(config_getter):
            return ""
        try:
            config = await self._call(config_getter, umo=umo)
        except TypeError:
            try:
                config = await self._call(config_getter)
            except Exception:
                return ""
        except Exception:
            return ""
        provider_settings = self._value(config, "provider_settings", {})
        return str(
            self._value(provider_settings, "default_personality", "") or ""
        ).strip()

    @staticmethod
    async def _current_conversation_id(manager: Any, umo: str) -> str:
        getter = getattr(manager, "get_curr_conversation_id", None)
        if not callable(getter):
            return ""
        try:
            return str(await AstrBotSessionPersonaManager._call(getter, umo) or "")
        except Exception:
            return ""

    @staticmethod
    async def _get_conversation(
        manager: Any,
        umo: str,
        conversation_id: str,
    ) -> Any:
        if not conversation_id:
            return None
        getter = getattr(manager, "get_conversation", None)
        if not callable(getter):
            return None
        try:
            return await AstrBotSessionPersonaManager._call(
                getter,
                umo,
                conversation_id,
            )
        except Exception:
            return None

    @staticmethod
    async def _call(func: Any, *args: Any, **kwargs: Any) -> Any:
        result = func(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

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
                return deepcopy(decoded)
        return []

    @staticmethod
    def _value(source: Any, name: str, default: Any = None) -> Any:
        if isinstance(source, dict):
            return source.get(name, default)
        return getattr(source, name, default)
