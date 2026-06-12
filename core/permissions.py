"""群默认人格管理权限的纯函数判断。"""

from __future__ import annotations

from typing import Any


def is_group_manager(
    user_id: str,
    group_rule: dict[str, Any],
    *,
    is_astrbot_admin: bool = False,
    raw_role: str = "",
    group: Any = None,
) -> bool:
    """判断用户是否拥有群策略管理权。"""

    if is_astrbot_admin:
        return True
    if user_id in {
        str(item) for item in group_rule.get("policy_admins", [])
    }:
        return True
    if raw_role in {"owner", "admin"}:
        return True
    if group is None:
        return False

    owner = str(getattr(group, "group_owner", "") or "")
    admins = {
        str(item)
        for item in (getattr(group, "group_admins", None) or [])
    }
    return user_id == owner or user_id in admins
