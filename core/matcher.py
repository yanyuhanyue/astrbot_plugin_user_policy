"""私聊用户、群聊与群成员的直接策略匹配。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from .policy_store import default_plugin_access


def _string_key(mapping: Any, key: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    if key in mapping:
        return mapping[key]
    for candidate, value in mapping.items():
        if str(candidate) == key:
            return value
    return None


@dataclass(frozen=True)
class EventIdentity:
    platform: str
    user_id: str
    group_id: str = ""
    chat_type: str = "private"
    role: str = "member"

    @property
    def is_private(self) -> bool:
        return self.chat_type == "private"


def parse_session_identity(session_id: str) -> EventIdentity | None:
    """从 AstrBot UMO 中提取私聊用户或群聊目标。"""

    if not isinstance(session_id, str):
        return None
    parsed: tuple[str, str, str] | None = None
    for message_type in (
        "FriendMessage",
        "GroupMessage",
        "PrivateMessage",
        "GuildMessage",
    ):
        anchor = f":{message_type}:"
        index = session_id.find(anchor)
        if index >= 0:
            parsed = (
                session_id[:index],
                message_type,
                session_id[index + len(anchor) :],
            )
            break
    if parsed is None:
        parts = session_id.split(":")
        if len(parts) == 3:
            parsed = (parts[0], parts[1], parts[2])
        elif len(parts) > 3:
            parsed = (
                ":".join(parts[:-2]),
                parts[-2],
                parts[-1],
            )
    if parsed is None:
        return None

    platform, message_type, target_id = parsed
    lowered_type = message_type.lower()
    if "friend" in lowered_type or "private" in lowered_type:
        return EventIdentity(
            platform=platform,
            user_id=target_id,
            chat_type="private",
        )
    if "group" in lowered_type or "guild" in lowered_type:
        return EventIdentity(
            platform=platform,
            user_id="",
            group_id=target_id,
            chat_type="group",
        )
    return None


@dataclass
class PolicyDecision:
    identity: EventIdentity
    persona_id: str
    persona_mode: str
    auto_persona: dict[str, Any]
    plugin_access: dict[str, Any]
    match_source: str
    is_blocked: bool
    allow_persona_switch: bool
    memory_isolation: bool
    livingmemory_isolation: bool
    policy_configured: bool
    group_rule: dict[str, Any]
    user_rule: dict[str, Any]
    provider_id: str = ""


@dataclass(frozen=True)
class PermissionResult:
    allowed: bool
    reason: str = ""


class PolicyMatcher:
    """按成员、群聊、会话默认的顺序生成本次事件策略。"""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def match(self, identity: EventIdentity) -> PolicyDecision:
        if identity.is_private:
            return self._match_private(identity)
        return self._match_group(identity)

    def match_private_aliases(
        self,
        identity: EventIdentity,
        aliases: list[str] | tuple[str, ...],
    ) -> PolicyDecision:
        """主发送者未配置时，尝试会话目标等私聊别名。"""

        decision = self.match(identity)
        if not identity.is_private or decision.policy_configured:
            return decision
        seen = {identity.user_id}
        for alias in aliases:
            user_id = str(alias or "").strip()
            if not user_id or user_id in seen:
                continue
            seen.add(user_id)
            candidate = self.match(replace(identity, user_id=user_id))
            if candidate.policy_configured:
                return candidate
        return decision

    def match_group_aliases(
        self,
        identity: EventIdentity,
        aliases: list[str] | tuple[str, ...],
    ) -> PolicyDecision:
        """优先尝试完整会话等精确群聊别名，再回退到群号。"""

        if identity.is_private:
            return self.match(identity)
        seen = set()
        for alias in [*aliases, identity.group_id]:
            group_id = str(alias or "").strip()
            if not group_id or group_id in seen:
                continue
            seen.add(group_id)
            candidate = self.match(replace(identity, group_id=group_id))
            if candidate.policy_configured:
                return candidate
        return self.match(identity)

    def check_plugin_access(
        self,
        decision: PolicyDecision,
        plugin_name: str,
    ) -> PermissionResult:
        if decision.is_blocked:
            return PermissionResult(False, "当前用户已被禁止使用机器人")

        access = decision.plugin_access or default_plugin_access()
        mode = str(access.get("mode", "all"))
        plugins = {str(item) for item in access.get("plugins", [])}
        if mode == "allowlist" and plugin_name not in plugins:
            return PermissionResult(False, "当前规则未允许该插件")
        if mode == "denylist" and plugin_name in plugins:
            return PermissionResult(False, "当前规则已禁用该插件")
        return PermissionResult(True)

    def _match_private(self, identity: EventIdentity) -> PolicyDecision:
        private_users = self.config.get("private_users", {})
        rule = _string_key(private_users, identity.user_id)
        if not isinstance(rule, dict):
            rule = {}
        has_rule = bool(rule)
        persona_mode = str(
            rule.get("persona_mode")
            or ("fixed" if rule.get("persona_id") else "default")
        )
        return PolicyDecision(
            identity=identity,
            persona_id=(
                str(rule.get("persona_id", "") or "")
                if persona_mode == "fixed"
                else ""
            ),
            persona_mode=persona_mode,
            provider_id=str(rule.get("provider_id", "") or ""),
            auto_persona=deepcopy(rule.get("auto_persona", {})),
            plugin_access=deepcopy(
                rule.get("plugin_access", default_plugin_access())
            ),
            match_source="私聊用户设置" if has_rule else "AstrBot 会话默认",
            is_blocked=bool(rule.get("blocked", False)),
            allow_persona_switch=bool(
                rule.get("allow_persona_switch", False)
            ),
            memory_isolation=(
                bool(rule.get("memory_isolation", True))
                if has_rule
                else False
            ),
            livingmemory_isolation=(
                bool(rule.get("livingmemory_isolation", True))
                if has_rule
                else False
            ),
            policy_configured=has_rule,
            group_rule={},
            user_rule=deepcopy(rule),
        )

    def _match_group(self, identity: EventIdentity) -> PolicyDecision:
        groups = self.config.get("groups", {})
        group_rule = _string_key(groups, identity.group_id)
        if not isinstance(group_rule, dict):
            group_rule = {}

        users = group_rule.get("users", {})
        member_rule = _string_key(users, identity.user_id)
        if not isinstance(member_rule, dict):
            member_rule = {}
        private_rule = _string_key(
            self.config.get("private_users", {}),
            identity.user_id,
        )
        if not isinstance(private_rule, dict):
            private_rule = {}

        group_mode = str(
            group_rule.get("persona_mode")
            or ("fixed" if group_rule.get("persona_id") else "default")
        )
        member_mode = str(
            member_rule.get("persona_mode")
            or ("fixed" if member_rule.get("persona_id") else "inherit")
        )
        if member_mode != "inherit":
            persona_mode = member_mode
            persona_id = (
                str(member_rule.get("persona_id", "") or "")
                if member_mode == "fixed"
                else ""
            )
            auto_persona = deepcopy(member_rule.get("auto_persona", {}))
            source = "群成员个性设置"
            provider_id = str(member_rule.get("provider_id", "") or "")
        elif group_rule:
            persona_mode = group_mode
            persona_id = (
                str(group_rule.get("persona_id", "") or "")
                if group_mode == "fixed"
                else ""
            )
            auto_persona = deepcopy(group_rule.get("auto_persona", {}))
            source = "群聊默认设置"
            provider_id = str(group_rule.get("provider_id", "") or "")
            effective_user_rule = member_rule
        elif private_rule:
            persona_mode = str(
                private_rule.get("persona_mode")
                or (
                    "fixed"
                    if private_rule.get("persona_id")
                    else "default"
                )
            )
            persona_id = (
                str(private_rule.get("persona_id", "") or "")
                if persona_mode == "fixed"
                else ""
            )
            auto_persona = deepcopy(
                private_rule.get("auto_persona", {})
            )
            source = "私聊用户设置（群聊回退）"
            provider_id = str(private_rule.get("provider_id", "") or "")
            effective_user_rule = private_rule
        else:
            persona_mode = "default"
            persona_id = ""
            auto_persona = {}
            source = "AstrBot 会话默认"
            provider_id = ""
            effective_user_rule = member_rule
        if member_mode != "inherit":
            effective_user_rule = member_rule

        member_plugins = member_rule.get(
            "plugin_access",
            default_plugin_access(inherit=True),
        )
        if member_plugins.get("mode") == "inherit":
            plugin_access = group_rule.get(
                "plugin_access",
                default_plugin_access(),
            )
        else:
            plugin_access = member_plugins

        member_memory = member_rule.get("memory_isolation")
        if not group_rule and not member_rule and private_rule:
            member_memory = private_rule.get("memory_isolation")
        group_memory = (
            bool(group_rule.get("memory_isolation", True))
            if group_rule
            else False
        )
        memory_isolation = (
            group_memory
            if member_memory is None
            else bool(member_memory)
        )
        member_livingmemory = member_rule.get("livingmemory_isolation")
        if not group_rule and not member_rule and private_rule:
            member_livingmemory = private_rule.get(
                "livingmemory_isolation"
            )
        group_livingmemory = (
            bool(group_rule.get("livingmemory_isolation", True))
            if group_rule
            else False
        )
        livingmemory_isolation = (
            group_livingmemory
            if member_livingmemory is None
            else bool(member_livingmemory)
        )

        return PolicyDecision(
            identity=identity,
            persona_id=persona_id,
            persona_mode=persona_mode,
            provider_id=provider_id,
            auto_persona=auto_persona,
            plugin_access=deepcopy(plugin_access),
            match_source=source,
            is_blocked=not self._member_is_allowed(
                identity.user_id,
                group_rule,
            ),
            allow_persona_switch=bool(
                member_rule.get("allow_persona_switch", False)
            ),
            memory_isolation=memory_isolation,
            livingmemory_isolation=livingmemory_isolation,
            policy_configured=bool(group_rule or private_rule),
            group_rule={
                key: deepcopy(value)
                for key, value in group_rule.items()
                if key != "users"
            },
            user_rule=deepcopy(effective_user_rule),
        )

    @staticmethod
    def _member_is_allowed(
        user_id: str,
        group_rule: dict[str, Any],
    ) -> bool:
        member_access = group_rule.get("member_access", {})
        if not isinstance(member_access, dict):
            return True
        mode = str(member_access.get("mode", "all"))
        users = {str(item) for item in member_access.get("users", [])}
        if mode == "allowlist":
            return user_id in users
        if mode == "denylist":
            return user_id not in users
        return True
