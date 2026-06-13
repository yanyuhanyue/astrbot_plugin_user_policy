"""从 AstrBot 现有会话中导入私聊和群聊候选。"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any

from .policy_store import (
    default_plugin_access,
    is_internal_webchat_identifier,
)


UMO_PATTERN = re.compile(
    r"(?P<platform>[^:]+):"
    r"(?P<type>private|friend|user|person|group|"
    r"FriendMessage|PrivateMessage|GroupMessage):"
    r"(?P<id>[^:]+)",
    re.IGNORECASE,
)
SESSION_FIELDS = (
    "current",
    "conversations",
    "conversation_dict",
    "conversation_map",
    "conversation_cache",
    "conversation_store",
    "sessions",
    "session_map",
    "session_dict",
    "session_cache",
    "session_store",
    "cache",
)
SESSION_METHODS = (
    "get_all_conversations",
    "list_conversations",
    "get_conversations",
    "get_all_conversation_ids",
    "list_conversation_ids",
    "get_conversation_ids",
    "get_all_sessions",
    "list_sessions",
    "get_sessions",
    "get_all",
    "list_all",
)


@dataclass(frozen=True)
class SessionImportCandidate:
    target_type: str
    target_id: str
    label: str = ""


class SessionImporter:
    """尽量兼容不同 AstrBot 版本的会话存储结构。"""

    def __init__(self, context: Any):
        self.context = context
        self.diagnostics: list[dict[str, str | int]] = []

    async def collect(self) -> dict[str, list[dict[str, str]]]:
        candidates: set[SessionImportCandidate] = set()
        self.diagnostics = []
        managers = self._candidate_managers()
        if not managers:
            self._record(
                "context",
                "missing",
                "未检测到 AstrBot 会话管理器对象。",
                0,
            )
        for label, manager in managers:
            await self._collect_from_manager(label, manager, candidates)
        private_users = self._dedupe_candidates(
            candidates,
            "private",
            "user_id",
        )
        groups = self._dedupe_candidates(
            candidates,
            "group",
            "group_id",
        )
        return {
            "private_users": private_users,
            "groups": groups,
            "diagnostics": self.diagnostics,
        }

    @staticmethod
    def _dedupe_candidates(
        candidates: set[SessionImportCandidate],
        target_type: str,
        id_key: str,
    ) -> list[dict[str, str]]:
        by_id: dict[str, str] = {}
        for item in sorted(
            (
                item
                for item in candidates
                if item.target_type == target_type
            ),
            key=lambda item: (item.target_id, not bool(item.label), item.label),
        ):
            if item.target_id not in by_id:
                by_id[item.target_id] = item.label or item.target_id
        return [
            {id_key: target_id, "label": label}
            for target_id, label in sorted(by_id.items())
        ]

    def _candidate_managers(self) -> list[tuple[str, Any]]:
        result = []
        seen = set()
        for label, owner in (
            ("context", self.context),
            ("context.provider", getattr(self.context, "provider", None)),
            ("context.core_lifecycle", getattr(self.context, "core_lifecycle", None)),
            ("context.platform_manager", getattr(self.context, "platform_manager", None)),
        ):
            if owner is None:
                continue
            for attr in (
                "",
                "conversation_manager",
                "conversation_mgr",
                "conversation_store",
                "session_manager",
                "session_mgr",
                "session_store",
            ):
                manager = owner if not attr else getattr(owner, attr, None)
                if manager is None:
                    continue
                identity = id(manager)
                if identity in seen:
                    continue
                seen.add(identity)
                result.append((label if not attr else f"{label}.{attr}", manager))
        return result

    async def _collect_from_manager(
        self,
        label: str,
        manager: Any,
        candidates: set[SessionImportCandidate],
    ) -> None:
        if manager is None:
            return
        for attr in SESSION_FIELDS:
            if not hasattr(manager, attr):
                continue
            before = len(candidates)
            try:
                self._collect_from_value(getattr(manager, attr, None), candidates)
            except Exception as exc:
                self._record(
                    f"{label}.{attr}",
                    "error",
                    f"读取失败：{exc}",
                    len(candidates) - before,
                )
                continue
            self._record_source(f"{label}.{attr}", len(candidates) - before)
        for name in SESSION_METHODS:
            getter = getattr(manager, name, None)
            if not callable(getter):
                continue
            if not self._can_call_without_arguments(getter):
                self._record(
                    f"{label}.{name}()",
                    "skipped",
                    "方法需要参数，已跳过。",
                    0,
                )
                continue
            before = len(candidates)
            try:
                value = getter()
                if inspect.isawaitable(value):
                    value = await value
            except Exception:
                self._record(
                    f"{label}.{name}()",
                    "error",
                    "调用失败，已跳过。",
                    0,
                )
                continue
            self._collect_from_value(value, candidates)
            self._record_source(f"{label}.{name}()", len(candidates) - before)

    def _collect_from_value(
        self,
        value: Any,
        candidates: set[SessionImportCandidate],
    ) -> None:
        if value is None:
            return
        if isinstance(value, str):
            self._add_from_unified_origin(value, candidates)
            return
        if isinstance(value, dict):
            self._collect_from_mapping(value, candidates)
            for key, item in value.items():
                self._collect_from_value(str(key), candidates)
                self._collect_from_value(item, candidates)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                self._collect_from_value(item, candidates)
            return
        self._collect_from_object(value, candidates)

    def _collect_from_mapping(
        self,
        value: dict[Any, Any],
        candidates: set[SessionImportCandidate],
    ) -> None:
        for key in (
            "unified_msg_origin",
            "umo",
            "session_id",
            "session",
            "conversation_id",
            "key",
        ):
            self._collect_from_value(value.get(key), candidates)

        group_id = self._first_value(
            value,
            ("group_id", "groupid", "group", "room_id", "channel_id"),
        )
        user_id = self._first_value(
            value,
            ("user_id", "sender_id", "userid", "friend_id", "member_id"),
        )
        chat_type = str(
            self._first_value(value, ("chat_type", "type", "message_type")) or ""
        ).casefold()
        title = str(self._first_value(value, ("title", "name", "label")) or "")
        if group_id:
            self._add_candidate("group", group_id, title, candidates)
            return
        if user_id and self._add_from_unified_origin(
            str(user_id),
            candidates,
            label=title,
        ):
            return
        if user_id and (
            not chat_type
            or chat_type
            in {
                "private",
                "friend",
                "person",
                "user",
                "privatemessage",
                "friendmessage",
            }
        ):
            self._add_candidate("private", user_id, title, candidates)

    def _collect_from_object(
        self,
        value: Any,
        candidates: set[SessionImportCandidate],
    ) -> None:
        for attr in (
            "unified_msg_origin",
            "umo",
            "session_id",
            "session",
            "conversation_id",
            "key",
        ):
            self._collect_from_value(getattr(value, attr, None), candidates)

        group_id = self._first_attr(
            value,
            ("group_id", "groupid", "group", "room_id", "channel_id"),
        )
        user_id = self._first_attr(
            value,
            ("user_id", "sender_id", "userid", "friend_id", "member_id"),
        )
        chat_type = str(self._first_attr(value, ("chat_type", "type")) or "").casefold()
        title = str(self._first_attr(value, ("title", "name")) or "")
        if group_id:
            self._add_candidate("group", group_id, title, candidates)
            return
        if user_id and self._add_from_unified_origin(
            str(user_id),
            candidates,
            label=title,
        ):
            return
        if user_id and (
            not chat_type
            or chat_type
            in {
                "private",
                "friend",
                "person",
                "user",
                "privatemessage",
                "friendmessage",
            }
        ):
            self._add_candidate("private", user_id, title, candidates)

    @staticmethod
    def _first_attr(value: Any, names: tuple[str, ...]) -> Any:
        for name in names:
            item = getattr(value, name, None)
            if item:
                return item
        return None

    @staticmethod
    def _first_value(value: dict[Any, Any], names: tuple[str, ...]) -> Any:
        for name in names:
            item = value.get(name)
            if item:
                return item
        return None

    def _add_from_unified_origin(
        self,
        value: str,
        candidates: set[SessionImportCandidate],
        label: str = "",
    ) -> bool:
        match = UMO_PATTERN.search(value)
        if not match:
            return False
        raw_type = match.group("type").casefold()
        target_type = (
            "group"
            if "group" in raw_type
            else "private"
        )
        target_id = f"{match.group('platform')}:{match.group('id')}"
        self._add_candidate(target_type, target_id, label, candidates)
        return True

    def _add_candidate(
        self,
        target_type: str,
        target_id: Any,
        label: str,
        candidates: set[SessionImportCandidate],
    ) -> None:
        identifier = str(target_id or "").strip()
        if not identifier:
            return
        reason = self._invalid_target_reason(identifier)
        if reason:
            self._record(
                f"candidate.{target_type}",
                "skipped",
                reason,
                0,
            )
            return
        candidates.add(SessionImportCandidate(target_type, identifier, label))

    @staticmethod
    def _invalid_target_reason(identifier: str) -> str:
        if is_internal_webchat_identifier(identifier):
            return f"已跳过 AstrBot 内部 WebChat 会话：{identifier}"
        return ""

    @staticmethod
    def _can_call_without_arguments(getter: Any) -> bool:
        try:
            signature = inspect.signature(getter)
        except (TypeError, ValueError):
            return True
        for parameter in signature.parameters.values():
            if (
                parameter.default is inspect.Parameter.empty
                and parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                }
            ):
                return False
        return True

    def _record_source(self, source: str, count: int) -> None:
        self._record(
            source,
            "found" if count else "empty",
            "扫描到会话。" if count else "该来源未返回会话。",
            count,
        )

    def _record(
        self,
        source: str,
        status: str,
        message: str,
        count: int,
    ) -> None:
        self.diagnostics.append(
            {
                "source": source,
                "status": status,
                "message": message,
                "count": count,
            }
        )


def imported_private_rule() -> dict[str, Any]:
    return {
        "persona_mode": "default",
        "persona_id": "",
        "auto_persona": {"persona_ids": [], "scenario": ""},
        "blocked": False,
        "allow_persona_switch": False,
        "memory_isolation": True,
        "livingmemory_isolation": True,
        "plugin_access": default_plugin_access(),
    }


def imported_group_rule(label: str = "") -> dict[str, Any]:
    return {
        "description": label,
        "persona_mode": "default",
        "persona_id": "",
        "auto_persona": {"persona_ids": [], "scenario": ""},
        "memory_isolation": True,
        "livingmemory_isolation": True,
        "plugin_access": default_plugin_access(),
        "member_access": {"mode": "all", "users": []},
        "policy_admins": [],
        "users": {},
    }
