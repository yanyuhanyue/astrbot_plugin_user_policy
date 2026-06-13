"""用户策略的版本化存储、校验与旧配置迁移。"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import yaml


SCHEMA_VERSION = 13
PLUGIN_ACCESS_MODES = {"all", "allowlist", "denylist"}
MEMBER_ACCESS_MODES = {"all", "allowlist", "denylist"}
SCHEDULE_TARGET_TYPES = {"private", "group", "member"}
SCHEDULE_MODES = {"daily", "random_interval", "weekly"}
PERSONA_MODES = {"default", "fixed", "auto"}
MEMBER_PERSONA_MODES = {"inherit", "fixed", "auto"}
MEME_DEFAULT_LIBRARY_ID = "__default__"
MEME_MANAGER_NAMESPACE = "__meme_manager__"
MEME_LIBRARY_NAME_MAX = 60
LIFE_SCHEDULE_LIBRARY_NAME_MAX = 60
LIFE_POOL_KEYS = ("daily_themes", "mood_colors", "outfit_styles", "schedule_types")
LIFE_POOL_ITEM_MAX = 100
LIFE_POOL_LIST_MAX = 200
DEFAULT_LIFE_POOL = {
    "daily_themes": [
        "探索日", "社交日", "宅家日", "工作日", "自我提升日", "休闲放松日",
        "创意日", "运动日", "整理日", "美食日", "文艺日", "随性漫游日",
    ],
    "mood_colors": [
        "慵懒", "活力", "优雅", "俏皮", "神秘", "温柔",
        "冷艳", "甜美", "知性", "随性", "浪漫", "清新",
    ],
    "outfit_styles": [
        "知性学院风", "街头休闲风", "温柔淑女风", "酷飒中性风", "慵懒居家风",
        "精致约会风", "运动活力风", "日系森女风", "法式优雅风", "韩系甜美风",
        "复古文艺风", "极简都市风", "甜酷混搭风", "民族风情风", "暗黑系风格",
    ],
    "schedule_types": [
        "户外活动型", "社交聚会型", "独处充电型", "技能学习型", "随性漫游型",
        "家务整理型", "工作专注型", "休闲娱乐型", "健身运动型", "美食探索型",
        "文化艺术型", "购物采买型",
    ],
}


def is_internal_webchat_identifier(value: Any) -> bool:
    """判断是否为 AstrBot WebChat 内部临时会话标识。"""

    identifier = str(value or "").strip().casefold()
    return (
        identifier.startswith("webchat!astrbot!")
        or ":webchat!astrbot!" in identifier
    )


class PolicyConfigError(ValueError):
    """策略内容不合法。"""


class PolicyConflictError(PolicyConfigError):
    """页面提交的版本已经落后于当前存储版本。"""


def default_plugin_access(*, inherit: bool = False) -> dict[str, Any]:
    return {
        "mode": "inherit" if inherit else "all",
        "plugins": [],
    }


def default_meme_isolation() -> dict[str, Any]:
    return {
        "enabled": True,
        "copy_default_descriptions": True,
    }


def default_private_companion_proactive() -> dict[str, Any]:
    return {
        "enabled": True,
        "image_fast_mode": True,
        "image_debounce_seconds": 1.5,
        "image_vision_timeout_seconds": 15.0,
    }


def default_life_pool() -> dict[str, list[str]]:
    return {key: list(DEFAULT_LIFE_POOL[key]) for key in LIFE_POOL_KEYS}


def default_gitee_aiimg_effects() -> dict[str, Any]:
    return {
        "enabled": False,
        "effects": {},
    }


def default_auto_persona() -> dict[str, Any]:
    return {
        "persona_ids": [],
        "scenario": "",
        "selector_provider_id": "",
        "persona_settings": {},
    }


def default_auto_persona_selector() -> dict[str, Any]:
    return {
        "provider_id": "",
        "model": "",
        "timeout_seconds": 8,
        "extra_prompt": "",
    }


def default_proactive_chat_persona_prompts() -> dict[str, Any]:
    return {"prompts": {}}


def default_policy() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "private_users": {},
        "groups": {},
        "session_import_ignored": {
            "private_users": [],
            "groups": [],
        },
        "schedules": {},
        "meme_manager_isolation": default_meme_isolation(),
        "meme_libraries": {},
        "meme_persona_library_map": {},
        "gitee_aiimg_persona_effects": default_gitee_aiimg_effects(),
        "auto_persona_selector": default_auto_persona_selector(),
        "proactive_chat_persona_prompts": (
            default_proactive_chat_persona_prompts()
        ),
        "life_schedule_libraries": {},
        "life_schedule_persona_map": {},
        "private_companion_proactive": (
            default_private_companion_proactive()
        ),
    }


class PolicyStore:
    """在 AstrBot 插件数据目录中保存直接用户与群聊规则。"""

    def __init__(self, data_dir: Path, legacy_path: Path | None = None):
        self.data_dir = data_dir.resolve()
        self.policy_path = self.data_dir / "policy.json"
        self.legacy_path = legacy_path.resolve() if legacy_path else None
        self._config: dict[str, Any] = default_policy()

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    @property
    def revision(self) -> int:
        return int(self._config.get("revision", 0))

    def load(self) -> dict[str, Any]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.policy_path.exists():
            try:
                raw = json.loads(self.policy_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PolicyConfigError(f"读取策略数据失败：{exc}") from exc
            normalized = self.normalize_and_validate(raw)
            if normalized != raw:
                self._atomic_write(normalized)
            self._config = normalized
            return self._config

        migrated = self._migrate_legacy()
        self._config = self.normalize_and_validate(migrated or default_policy())
        self._atomic_write(self._config)
        return self._config

    def reload(self) -> dict[str, Any]:
        return self.load()

    def update(
        self,
        expected_revision: int,
        mutator: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        if int(expected_revision) != self.revision:
            raise PolicyConflictError(
                "数据已被其他页面更新，请刷新后再修改。"
            )

        candidate = deepcopy(self._config)
        mutator(candidate)
        candidate["revision"] = self.revision + 1
        candidate["schema_version"] = SCHEMA_VERSION
        normalized = self.normalize_and_validate(candidate)
        self._atomic_write(normalized)
        self._config = normalized
        return self._config

    @classmethod
    def normalize_and_validate(cls, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise PolicyConfigError("策略数据必须是对象。")

        result = default_policy()
        try:
            result["revision"] = max(1, int(raw.get("revision", 1)))
        except (TypeError, ValueError) as exc:
            raise PolicyConfigError("策略版本号无效。") from exc

        private_users = raw.get("private_users", {})
        groups = raw.get("groups", {})
        session_import_ignored = raw.get("session_import_ignored", {})
        schedules = raw.get("schedules", {})
        meme_manager_isolation = raw.get("meme_manager_isolation", {})
        meme_libraries = raw.get("meme_libraries", {})
        meme_persona_map = raw.get("meme_persona_library_map", {})
        gitee_aiimg_effects = raw.get("gitee_aiimg_persona_effects", {})
        auto_persona_selector = raw.get("auto_persona_selector", {})
        proactive_chat_prompts = raw.get(
            "proactive_chat_persona_prompts",
            {},
        )
        life_schedule_libraries = raw.get("life_schedule_libraries", {})
        life_schedule_persona_map = raw.get("life_schedule_persona_map", {})
        private_companion_proactive = raw.get(
            "private_companion_proactive",
            {},
        )
        if not isinstance(private_users, dict):
            raise PolicyConfigError("私聊用户数据必须是对象。")
        if not isinstance(groups, dict):
            raise PolicyConfigError("群聊数据必须是对象。")
        if not isinstance(session_import_ignored, dict):
            raise PolicyConfigError("会话导入忽略列表必须是对象。")
        if not isinstance(schedules, dict):
            raise PolicyConfigError("人格计划数据必须是对象。")
        if not isinstance(meme_manager_isolation, dict):
            raise PolicyConfigError("表情包库隔离设置必须是对象。")
        if not isinstance(meme_libraries, dict):
            raise PolicyConfigError("命名表情库数据必须是对象。")
        if not isinstance(meme_persona_map, dict):
            raise PolicyConfigError("人格表情库映射必须是对象。")
        if not isinstance(gitee_aiimg_effects, dict):
            raise PolicyConfigError("Gitee AI Image 人格效果设置必须是对象。")
        if not isinstance(auto_persona_selector, dict):
            raise PolicyConfigError("自动人格选择模型设置必须是对象。")
        if not isinstance(proactive_chat_prompts, dict):
            raise PolicyConfigError("主动消息人格补充要求必须是对象。")
        if not isinstance(life_schedule_libraries, dict):
            raise PolicyConfigError("人格日程库数据必须是对象。")
        if not isinstance(life_schedule_persona_map, dict):
            raise PolicyConfigError("人格日程库映射必须是对象。")
        if not isinstance(private_companion_proactive, dict):
            raise PolicyConfigError("Private Companion 主动对话设置必须是对象。")

        legacy_livingmemory = raw.get("livingmemory_persona_isolation")
        if (
            isinstance(legacy_livingmemory, dict)
            and not bool(legacy_livingmemory.get("enabled", True))
        ):
            private_users, groups = cls._migrate_disabled_livingmemory(
                private_users,
                groups,
            )
        private_users, groups = cls._migrate_unified_sessions_from_private(
            private_users,
            groups,
        )

        result["private_users"] = {
            cls.validate_identifier(user_id, "用户 ID"): cls._normalize_private_rule(
                rule
            )
            for user_id, rule in private_users.items()
            if not is_internal_webchat_identifier(user_id)
        }
        result["groups"] = {
            cls.validate_identifier(group_id, "群 ID"): cls._normalize_group_rule(
                rule
            )
            for group_id, rule in groups.items()
            if not is_internal_webchat_identifier(group_id)
        }
        result["session_import_ignored"] = (
            cls._normalize_session_import_ignored(session_import_ignored)
        )
        result["schedules"] = {
            cls.validate_identifier(
                schedule_id,
                "计划 ID",
            ): cls._normalize_schedule_rule(rule)
            for schedule_id, rule in schedules.items()
            if not cls._schedule_has_internal_target(rule)
        }
        result["meme_manager_isolation"] = cls._normalize_meme_isolation(
            meme_manager_isolation
        )
        result["meme_libraries"] = cls._normalize_meme_libraries(meme_libraries)
        result["meme_persona_library_map"] = cls._normalize_meme_persona_map(
            meme_persona_map,
            result["meme_libraries"],
        )
        result["gitee_aiimg_persona_effects"] = cls._normalize_gitee_aiimg_effects(
            gitee_aiimg_effects
        )
        result["auto_persona_selector"] = cls._normalize_auto_persona_selector(
            auto_persona_selector
        )
        result["proactive_chat_persona_prompts"] = (
            cls._normalize_proactive_chat_persona_prompts(
                proactive_chat_prompts
            )
        )
        result["life_schedule_libraries"] = (
            cls._normalize_life_schedule_libraries(life_schedule_libraries)
        )
        result["life_schedule_persona_map"] = (
            cls._normalize_life_schedule_persona_map(
                life_schedule_persona_map,
                result["life_schedule_libraries"],
            )
        )
        result["private_companion_proactive"] = (
            cls._normalize_private_companion_proactive(
                private_companion_proactive
            )
        )
        return result

    @staticmethod
    def validate_identifier(value: Any, label: str) -> str:
        identifier = str(value or "").strip()
        if not identifier:
            raise PolicyConfigError(f"{label}不能为空。")
        if len(identifier) > 128:
            raise PolicyConfigError(f"{label}不能超过 128 个字符。")
        if any(ord(char) < 32 for char in identifier):
            raise PolicyConfigError(f"{label}不能包含控制字符。")
        if is_internal_webchat_identifier(identifier):
            raise PolicyConfigError(
                f"{label}不能使用 AstrBot WebChat 内部临时会话标识。"
            )
        return identifier

    @classmethod
    def normalize_private_rule(cls, rule: Any) -> dict[str, Any]:
        return cls._normalize_private_rule(rule)

    @classmethod
    def normalize_group_rule(cls, rule: Any) -> dict[str, Any]:
        return cls._normalize_group_rule(rule)

    @classmethod
    def normalize_member_rule(cls, rule: Any) -> dict[str, Any]:
        return cls._normalize_member_rule(rule)

    @classmethod
    def normalize_schedule_rule(cls, rule: Any) -> dict[str, Any]:
        return cls._normalize_schedule_rule(rule)

    @classmethod
    def normalize_meme_isolation(cls, rule: Any) -> dict[str, Any]:
        return cls._normalize_meme_isolation(rule)

    @classmethod
    def _normalize_private_rule(cls, rule: Any) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise PolicyConfigError("私聊用户规则必须是对象。")
        persona_id = cls._string(rule.get("persona_id", ""), "人格 ID")
        persona_mode = cls._normalize_persona_mode(
            rule.get("persona_mode"),
            persona_id,
            allow_inherit=False,
        )
        return {
            "persona_mode": persona_mode,
            "persona_id": persona_id if persona_mode == "fixed" else "",
            "provider_id": cls._string(
                rule.get("provider_id", ""),
                "回复模型",
                maximum=200,
            ),
            "auto_persona": cls._normalize_auto_persona(
                rule.get("auto_persona"),
                required=persona_mode == "auto",
            ),
            "blocked": bool(rule.get("blocked", False)),
            "allow_persona_switch": bool(
                rule.get("allow_persona_switch", False)
            ),
            "memory_isolation": bool(rule.get("memory_isolation", True)),
            "livingmemory_isolation": bool(
                rule.get("livingmemory_isolation", True)
            ),
            "private_companion_proactive": bool(
                rule.get("private_companion_proactive", True)
            ),
            "plugin_access": cls._normalize_plugin_access(
                rule.get("plugin_access"),
                allow_inherit=False,
            ),
        }

    @classmethod
    def _normalize_group_rule(cls, rule: Any) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise PolicyConfigError("群聊规则必须是对象。")

        member_access = rule.get("member_access", {})
        if not isinstance(member_access, dict):
            raise PolicyConfigError("群成员访问规则必须是对象。")
        member_mode = str(member_access.get("mode", "all")).strip()
        if member_mode not in MEMBER_ACCESS_MODES:
            raise PolicyConfigError("群成员访问模式无效。")

        users = rule.get("users", {})
        if not isinstance(users, dict):
            raise PolicyConfigError("群成员个性设置必须是对象。")

        persona_id = cls._string(rule.get("persona_id", ""), "人格 ID")
        persona_mode = cls._normalize_persona_mode(
            rule.get("persona_mode"),
            persona_id,
            allow_inherit=False,
        )
        return {
            "description": cls._string(
                rule.get("description", ""),
                "群聊备注",
                maximum=200,
            ),
            "persona_mode": persona_mode,
            "persona_id": persona_id if persona_mode == "fixed" else "",
            "provider_id": cls._string(
                rule.get("provider_id", ""),
                "回复模型",
                maximum=200,
            ),
            "auto_persona": cls._normalize_auto_persona(
                rule.get("auto_persona"),
                required=persona_mode == "auto",
            ),
            "memory_isolation": bool(rule.get("memory_isolation", True)),
            "livingmemory_isolation": bool(
                rule.get("livingmemory_isolation", True)
            ),
            "plugin_access": cls._normalize_plugin_access(
                rule.get("plugin_access"),
                allow_inherit=False,
            ),
            "member_access": {
                "mode": member_mode,
                "users": cls._normalize_id_list(
                    member_access.get("users", []),
                    "群成员名单",
                ),
            },
            "policy_admins": cls._normalize_id_list(
                rule.get("policy_admins", []),
                "策略管理员",
            ),
            "users": {
                cls.validate_identifier(user_id, "群成员 ID"):
                cls._normalize_member_rule(user_rule)
                for user_id, user_rule in users.items()
                if not is_internal_webchat_identifier(user_id)
            },
        }

    @classmethod
    def _normalize_member_rule(cls, rule: Any) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise PolicyConfigError("群成员个性设置必须是对象。")
        memory_isolation = rule.get("memory_isolation")
        if memory_isolation is not None and not isinstance(
            memory_isolation,
            bool,
        ):
            raise PolicyConfigError("群成员记忆隔离设置必须是开启、关闭或沿用群聊。")
        livingmemory_isolation = rule.get("livingmemory_isolation")
        if livingmemory_isolation is not None and not isinstance(
            livingmemory_isolation,
            bool,
        ):
            raise PolicyConfigError(
                "群成员 LivingMemory 隔离设置必须是开启、关闭或沿用群聊。"
            )
        persona_id = cls._string(rule.get("persona_id", ""), "人格 ID")
        persona_mode = cls._normalize_persona_mode(
            rule.get("persona_mode"),
            persona_id,
            allow_inherit=True,
        )
        return {
            "persona_mode": persona_mode,
            "persona_id": persona_id if persona_mode == "fixed" else "",
            "provider_id": cls._string(
                rule.get("provider_id", ""),
                "回复模型",
                maximum=200,
            ),
            "auto_persona": cls._normalize_auto_persona(
                rule.get("auto_persona"),
                required=persona_mode == "auto",
            ),
            "allow_persona_switch": bool(
                rule.get("allow_persona_switch", False)
            ),
            "memory_isolation": memory_isolation,
            "livingmemory_isolation": livingmemory_isolation,
            "plugin_access": cls._normalize_plugin_access(
                rule.get("plugin_access"),
                allow_inherit=True,
            ),
        }

    @classmethod
    def _normalize_schedule_rule(cls, rule: Any) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise PolicyConfigError("人格计划必须是对象。")

        target_type = str(rule.get("target_type", "") or "").strip()
        if target_type not in SCHEDULE_TARGET_TYPES:
            raise PolicyConfigError("人格计划的目标类型无效。")
        user_id = cls._string(
            rule.get("user_id", ""),
            "用户 ID",
            maximum=128,
        )
        group_id = cls._string(
            rule.get("group_id", ""),
            "群 ID",
            maximum=128,
        )
        if target_type == "private" and not user_id:
            raise PolicyConfigError("私聊人格计划必须选择用户。")
        if target_type == "group" and not group_id:
            raise PolicyConfigError("群聊人格计划必须选择群聊。")
        if target_type == "member" and (not group_id or not user_id):
            raise PolicyConfigError("群成员人格计划必须选择群聊和成员。")
        if is_internal_webchat_identifier(user_id) or (
            is_internal_webchat_identifier(group_id)
        ):
            raise PolicyConfigError(
                "人格计划不能使用 AstrBot WebChat 内部临时会话。"
            )

        mode = str(rule.get("mode", "") or "").strip()
        if mode not in SCHEDULE_MODES:
            raise PolicyConfigError("人格计划的执行方式无效。")

        daily_time = str(rule.get("daily_time", "") or "").strip()
        weekly_time = str(rule.get("weekly_time", "") or "").strip()
        persona_id = cls._string(
            rule.get("persona_id", ""),
            "人格 ID",
        )
        persona_ids = cls._normalize_string_list(
            rule.get("persona_ids", []),
            "随机人格范围",
        )
        weekly_rules = cls._normalize_weekly_rules(
            rule.get("weekly_rules", []),
        )
        try:
            interval_minutes = int(rule.get("interval_minutes", 60))
        except (TypeError, ValueError) as exc:
            raise PolicyConfigError("随机切换间隔必须是整数分钟。") from exc

        if mode == "daily":
            cls._validate_time(daily_time, "每天执行时间")
            if not persona_id:
                raise PolicyConfigError("每天定时切换必须选择人格。")
            persona_ids = []
            weekly_time = ""
            weekly_rules = []
        elif mode == "random_interval":
            daily_time = ""
            weekly_time = ""
            weekly_rules = []
            persona_id = ""
            if interval_minutes < 5 or interval_minutes > 10080:
                raise PolicyConfigError(
                    "随机切换间隔必须在 5 分钟到 7 天之间。"
                )
            if len(persona_ids) < 2:
                raise PolicyConfigError("随机切换范围至少选择两个人格。")
        else:
            daily_time = ""
            persona_id = ""
            persona_ids = []
            cls._validate_time(weekly_time, "每周执行时间")
            if not weekly_rules:
                raise PolicyConfigError("每周切换至少设置一个星期的人格。")

        return {
            "name": cls._string(
                rule.get("name", ""),
                "计划名称",
                maximum=100,
            ),
            "enabled": bool(rule.get("enabled", True)),
            "target_type": target_type,
            "user_id": user_id,
            "group_id": group_id,
            "mode": mode,
            "daily_time": daily_time,
            "weekly_time": weekly_time,
            "interval_minutes": interval_minutes,
            "persona_id": persona_id,
            "persona_ids": persona_ids,
            "weekly_rules": weekly_rules,
        }

    @classmethod
    def _normalize_meme_isolation(cls, rule: Any) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise PolicyConfigError("表情包库隔离设置必须是对象。")
        return {
            "enabled": True,
            "copy_default_descriptions": bool(
                rule.get("copy_default_descriptions", True)
            ),
        }

    @classmethod
    def normalize_gitee_aiimg_effects(cls, rule: Any) -> dict[str, Any]:
        return cls._normalize_gitee_aiimg_effects(rule)

    @classmethod
    def normalize_auto_persona_selector(cls, rule: Any) -> dict[str, Any]:
        return cls._normalize_auto_persona_selector(rule)

    @classmethod
    def normalize_proactive_chat_persona_prompts(
        cls,
        rule: Any,
    ) -> dict[str, Any]:
        return cls._normalize_proactive_chat_persona_prompts(rule)

    @classmethod
    def normalize_life_schedule_libraries(
        cls,
        value: Any,
    ) -> dict[str, Any]:
        return cls._normalize_life_schedule_libraries(value)

    @classmethod
    def normalize_life_schedule_persona_map(
        cls,
        value: Any,
        libraries: dict[str, Any],
    ) -> dict[str, Any]:
        return cls._normalize_life_schedule_persona_map(value, libraries)

    @classmethod
    def normalize_meme_libraries(cls, value: Any) -> dict[str, Any]:
        return cls._normalize_meme_libraries(value)

    @classmethod
    def normalize_meme_persona_map(
        cls,
        value: Any,
        libraries: dict[str, Any],
    ) -> dict[str, Any]:
        return cls._normalize_meme_persona_map(value, libraries)

    @classmethod
    def _normalize_meme_libraries(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise PolicyConfigError("命名表情库数据必须是对象。")
        result: dict[str, Any] = {}
        for lib_id, meta in value.items():
            key = cls.validate_identifier(lib_id, "表情库 ID")
            if key in {MEME_DEFAULT_LIBRARY_ID, MEME_MANAGER_NAMESPACE, "default"}:
                raise PolicyConfigError("表情库 ID 不能使用保留值。")
            if not isinstance(meta, dict):
                raise PolicyConfigError("表情库定义必须是对象。")
            name = cls._string(
                meta.get("name", ""),
                "表情库名称",
                maximum=MEME_LIBRARY_NAME_MAX,
            )
            if not name:
                raise PolicyConfigError("表情库名称不能为空。")
            result[key] = {"name": name}
        return result

    @classmethod
    def _normalize_meme_persona_map(
        cls,
        value: Any,
        libraries: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise PolicyConfigError("人格表情库映射必须是对象。")
        result: dict[str, Any] = {}
        for persona_id, lib_id in value.items():
            pid = cls._string(str(persona_id), "人格 ID", maximum=200)
            target = str(lib_id or "").strip()
            if not pid or not target:
                continue
            if target in {MEME_DEFAULT_LIBRARY_ID, MEME_MANAGER_NAMESPACE, "default"}:
                continue
            if target not in libraries:
                continue
            result[pid] = target
        return result

    @classmethod
    def _normalize_gitee_aiimg_effects(cls, rule: Any) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise PolicyConfigError("Gitee AI Image 人格效果设置必须是对象。")
        effects = rule.get("effects", {})
        if not isinstance(effects, dict):
            raise PolicyConfigError("Gitee AI Image 人格效果列表必须是对象。")
        normalized = {}
        for persona_id, effect in effects.items():
            key = cls._string(str(persona_id), "人格 ID", maximum=200)
            text = cls._string(effect, "人格生图效果", maximum=1000)
            if key and text:
                normalized[key] = text
        return {
            "enabled": bool(rule.get("enabled", False)),
            "effects": normalized,
        }

    @classmethod
    def _normalize_persona_mode(
        cls,
        raw_mode: Any,
        persona_id: str,
        *,
        allow_inherit: bool,
    ) -> str:
        if raw_mode is None:
            if persona_id:
                return "fixed"
            return "inherit" if allow_inherit else "default"
        mode = str(raw_mode or "").strip()
        allowed = MEMBER_PERSONA_MODES if allow_inherit else PERSONA_MODES
        if mode not in allowed:
            raise PolicyConfigError("人格选择模式无效。")
        if mode == "fixed" and not persona_id:
            raise PolicyConfigError("固定人格模式必须选择人格。")
        return mode

    @classmethod
    def _normalize_auto_persona(
        cls,
        value: Any,
        *,
        required: bool,
    ) -> dict[str, Any]:
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise PolicyConfigError("自动人格设置必须是对象。")
        persona_ids = cls._normalize_string_list(
            value.get("persona_ids", []),
            "自动人格候选范围",
        )
        if required and len(persona_ids) < 2:
            raise PolicyConfigError("自动切换人格至少选择两个人格。")
        settings = value.get("persona_settings", {})
        if not isinstance(settings, dict):
            raise PolicyConfigError("自动人格模型配置必须是对象。")
        normalized_settings: dict[str, Any] = {}
        for persona_id, item in settings.items():
            key = cls._string(
                str(persona_id),
                "人格 ID",
                maximum=200,
            )
            if key not in persona_ids or not isinstance(item, dict):
                continue
            normalized_settings[key] = {
                "provider_id": cls._string(
                    item.get("provider_id", ""),
                    "人格回复模型",
                    maximum=200,
                ),
                "persona_desc": cls._string(
                    item.get("persona_desc", ""),
                    "人格描述",
                    maximum=2000,
                ),
                "scenario_desc": cls._string(
                    item.get("scenario_desc", ""),
                    "人格适用场景",
                    maximum=2000,
                ),
            }
        return {
            "persona_ids": persona_ids,
            "scenario": cls._string(
                value.get("scenario", ""),
                "自动人格场景说明",
                maximum=2000,
            ),
            "selector_provider_id": cls._string(
                value.get("selector_provider_id", ""),
                "用户自动人格选择模型",
                maximum=200,
            ),
            "persona_settings": normalized_settings,
        }

    @classmethod
    def _normalize_auto_persona_selector(
        cls,
        rule: Any,
    ) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise PolicyConfigError("自动人格选择模型设置必须是对象。")
        try:
            timeout = int(rule.get("timeout_seconds", 8))
        except (TypeError, ValueError) as exc:
            raise PolicyConfigError("自动人格判断超时时间必须是整数。") from exc
        if timeout < 2 or timeout > 30:
            raise PolicyConfigError("自动人格判断超时时间必须在 2 到 30 秒之间。")
        return {
            "provider_id": cls._string(
                rule.get("provider_id", ""),
                "自动人格 Provider",
                maximum=200,
            ),
            "model": cls._string(
                rule.get("model", ""),
                "自动人格模型",
                maximum=200,
            ),
            "timeout_seconds": timeout,
            "extra_prompt": cls._string(
                rule.get("extra_prompt", ""),
                "自动人格附加要求",
                maximum=2000,
            ),
        }

    @classmethod
    def _normalize_proactive_chat_persona_prompts(
        cls,
        rule: Any,
    ) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise PolicyConfigError("主动消息人格补充要求必须是对象。")
        prompts = rule.get("prompts", {})
        if not isinstance(prompts, dict):
            raise PolicyConfigError("主动消息人格补充要求列表必须是对象。")
        normalized: dict[str, str] = {}
        for persona_id, prompt in prompts.items():
            key = cls._string(str(persona_id), "人格 ID", maximum=200)
            text = cls._string(
                prompt,
                "主动消息补充要求",
                maximum=2000,
            )
            if key and text:
                normalized[key] = text
        return {"prompts": normalized}

    @classmethod
    def _normalize_life_schedule_libraries(
        cls,
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise PolicyConfigError("人格日程库数据必须是对象。")
        result: dict[str, Any] = {}
        for library_id, library in value.items():
            key = cls.validate_identifier(library_id, "日程库 ID")
            if not isinstance(library, dict):
                raise PolicyConfigError("人格日程库定义必须是对象。")
            name = cls._string(
                library.get("name", ""),
                "日程库名称",
                maximum=LIFE_SCHEDULE_LIBRARY_NAME_MAX,
            )
            if not name:
                raise PolicyConfigError("日程库名称不能为空。")
            records = library.get("records", {})
            if not isinstance(records, dict):
                raise PolicyConfigError("日程库记录必须是对象。")
            normalized_records: dict[str, Any] = {}
            for date, record in records.items():
                date_key = cls._normalize_date(date)
                if not isinstance(record, dict):
                    raise PolicyConfigError("日程记录必须是对象。")
                status = str(record.get("status", "ok") or "ok").strip()
                if status not in {"ok", "failed"}:
                    raise PolicyConfigError("日程记录状态无效。")
                normalized_records[date_key] = {
                    "date": date_key,
                    "outfit_style": cls._string(
                        record.get("outfit_style", ""),
                        "穿搭风格",
                        maximum=500,
                    ),
                    "outfit": cls._string(
                        record.get("outfit", ""),
                        "穿搭内容",
                        maximum=4000,
                    ),
                    "schedule": cls._string(
                        record.get("schedule", ""),
                        "日程内容",
                        maximum=10000,
                    ),
                    "status": status,
                }
            result[key] = {
                "name": name,
                "records": normalized_records,
                "pool": cls._normalize_life_pool(library.get("pool")),
            }
        return result

    @classmethod
    def normalize_life_pool(cls, value: Any) -> dict[str, list[str]]:
        return cls._normalize_life_pool(value)

    @classmethod
    def _normalize_life_pool(cls, value: Any) -> dict[str, list[str]]:
        # value 整体为 None → 全部用参考插件默认池；提供了 dict（哪怕缺某池）→
        # 缺失的池按空处理，便于「新建日程库四池为空」。
        if value is None:
            return default_life_pool()
        if not isinstance(value, dict):
            raise PolicyConfigError("创意池必须是对象。")
        result: dict[str, list[str]] = {}
        for key in LIFE_POOL_KEYS:
            items = value.get(key)
            if items is None:
                result[key] = []
                continue
            normalized = cls._normalize_string_list(items, "创意池条目")
            normalized = [
                item[:LIFE_POOL_ITEM_MAX] for item in normalized
            ][:LIFE_POOL_LIST_MAX]
            result[key] = normalized
        return result

    @classmethod
    def normalize_private_companion_proactive(
        cls,
        value: Any,
    ) -> dict[str, Any]:
        return cls._normalize_private_companion_proactive(value)

    @classmethod
    def _normalize_private_companion_proactive(
        cls,
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise PolicyConfigError(
                "Private Companion 主动对话设置必须是对象。"
            )
        try:
            debounce_seconds = float(
                value.get("image_debounce_seconds", 1.5)
            )
            vision_timeout_seconds = float(
                value.get("image_vision_timeout_seconds", 15.0)
            )
        except (TypeError, ValueError) as exc:
            raise PolicyConfigError(
                "Private Companion 识图等待时间必须是数字。"
            ) from exc
        if debounce_seconds < 0 or debounce_seconds > 10:
            raise PolicyConfigError(
                "Private Companion 图片防抖必须在 0 到 10 秒之间。"
            )
        if vision_timeout_seconds < 3 or vision_timeout_seconds > 30:
            raise PolicyConfigError(
                "Private Companion 视觉等待必须在 3 到 30 秒之间。"
            )
        return {
            "enabled": bool(value.get("enabled", True)),
            "image_fast_mode": bool(
                value.get("image_fast_mode", True)
            ),
            "image_debounce_seconds": debounce_seconds,
            "image_vision_timeout_seconds": vision_timeout_seconds,
        }

    @staticmethod
    def _migrate_disabled_livingmemory(
        private_users: dict[str, Any],
        groups: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        migrated_private = deepcopy(private_users)
        migrated_groups = deepcopy(groups)
        for rule in migrated_private.values():
            if isinstance(rule, dict):
                rule["livingmemory_isolation"] = False
        for group in migrated_groups.values():
            if not isinstance(group, dict):
                continue
            group["livingmemory_isolation"] = False
            users = group.get("users", {})
            if not isinstance(users, dict):
                continue
            for member in users.values():
                if isinstance(member, dict):
                    member["livingmemory_isolation"] = False
        return migrated_private, migrated_groups

    @classmethod
    def _migrate_unified_sessions_from_private(
        cls,
        private_users: dict[str, Any],
        groups: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        migrated_private = deepcopy(private_users)
        migrated_groups = deepcopy(groups)
        for user_id, rule in list(migrated_private.items()):
            parsed = cls._parse_unified_identifier(str(user_id))
            if parsed is None:
                continue
            target_id = parsed["scoped_id"]
            if parsed["target_type"] == "group":
                migrated_private.pop(user_id, None)
                if target_id not in migrated_groups:
                    migrated_groups[target_id] = cls._group_rule_from_private(rule)
                continue
            migrated_private.pop(user_id, None)
            if target_id not in migrated_private:
                migrated_private[target_id] = rule
        return migrated_private, migrated_groups

    @classmethod
    def _group_rule_from_private(cls, rule: Any) -> dict[str, Any]:
        if not isinstance(rule, dict):
            rule = {}
        persona_id = cls._string(
            str(rule.get("persona_id", "") or ""),
            "人格 ID",
        )
        return {
            "description": "从私聊列表迁移的群聊",
            "persona_mode": cls._string(
                str(
                    rule.get("persona_mode")
                    or ("fixed" if persona_id else "default")
                ),
                "人格选择模式",
            ),
            "persona_id": persona_id,
            "provider_id": cls._string(
                str(rule.get("provider_id", "") or ""),
                "回复模型",
            ),
            "auto_persona": deepcopy(rule.get("auto_persona", {})),
            "memory_isolation": bool(rule.get("memory_isolation", True)),
            "livingmemory_isolation": bool(
                rule.get("livingmemory_isolation", True)
            ),
            "plugin_access": deepcopy(
                rule.get("plugin_access", default_plugin_access())
            ),
            "member_access": {"mode": "all", "users": []},
            "policy_admins": [],
            "users": {},
        }

    @staticmethod
    def _parse_unified_identifier(value: str) -> dict[str, str] | None:
        raw = str(value or "").strip()
        for message_type in (
            "FriendMessage",
            "PrivateMessage",
            "GroupMessage",
            "GuildMessage",
        ):
            anchor = f":{message_type}:"
            index = raw.find(anchor)
            if index < 0:
                continue
            target_id = raw[index + len(anchor):].strip()
            if not target_id:
                return None
            lowered = message_type.lower()
            platform = raw[:index].strip()
            return {
                "target_type": (
                    "group"
                    if "group" in lowered or "guild" in lowered
                    else "private"
                ),
                "target_id": target_id,
                "platform": platform,
                "scoped_id": f"{platform}:{target_id}" if platform else target_id,
            }
        return None

    @classmethod
    def _normalize_life_schedule_persona_map(
        cls,
        value: Any,
        libraries: dict[str, Any],
    ) -> dict[str, str]:
        if not isinstance(value, dict):
            raise PolicyConfigError("人格日程库映射必须是对象。")
        result: dict[str, str] = {}
        for persona_id, library_id in value.items():
            pid = cls._string(str(persona_id), "人格 ID", maximum=200)
            target = str(library_id or "").strip()
            if pid and target in libraries:
                result[pid] = target
        return result

    @staticmethod
    def _normalize_date(value: Any) -> str:
        import datetime

        text = str(value or "").strip()
        try:
            return datetime.date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise PolicyConfigError("日程日期必须使用 YYYY-MM-DD 格式。") from exc

    @classmethod
    def _normalize_plugin_access(
        cls,
        value: Any,
        *,
        allow_inherit: bool,
    ) -> dict[str, Any]:
        if value is None:
            return default_plugin_access(inherit=allow_inherit)
        if not isinstance(value, dict):
            raise PolicyConfigError("插件权限必须是对象。")

        default_mode = "inherit" if allow_inherit else "all"
        mode = str(value.get("mode", default_mode)).strip()
        allowed_modes = set(PLUGIN_ACCESS_MODES)
        if allow_inherit:
            allowed_modes.add("inherit")
        if mode not in allowed_modes:
            raise PolicyConfigError("插件权限模式无效。")

        plugins = value.get("plugins", [])
        if not isinstance(plugins, list):
            raise PolicyConfigError("插件权限列表必须是数组。")
        normalized_plugins = []
        for plugin_name in plugins:
            name = cls._string(plugin_name, "插件名称", maximum=200)
            if name and name not in normalized_plugins:
                normalized_plugins.append(name)
        if mode in {"all", "inherit"}:
            normalized_plugins = []
        return {"mode": mode, "plugins": normalized_plugins}

    @classmethod
    def _normalize_id_list(cls, values: Any, label: str) -> list[str]:
        if not isinstance(values, list):
            raise PolicyConfigError(f"{label}必须是数组。")
        result = []
        for value in values:
            if is_internal_webchat_identifier(value):
                continue
            identifier = cls.validate_identifier(value, label)
            if identifier not in result:
                result.append(identifier)
        return result

    @staticmethod
    def _schedule_has_internal_target(rule: Any) -> bool:
        if not isinstance(rule, dict):
            return False
        return is_internal_webchat_identifier(
            rule.get("user_id")
        ) or is_internal_webchat_identifier(rule.get("group_id"))

    @classmethod
    def _normalize_session_import_ignored(cls, value: Any) -> dict[str, list[str]]:
        if not isinstance(value, dict):
            raise PolicyConfigError("会话导入忽略列表必须是对象。")
        return {
            "private_users": cls._normalize_id_list(
                value.get("private_users", []),
                "忽略导入私聊用户",
            ),
            "groups": cls._normalize_id_list(
                value.get("groups", []),
                "忽略导入群聊",
            ),
        }

    @classmethod
    def _normalize_string_list(
        cls,
        values: Any,
        label: str,
    ) -> list[str]:
        if not isinstance(values, list):
            raise PolicyConfigError(f"{label}必须是数组。")
        result = []
        for value in values:
            item = cls._string(value, label)
            if item and item not in result:
                result.append(item)
        return result

    @classmethod
    def _normalize_weekly_rules(cls, values: Any) -> list[dict[str, Any]]:
        if not isinstance(values, list):
            raise PolicyConfigError("每周人格安排必须是数组。")
        result = []
        seen = set()
        for item in values:
            if not isinstance(item, dict):
                raise PolicyConfigError("每周人格安排必须是对象数组。")
            try:
                weekday = int(item.get("weekday"))
            except (TypeError, ValueError) as exc:
                raise PolicyConfigError("星期必须是 0 到 6 的整数。") from exc
            if weekday < 0 or weekday > 6:
                raise PolicyConfigError("星期必须是 0 到 6 的整数。")
            persona_id = cls._string(item.get("persona_id", ""), "人格 ID")
            enabled = bool(item.get("enabled", True)) and bool(persona_id)
            if not enabled or weekday in seen:
                continue
            seen.add(weekday)
            result.append(
                {
                    "weekday": weekday,
                    "enabled": True,
                    "persona_id": persona_id,
                }
            )
        return sorted(result, key=lambda item: item["weekday"])

    @staticmethod
    def _validate_time(value: str, label: str) -> None:
        parts = value.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise PolicyConfigError(f"{label}必须使用 HH:MM 格式。")
        hour, minute = (int(part) for part in parts)
        if hour > 23 or minute > 59:
            raise PolicyConfigError(f"{label}无效。")

    @staticmethod
    def _string(value: Any, label: str, maximum: int = 200) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise PolicyConfigError(f"{label}必须是字符串。")
        result = value.strip()
        if len(result) > maximum:
            raise PolicyConfigError(f"{label}不能超过 {maximum} 个字符。")
        return result

    def _atomic_write(self, config: dict[str, Any]) -> None:
        temporary = self.policy_path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.policy_path)
        except OSError as exc:
            raise PolicyConfigError(f"保存策略数据失败：{exc}") from exc

    def _migrate_legacy(self) -> dict[str, Any] | None:
        path = self.legacy_path
        if path is None or not path.is_file():
            return None
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None
        if not isinstance(raw, dict):
            return None

        profiles = raw.get("profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
        private_users: dict[str, Any] = {}
        global_blacklist = {
            str(item) for item in raw.get("blacklist_users", [])
        }

        legacy_private = raw.get("private_users", {})
        if isinstance(legacy_private, dict):
            for user_id, rule in legacy_private.items():
                identifier = str(user_id)
                resolved = self._resolve_legacy_profile(profiles, rule)
                private_users[identifier] = {
                    "persona_id": str(resolved.get("persona_id", "") or ""),
                    "blocked": identifier in global_blacklist,
                    "allow_persona_switch": False,
                    "memory_isolation": True,
                    "plugin_access": self._legacy_plugin_access(resolved),
                }
        for user_id in global_blacklist:
            private_users.setdefault(
                user_id,
                {
                    "persona_id": "",
                    "blocked": True,
                    "allow_persona_switch": False,
                    "memory_isolation": True,
                    "plugin_access": default_plugin_access(),
                },
            )

        migrated_groups: dict[str, Any] = {}
        legacy_groups = raw.get("groups", {})
        if isinstance(legacy_groups, dict):
            for group_id, group_rule in legacy_groups.items():
                if not isinstance(group_rule, dict):
                    continue
                group_profile = self._resolve_legacy_profile(
                    profiles,
                    {"profile": group_rule.get("default_profile", "")},
                )
                group_access = self._combine_plugin_access(
                    self._legacy_plugin_access(group_profile),
                    self._legacy_plugin_access(group_rule),
                )
                users = {}
                legacy_users = group_rule.get("users", {})
                if isinstance(legacy_users, dict):
                    for user_id, user_rule in legacy_users.items():
                        resolved = self._resolve_legacy_profile(
                            profiles,
                            user_rule,
                        )
                        member_access = self._combine_plugin_access(
                            self._legacy_plugin_access(resolved),
                            group_access,
                        )
                        users[str(user_id)] = {
                            "persona_id": str(
                                resolved.get("persona_id", "") or ""
                            ),
                            "allow_persona_switch": False,
                            "memory_isolation": None,
                            "plugin_access": member_access,
                        }

                migrated_groups[str(group_id)] = {
                    "description": str(
                        group_rule.get("description", "") or ""
                    ),
                    "persona_id": str(
                        group_profile.get("persona_id", "") or ""
                    ),
                    "memory_isolation": True,
                    "plugin_access": group_access,
                    "member_access": {
                        "mode": (
                            "denylist"
                            if group_rule.get("blacklist_users")
                            else "all"
                        ),
                        "users": [
                            str(item)
                            for item in group_rule.get(
                                "blacklist_users",
                                [],
                            )
                        ],
                    },
                    "policy_admins": [],
                    "users": users,
                }

        return {
            "schema_version": SCHEMA_VERSION,
            "revision": 1,
            "private_users": private_users,
            "groups": migrated_groups,
            "schedules": {},
            "meme_manager_isolation": default_meme_isolation(),
            "meme_libraries": {},
            "meme_persona_library_map": {},
            "gitee_aiimg_persona_effects": default_gitee_aiimg_effects(),
            "private_companion_proactive": (
                default_private_companion_proactive()
            ),
        }

    @staticmethod
    def _resolve_legacy_profile(
        profiles: dict[str, Any],
        rule: Any,
    ) -> dict[str, Any]:
        if not isinstance(rule, dict):
            rule = {}
        profile_name = str(rule.get("profile", "") or "")
        profile = profiles.get(profile_name, {})
        resolved = deepcopy(profile) if isinstance(profile, dict) else {}
        for key, value in rule.items():
            if key != "profile":
                resolved[key] = deepcopy(value)
        return resolved

    @staticmethod
    def _legacy_plugin_access(rule: Any) -> dict[str, Any]:
        if not isinstance(rule, dict):
            return default_plugin_access()
        allowed = {
            str(item)
            for item in rule.get("allowed_plugins", ["*"])
        }
        disabled = {
            str(item)
            for item in rule.get("disabled_plugins", [])
        }
        if "*" in disabled:
            return {"mode": "allowlist", "plugins": []}
        if "*" in allowed:
            if disabled:
                return {
                    "mode": "denylist",
                    "plugins": sorted(disabled),
                }
            return default_plugin_access()
        return {
            "mode": "allowlist",
            "plugins": sorted(allowed - disabled),
        }

    @staticmethod
    def _combine_plugin_access(
        first: dict[str, Any],
        second: dict[str, Any],
    ) -> dict[str, Any]:
        first_mode = first.get("mode", "all")
        second_mode = second.get("mode", "all")
        first_items = set(first.get("plugins", []))
        second_items = set(second.get("plugins", []))

        if first_mode == "all":
            return deepcopy(second)
        if second_mode == "all":
            return deepcopy(first)
        if first_mode == "denylist" and second_mode == "denylist":
            return {
                "mode": "denylist",
                "plugins": sorted(first_items | second_items),
            }
        if first_mode == "allowlist" and second_mode == "allowlist":
            return {
                "mode": "allowlist",
                "plugins": sorted(first_items & second_items),
            }
        if first_mode == "allowlist":
            return {
                "mode": "allowlist",
                "plugins": sorted(first_items - second_items),
            }
        return {
            "mode": "allowlist",
            "plugins": sorted(second_items - first_items),
        }
