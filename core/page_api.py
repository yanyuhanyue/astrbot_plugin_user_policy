"""AstrBot 插件页面使用的 3.0 结构化管理接口。"""

from __future__ import annotations

import logging
from datetime import date as date_type
from functools import wraps
from typing import Any
from uuid import uuid4

from quart import request

from .meme_library import MemePersonaLibraryManager
from .policy_store import (
    MEME_DEFAULT_LIBRARY_ID,
    PolicyConfigError,
    PolicyConflictError,
    PolicyStore,
    default_life_pool,
)
from .session_importer import imported_group_rule, imported_private_rule


PLUGIN_NAME = "astrbot_plugin_user_policy"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}"
log = logging.getLogger(__name__)


class PluginPageApi:
    """注册不暴露原始配置文件的多人格管理接口。"""

    def __init__(self, plugin: Any):
        self.plugin = plugin

    def register_routes(self) -> None:
        routes = (
            ("bootstrap", self.bootstrap, ["GET"], "读取多人格管理页面数据"),
            ("sessions/import-preview", self.session_import_preview, ["GET"], "预览已有会话导入"),
            ("sessions/import", self.import_sessions, ["POST"], "导入已有会话"),
            ("users/save", self.save_user, ["POST"], "保存私聊用户"),
            ("users/delete", self.delete_user, ["POST"], "删除私聊用户"),
            ("groups/save", self.save_group, ["POST"], "保存群聊"),
            ("groups/delete", self.delete_group, ["POST"], "删除群聊"),
            (
                "groups/users/save",
                self.save_group_user,
                ["POST"],
                "保存群成员个性设置",
            ),
            (
                "groups/users/delete",
                self.delete_group_user,
                ["POST"],
                "删除群成员个性设置",
            ),
            (
                "schedules/save",
                self.save_schedule,
                ["POST"],
                "保存人格计划",
            ),
            (
                "schedules/delete",
                self.delete_schedule,
                ["POST"],
                "删除人格计划",
            ),
            (
                "integrations/meme-isolation/save",
                self.save_meme_isolation,
                ["POST"],
                "保存表情包库隔离设置",
            ),
            (
                "integrations/gitee-aiimg-effects/save",
                self.save_gitee_aiimg_effects,
                ["POST"],
                "保存 Gitee AI Image 人格效果设置",
            ),
            (
                "integrations/auto-persona/save",
                self.save_auto_persona_selector,
                ["POST"],
                "保存自动人格选择模型设置",
            ),
            (
                "integrations/proactive-chat/save",
                self.save_proactive_chat_prompts,
                ["POST"],
                "保存主动消息人格补充要求",
            ),
            (
                "integrations/private-companion-proactive/save",
                self.save_private_companion_proactive,
                ["POST"],
                "保存 Private Companion 主动对话设置",
            ),
            (
                "life-schedule/library/create",
                self.create_life_schedule_library,
                ["POST"],
                "创建人格日程库",
            ),
            (
                "life-schedule/library/rename",
                self.rename_life_schedule_library,
                ["POST"],
                "重命名人格日程库",
            ),
            (
                "life-schedule/library/delete",
                self.delete_life_schedule_library,
                ["POST"],
                "删除人格日程库",
            ),
            (
                "life-schedule/record/save",
                self.save_life_schedule_record,
                ["POST"],
                "保存人格日程记录",
            ),
            (
                "life-schedule/record/delete",
                self.delete_life_schedule_record,
                ["POST"],
                "删除人格日程记录",
            ),
            (
                "life-schedule/persona-map/save",
                self.save_life_schedule_persona_map,
                ["POST"],
                "保存人格日程库映射",
            ),
            (
                "life-schedule/pool/save",
                self.save_life_schedule_pool,
                ["POST"],
                "保存人格日程库创意池",
            ),
            (
                "meme-library/bootstrap",
                self.meme_library_bootstrap,
                ["GET"],
                "读取 Meme Manager 人格图库",
            ),
            (
                "meme-library/load",
                self.load_meme_library,
                ["POST"],
                "切换 Meme Manager 人格图库",
            ),
            (
                "meme-library/category/save",
                self.save_meme_category,
                ["POST"],
                "保存 Meme Manager 人格图库分类",
            ),
            (
                "meme-library/category/delete",
                self.delete_meme_category,
                ["POST"],
                "删除 Meme Manager 人格图库分类",
            ),
            (
                "meme-library/images/upload",
                self.upload_meme_images,
                ["POST"],
                "上传 Meme Manager 人格图库图片",
            ),
            (
                "meme-library/images/delete",
                self.delete_meme_images,
                ["POST"],
                "删除 Meme Manager 人格图库图片",
            ),
            (
                "meme-library/images/move",
                self.move_meme_images,
                ["POST"],
                "移动 Meme Manager 人格图库图片",
            ),
            (
                "meme-library/images/copy",
                self.copy_meme_images,
                ["POST"],
                "复制 Meme Manager 人格图库图片",
            ),
            (
                "meme-library/images/preview",
                self.meme_image_preview,
                ["POST"],
                "读取单张表情预览",
            ),
            (
                "meme-library/library/create",
                self.create_meme_library,
                ["POST"],
                "新建命名表情库",
            ),
            (
                "meme-library/library/rename",
                self.rename_meme_library,
                ["POST"],
                "重命名命名表情库",
            ),
            (
                "meme-library/library/delete",
                self.delete_meme_library,
                ["POST"],
                "删除命名表情库",
            ),
            (
                "meme-library/library/copy",
                self.copy_meme_library,
                ["POST"],
                "复制命名表情库",
            ),
            (
                "meme-library/persona-map/save",
                self.save_meme_persona_map,
                ["POST"],
                "设置人格表情库映射",
            ),
        )
        for endpoint, handler, methods, description in routes:
            self.plugin.context.register_web_api(
                f"{PAGE_API_PREFIX}/{endpoint}",
                self._guard(handler, description),
                methods,
                description,
            )

    async def bootstrap(self):
        store = self.plugin.store
        if store is None:
            return self._error("策略数据尚未加载，请先重新加载插件。", 503)

        warnings = []
        try:
            personas = await self.plugin.personas.list_personas()
        except Exception as exc:
            log.exception("读取 AstrBot 人格列表失败")
            personas = []
            warnings.append(f"读取 AstrBot 人格列表失败：{exc}")

        try:
            plugins = self.plugin.plugin_catalog_for_ui()
        except Exception as exc:
            log.exception("读取 AstrBot 插件列表失败")
            plugins = []
            warnings.append(f"读取 AstrBot 插件列表失败：{exc}")

        try:
            integrations = self.plugin.integrations.report()
        except Exception as exc:
            log.exception("读取插件兼容状态失败")
            integrations = {"items": [], "diagnostics": []}
            warnings.append(f"读取插件兼容状态失败：{exc}")

        config = store.config
        # 首次没有任何日程库时，自动创建一个预填默认创意池的「默认日程库」。
        if not config.get("life_schedule_libraries"):
            try:
                def _seed(cfg: dict[str, Any]) -> None:
                    libraries = cfg.setdefault("life_schedule_libraries", {})
                    if not libraries:
                        libraries[uuid4().hex] = {
                            "name": "默认日程库",
                            "records": {},
                            "pool": default_life_pool(),
                        }
                config = await self.plugin.update_policy(store.revision, _seed)
            except Exception as exc:
                log.warning("创建默认日程库失败：%s", exc)
                config = store.config
        providers = self._provider_catalog()
        missing_personas = self._missing_personas(config, personas)
        scheduler = getattr(self.plugin, "persona_scheduler", None)
        schedule_status = (
            scheduler.status_for_ui()
            if scheduler is not None
            else {}
        )
        return self._ok(
            {
                "revision": store.revision,
                "personas_available": self.plugin.personas.available,
                "personas": personas,
                "plugins": plugins,
                "private_users": [
                    {"user_id": user_id, **rule}
                    for user_id, rule in sorted(
                        config.get("private_users", {}).items()
                    )
                ],
                "groups": [
                    {"group_id": group_id, **rule}
                    for group_id, rule in sorted(
                        config.get("groups", {}).items()
                    )
                ],
                "schedules": [
                    {
                        "schedule_id": schedule_id,
                        **rule,
                        "runtime": schedule_status.get(schedule_id, {}),
                    }
                    for schedule_id, rule in sorted(
                        config.get("schedules", {}).items()
                    )
                ],
                "meme_manager_isolation": config.get(
                    "meme_manager_isolation",
                    {},
                ),
                "meme_libraries": config.get("meme_libraries", {}),
                "meme_persona_library_map": config.get(
                    "meme_persona_library_map",
                    {},
                ),
                "gitee_aiimg_persona_effects": config.get(
                    "gitee_aiimg_persona_effects",
                    {},
                ),
                "private_companion_proactive": config.get(
                    "private_companion_proactive",
                    {},
                ),
                "providers": providers,
                "auto_persona_selector": config.get(
                    "auto_persona_selector",
                    {},
                ),
                "proactive_chat_persona_prompts": config.get(
                    "proactive_chat_persona_prompts",
                    {},
                ),
                "life_schedule_libraries": config.get(
                    "life_schedule_libraries",
                    {},
                ),
                "life_schedule_persona_map": config.get(
                    "life_schedule_persona_map",
                    {},
                ),
                "integrations": integrations,
                "missing_personas": sorted(missing_personas),
                "warnings": warnings,
            }
        )

    async def save_user(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        user_id = PolicyStore.validate_identifier(
            payload.get("user_id"),
            "用户 ID",
        )
        rule = PolicyStore.normalize_private_rule(payload.get("rule"))
        await self._validate_persona_rule(rule)

        def mutate(config: dict[str, Any]) -> None:
            config["private_users"][user_id] = rule

        config = await self.plugin.update_policy(revision, mutate)
        return self._saved(config, "私聊用户已保存。")

    async def session_import_preview(self):
        candidates = await self.plugin.session_importer.collect()
        store = self.plugin.store
        if store is None:
            raise PolicyConfigError("策略数据尚未加载。")
        existing_users = store.config.get("private_users", {})
        existing_groups = store.config.get("groups", {})
        private_candidates = candidates.get("private_users", [])
        group_candidates = candidates.get("groups", [])
        importable_users = [
            item
            for item in private_candidates
            if item.get("user_id") not in existing_users
        ]
        importable_groups = [
            item
            for item in group_candidates
            if item.get("group_id") not in existing_groups
        ]
        return self._ok(
            {
                "private_users": importable_users,
                "groups": importable_groups,
                "existing_private_users": len(private_candidates) - len(importable_users),
                "existing_groups": len(group_candidates) - len(importable_groups),
                "discovered_private_users": len(private_candidates),
                "discovered_groups": len(group_candidates),
                "diagnostics": candidates.get("diagnostics", []),
            }
        )

    async def import_sessions(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        candidates = await self.plugin.session_importer.collect()

        def mutate(config: dict[str, Any]) -> None:
            for item in candidates.get("private_users", []):
                user_id = str(item.get("user_id", "") or "").strip()
                if user_id and user_id not in config["private_users"]:
                    config["private_users"][user_id] = imported_private_rule()
            for item in candidates.get("groups", []):
                group_id = str(item.get("group_id", "") or "").strip()
                if group_id and group_id not in config["groups"]:
                    config["groups"][group_id] = imported_group_rule(
                        str(item.get("label", "") or "")
                    )

        before_users = set(self.plugin.store.config.get("private_users", {}))
        before_groups = set(self.plugin.store.config.get("groups", {}))
        config = await self.plugin.update_policy(revision, mutate)
        added_users = set(config.get("private_users", {})) - before_users
        added_groups = set(config.get("groups", {})) - before_groups
        return self._ok(
            {
                "message": (
                    f"已导入 {len(added_users)} 个私聊用户、"
                    f"{len(added_groups)} 个群聊。"
                ),
                "revision": int(config.get("revision", 0)),
                "added_private_users": len(added_users),
                "added_groups": len(added_groups),
                "discovered_private_users": len(candidates.get("private_users", [])),
                "discovered_groups": len(candidates.get("groups", [])),
                "diagnostics": candidates.get("diagnostics", []),
            }
        )

    async def delete_user(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        user_id = PolicyStore.validate_identifier(
            payload.get("user_id"),
            "用户 ID",
        )

        def mutate(config: dict[str, Any]) -> None:
            config["private_users"].pop(user_id, None)

        config = await self.plugin.update_policy(revision, mutate)
        return self._saved(config, "私聊用户已删除。")

    async def save_group(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        group_id = PolicyStore.validate_identifier(
            payload.get("group_id"),
            "群 ID",
        )
        rule = PolicyStore.normalize_group_rule(payload.get("rule"))
        await self._validate_persona_rule(rule)
        for member in rule.get("users", {}).values():
            await self._validate_persona_rule(member)

        def mutate(config: dict[str, Any]) -> None:
            config["groups"][group_id] = rule

        config = await self.plugin.update_policy(revision, mutate)
        return self._saved(config, "群聊设置已保存。")

    async def delete_group(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        group_id = PolicyStore.validate_identifier(
            payload.get("group_id"),
            "群 ID",
        )

        def mutate(config: dict[str, Any]) -> None:
            config["groups"].pop(group_id, None)

        config = await self.plugin.update_policy(revision, mutate)
        return self._saved(config, "群聊设置已删除。")

    async def save_group_user(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        group_id = PolicyStore.validate_identifier(
            payload.get("group_id"),
            "群 ID",
        )
        user_id = PolicyStore.validate_identifier(
            payload.get("user_id"),
            "群成员 ID",
        )
        rule = PolicyStore.normalize_member_rule(payload.get("rule"))
        await self._validate_persona_rule(rule)

        def mutate(config: dict[str, Any]) -> None:
            group = config["groups"].get(group_id)
            if not isinstance(group, dict):
                raise PolicyConfigError("群聊不存在，请先添加群聊。")
            group["users"][user_id] = rule

        config = await self.plugin.update_policy(revision, mutate)
        return self._saved(config, "群成员个性设置已保存。")

    async def delete_group_user(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        group_id = PolicyStore.validate_identifier(
            payload.get("group_id"),
            "群 ID",
        )
        user_id = PolicyStore.validate_identifier(
            payload.get("user_id"),
            "群成员 ID",
        )

        def mutate(config: dict[str, Any]) -> None:
            group = config["groups"].get(group_id)
            if isinstance(group, dict):
                group.get("users", {}).pop(user_id, None)

        config = await self.plugin.update_policy(revision, mutate)
        return self._saved(config, "群成员个性设置已删除。")

    async def save_schedule(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        raw_schedule_id = str(payload.get("schedule_id", "") or "").strip()
        schedule_id = (
            PolicyStore.validate_identifier(raw_schedule_id, "计划 ID")
            if raw_schedule_id
            else uuid4().hex
        )
        rule = PolicyStore.normalize_schedule_rule(payload.get("rule"))
        await self._validate_schedule_references(rule)

        def mutate(config: dict[str, Any]) -> None:
            config["schedules"][schedule_id] = rule

        config = await self.plugin.update_policy(revision, mutate)
        return self._saved(config, "人格计划已保存。")

    async def delete_schedule(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        schedule_id = PolicyStore.validate_identifier(
            payload.get("schedule_id"),
            "计划 ID",
        )

        def mutate(config: dict[str, Any]) -> None:
            config["schedules"].pop(schedule_id, None)

        config = await self.plugin.update_policy(revision, mutate)
        return self._saved(config, "人格计划已删除。")

    async def save_meme_isolation(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        rule = PolicyStore.normalize_meme_isolation(payload.get("rule"))

        def mutate(config: dict[str, Any]) -> None:
            config["meme_manager_isolation"] = rule

        config = await self.plugin.update_policy(revision, mutate)
        return self._saved(config, "表情包库隔离设置已保存。")

    async def save_gitee_aiimg_effects(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        rule = PolicyStore.normalize_gitee_aiimg_effects(payload.get("rule"))

        def mutate(config: dict[str, Any]) -> None:
            config["gitee_aiimg_persona_effects"] = rule

        config = await self.plugin.update_policy(revision, mutate)
        return self._saved(config, "Gitee AI Image 人格效果已保存。")

    async def save_auto_persona_selector(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        rule = PolicyStore.normalize_auto_persona_selector(
            payload.get("rule")
        )

        def mutate(config: dict[str, Any]) -> None:
            config["auto_persona_selector"] = rule

        config = await self.plugin.update_policy(revision, mutate)
        return self._saved(config, "自动人格选择模型设置已保存。")

    async def save_proactive_chat_prompts(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        rule = PolicyStore.normalize_proactive_chat_persona_prompts(
            payload.get("rule")
        )

        def mutate(config: dict[str, Any]) -> None:
            config["proactive_chat_persona_prompts"] = rule

        config = await self.plugin.update_policy(revision, mutate)
        return self._saved(config, "主动消息人格补充要求已保存。")

    async def save_private_companion_proactive(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        rule = PolicyStore.normalize_private_companion_proactive(
            payload.get("rule")
        )

        def mutate(config: dict[str, Any]) -> None:
            config["private_companion_proactive"] = rule

        config = await self.plugin.update_policy(revision, mutate)
        return self._saved(config, "Private Companion 主动对话设置已保存。")

    async def create_life_schedule_library(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        name = self._life_library_name(payload.get("name"))
        library_id = uuid4().hex

        def mutate(config: dict[str, Any]) -> None:
            config.setdefault("life_schedule_libraries", {})[
                library_id
            ] = {
                "name": name,
                "records": {},
                "pool": {
                    "daily_themes": [],
                    "mood_colors": [],
                    "outfit_styles": [],
                    "schedule_types": [],
                },
            }

        config = await self.plugin.update_policy(revision, mutate)
        return self._life_saved(
            config,
            f"日程库「{name}」已创建。",
            library_id=library_id,
        )

    async def rename_life_schedule_library(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        library_id = PolicyStore.validate_identifier(
            payload.get("library_id"),
            "日程库 ID",
        )
        name = self._life_library_name(payload.get("name"))

        def mutate(config: dict[str, Any]) -> None:
            library = config.get("life_schedule_libraries", {}).get(
                library_id
            )
            if not isinstance(library, dict):
                raise PolicyConfigError("日程库不存在。")
            library["name"] = name

        config = await self.plugin.update_policy(revision, mutate)
        return self._life_saved(config, "日程库已重命名。")

    async def delete_life_schedule_library(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        library_id = PolicyStore.validate_identifier(
            payload.get("library_id"),
            "日程库 ID",
        )

        def mutate(config: dict[str, Any]) -> None:
            libraries = config.get("life_schedule_libraries", {})
            if library_id not in libraries:
                raise PolicyConfigError("日程库不存在。")
            libraries.pop(library_id, None)
            mapping = config.get("life_schedule_persona_map", {})
            for persona_id, target in list(mapping.items()):
                if target == library_id:
                    mapping.pop(persona_id, None)

        config = await self.plugin.update_policy(revision, mutate)
        return self._life_saved(config, "日程库已删除。")

    async def save_life_schedule_record(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        library_id = PolicyStore.validate_identifier(
            payload.get("library_id"),
            "日程库 ID",
        )
        record = self._life_record(payload.get("record"))

        def mutate(config: dict[str, Any]) -> None:
            library = config.get("life_schedule_libraries", {}).get(
                library_id
            )
            if not isinstance(library, dict):
                raise PolicyConfigError("日程库不存在。")
            library.setdefault("records", {})[record["date"]] = record

        config = await self.plugin.update_policy(revision, mutate)
        return self._life_saved(config, "日程记录已保存。")

    async def delete_life_schedule_record(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        library_id = PolicyStore.validate_identifier(
            payload.get("library_id"),
            "日程库 ID",
        )
        date = self._date(payload.get("date"))

        def mutate(config: dict[str, Any]) -> None:
            library = config.get("life_schedule_libraries", {}).get(
                library_id
            )
            if not isinstance(library, dict):
                raise PolicyConfigError("日程库不存在。")
            library.setdefault("records", {}).pop(date, None)

        config = await self.plugin.update_policy(revision, mutate)
        return self._life_saved(config, "日程记录已删除。")

    async def save_life_schedule_persona_map(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        persona_id = str(payload.get("persona_id", "") or "").strip()
        library_id = str(payload.get("library_id", "") or "").strip()
        if not persona_id:
            raise PolicyConfigError("缺少人格 ID。")
        await self._validate_persona_ids({persona_id})

        def mutate(config: dict[str, Any]) -> None:
            mapping = config.setdefault("life_schedule_persona_map", {})
            if not library_id:
                mapping.pop(persona_id, None)
                return
            if library_id not in config.get(
                "life_schedule_libraries",
                {},
            ):
                raise PolicyConfigError("目标日程库不存在。")
            mapping[persona_id] = library_id

        config = await self.plugin.update_policy(revision, mutate)
        return self._life_saved(config, "人格日程库映射已保存。")

    async def save_life_schedule_pool(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        library_id = PolicyStore.validate_identifier(
            payload.get("library_id"),
            "日程库 ID",
        )
        pool = PolicyStore.normalize_life_pool(payload.get("pool"))

        def mutate(config: dict[str, Any]) -> None:
            library = config.get("life_schedule_libraries", {}).get(library_id)
            if not isinstance(library, dict):
                raise PolicyConfigError("日程库不存在。")
            library["pool"] = pool

        config = await self.plugin.update_policy(revision, mutate)
        return self._life_saved(config, "创意池已保存。")

    async def meme_library_bootstrap(self):
        manager = self._meme_library_manager()
        targets = manager.targets()
        library = await manager.describe(MEME_DEFAULT_LIBRARY_ID)
        config = self.plugin.store.config
        try:
            personas = await self.plugin.personas.list_personas()
        except Exception as exc:
            log.exception("读取 AstrBot 人格列表失败")
            personas = []
        return self._ok(
            {
                "revision": self.plugin.store.revision,
                "targets": targets,
                "library": library,
                "libraries": config.get("meme_libraries", {}),
                "persona_library_map": config.get("meme_persona_library_map", {}),
                "personas": personas,
            }
        )

    async def load_meme_library(self):
        payload = await self._json_payload()
        manager = self._meme_library_manager()
        library = await manager.describe(
            payload.get("target_id", MEME_DEFAULT_LIBRARY_ID),
        )
        return self._ok({"library": library})

    async def save_meme_category(self):
        payload = await self._json_payload()
        manager = self._meme_library_manager()
        library = await manager.save_category(
            payload.get("target_id", MEME_DEFAULT_LIBRARY_ID),
            payload.get("category"),
            payload.get("description", ""),
            payload.get("new_name"),
        )
        return self._ok(
            {
                "message": "分类已保存。",
                "library": library,
            }
        )

    async def delete_meme_category(self):
        payload = await self._json_payload()
        manager = self._meme_library_manager()
        library = await manager.delete_category(
            payload.get("target_id", MEME_DEFAULT_LIBRARY_ID),
            payload.get("category"),
        )
        return self._ok(
            {
                "message": "分类已删除。",
                "library": library,
            }
        )

    async def upload_meme_images(self):
        payload = await self._json_payload()
        manager = self._meme_library_manager()
        library = await manager.upload_images(
            payload.get("target_id", MEME_DEFAULT_LIBRARY_ID),
            payload.get("category"),
            payload.get("files", []),
        )
        return self._ok({"message": library.pop("message"), "library": library})

    async def delete_meme_images(self):
        payload = await self._json_payload()
        manager = self._meme_library_manager()
        filenames = payload.get("filenames")
        if filenames is None and payload.get("filename"):
            filenames = [payload.get("filename")]
        library = await manager.delete_images(
            payload.get("target_id", MEME_DEFAULT_LIBRARY_ID),
            payload.get("category"),
            filenames,
        )
        return self._ok({"message": library.pop("message"), "library": library})

    async def move_meme_images(self):
        payload = await self._json_payload()
        manager = self._meme_library_manager()
        library = await manager.move_images(
            payload.get("target_id", MEME_DEFAULT_LIBRARY_ID),
            payload.get("source_category"),
            payload.get("target_category"),
            payload.get("filenames", []),
            include_library=payload.get("include_library", True) is not False,
        )
        message = library.pop("message")
        result = {"message": message}
        if library:
            result["library"] = library
        return self._ok(result)

    async def copy_meme_images(self):
        payload = await self._json_payload()
        manager = self._meme_library_manager()
        library = await manager.copy_images(
            payload.get("target_id", MEME_DEFAULT_LIBRARY_ID),
            payload.get("category"),
            payload.get("filenames", []),
            payload.get("dest_target_id", MEME_DEFAULT_LIBRARY_ID),
            include_library=payload.get("include_library", True) is not False,
        )
        message = library.pop("message")
        result = {"message": message}
        if library:
            result["library"] = library
        return self._ok(result)

    async def meme_image_preview(self):
        payload = await self._json_payload()
        manager = self._meme_library_manager()
        image = await manager.image_preview(
            payload.get("target_id", MEME_DEFAULT_LIBRARY_ID),
            payload.get("category"),
            payload.get("filename"),
        )
        return self._ok(image)

    async def create_meme_library(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        name = MemePersonaLibraryManager.validate_library_name(payload.get("name"))
        lib_id = uuid4().hex

        def mutate(config: dict[str, Any]) -> None:
            config.setdefault("meme_libraries", {})[lib_id] = {"name": name}

        config = await self.plugin.update_policy(revision, mutate)
        manager = self._meme_library_manager()
        return self._ok(
            {
                "message": f"图库「{name}」已创建。",
                "revision": int(config.get("revision", 0)),
                "lib_id": lib_id,
                "targets": manager.targets(),
                "libraries": config.get("meme_libraries", {}),
            }
        )

    async def copy_meme_library(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        source_target_id = payload.get("source_target_id", MEME_DEFAULT_LIBRARY_ID)
        name = MemePersonaLibraryManager.validate_library_name(payload.get("name"))
        lib_id = uuid4().hex
        manager = self._meme_library_manager()
        copied = manager.copy_library(source_target_id, lib_id)

        def mutate(config: dict[str, Any]) -> None:
            config.setdefault("meme_libraries", {})[lib_id] = {"name": name}

        config = await self.plugin.update_policy(revision, mutate)
        return self._ok(
            {
                "message": f"已复制为「{name}」，共 {copied} 张图片。",
                "revision": int(config.get("revision", 0)),
                "lib_id": lib_id,
                "targets": manager.targets(),
                "libraries": config.get("meme_libraries", {}),
            }
        )

    async def rename_meme_library(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        lib_id = str(payload.get("lib_id", "") or "").strip()
        name = MemePersonaLibraryManager.validate_library_name(payload.get("name"))

        def mutate(config: dict[str, Any]) -> None:
            libraries = config.setdefault("meme_libraries", {})
            if lib_id not in libraries:
                raise PolicyConfigError("表情库不存在。")
            libraries[lib_id] = {"name": name}

        config = await self.plugin.update_policy(revision, mutate)
        manager = self._meme_library_manager()
        return self._ok(
            {
                "message": "图库已重命名。",
                "revision": int(config.get("revision", 0)),
                "targets": manager.targets(),
                "libraries": config.get("meme_libraries", {}),
            }
        )

    async def delete_meme_library(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        lib_id = str(payload.get("lib_id", "") or "").strip()

        def mutate(config: dict[str, Any]) -> None:
            libraries = config.setdefault("meme_libraries", {})
            if lib_id not in libraries:
                raise PolicyConfigError("表情库不存在。")
            libraries.pop(lib_id, None)
            # 映射到该库的人格会在 normalize 时自动回退默认库。

        config = await self.plugin.update_policy(revision, mutate)
        manager = self._meme_library_manager()
        try:
            manager.purge_library_dir(lib_id)
        except Exception as exc:
            log.warning("删除表情库目录失败：%s", exc)
        return self._ok(
            {
                "message": "图库已删除。",
                "revision": int(config.get("revision", 0)),
                "targets": manager.targets(),
                "libraries": config.get("meme_libraries", {}),
                "persona_library_map": config.get("meme_persona_library_map", {}),
            }
        )

    async def save_meme_persona_map(self):
        payload = await self._json_payload()
        revision = self._revision(payload)
        persona_id = str(payload.get("persona_id", "") or "").strip()
        lib_id = str(payload.get("lib_id", "") or "").strip()
        if not persona_id:
            raise PolicyConfigError("缺少人格 ID。")

        def mutate(config: dict[str, Any]) -> None:
            mapping = config.setdefault("meme_persona_library_map", {})
            if not lib_id or lib_id == MEME_DEFAULT_LIBRARY_ID:
                mapping.pop(persona_id, None)
                return
            if lib_id not in config.get("meme_libraries", {}):
                raise PolicyConfigError("目标表情库不存在。")
            mapping[persona_id] = lib_id

        config = await self.plugin.update_policy(revision, mutate)
        return self._ok(
            {
                "message": "人格表情库映射已保存。",
                "revision": int(config.get("revision", 0)),
                "persona_library_map": config.get("meme_persona_library_map", {}),
            }
        )

    async def _validate_schedule_references(
        self,
        rule: dict[str, Any],
    ) -> None:
        store = self.plugin.store
        if store is None:
            raise PolicyConfigError("策略数据尚未加载。")
        config = store.config
        target_type = rule["target_type"]
        user_id = rule["user_id"]
        group_id = rule["group_id"]
        if target_type == "private":
            exists = user_id in config.get("private_users", {})
            missing_message = "所选私聊用户不存在，请先添加用户。"
        elif target_type == "group":
            exists = group_id in config.get("groups", {})
            missing_message = "所选群聊不存在，请先添加群聊。"
        else:
            group = config.get("groups", {}).get(group_id, {})
            exists = user_id in group.get("users", {})
            missing_message = "所选群成员设置不存在，请先添加成员设置。"
        if not exists:
            raise PolicyConfigError(missing_message)

        personas = await self.plugin.personas.list_personas()
        available = {
            str(item.get("persona_id", ""))
            for item in personas
        }
        if rule["mode"] == "daily":
            selected = {rule["persona_id"]}
        elif rule["mode"] == "weekly":
            selected = {
                str(item.get("persona_id", ""))
                for item in rule.get("weekly_rules", [])
            }
        else:
            selected = set(rule["persona_ids"])
        missing = sorted(selected - available)
        if missing:
            raise PolicyConfigError(
                "以下人格已不可用，请重新选择：" + "、".join(missing)
            )

    async def _validate_persona_rule(
        self,
        rule: dict[str, Any],
    ) -> None:
        mode = str(rule.get("persona_mode", "") or "")
        selected = set()
        if mode == "fixed" and rule.get("persona_id"):
            selected.add(str(rule["persona_id"]))
        if mode == "auto":
            selected.update(
                str(item)
                for item in rule.get("auto_persona", {}).get(
                    "persona_ids",
                    [],
                )
                if item
            )
        await self._validate_persona_ids(selected)

    async def _validate_persona_ids(self, selected: set[str]) -> None:
        if not selected:
            return
        personas = await self.plugin.personas.list_personas()
        available = {
            str(item.get("persona_id", "") or "")
            for item in personas
        }
        missing = sorted(selected - available)
        if missing:
            raise PolicyConfigError(
                "以下人格已不可用，请重新选择：" + "、".join(missing)
            )

    def _provider_catalog(self) -> list[dict[str, Any]]:
        context = self.plugin.context
        candidates: list[Any] = []
        for name in (
            "get_all_providers",
            "get_all_provider",
            "get_providers",
        ):
            getter = getattr(context, name, None)
            if not callable(getter):
                continue
            try:
                value = getter()
                if hasattr(value, "__await__"):
                    continue
                candidates.append(value)
            except Exception:
                continue
        manager = getattr(context, "provider_manager", None)
        for name in ("providers", "provider_insts", "instances"):
            value = getattr(manager, name, None)
            if value:
                candidates.append(value)

        result: dict[str, dict[str, Any]] = {}
        for source in candidates:
            if isinstance(source, dict):
                values = source.items()
            else:
                values = (
                    (str(index), item)
                    for index, item in enumerate(source or [])
                )
            for fallback_id, item in values:
                meta = None
                meta_getter = getattr(item, "meta", None)
                if callable(meta_getter):
                    try:
                        meta = meta_getter()
                    except Exception:
                        meta = None
                provider_id = str(
                    self._value(item, "id", "")
                    or self._value(item, "provider_id", "")
                    or self._value(meta, "id", "")
                    or fallback_id
                ).strip()
                if not provider_id or provider_id.isdigit():
                    continue
                name = str(
                    self._value(item, "name", "")
                    or self._value(item, "display_name", "")
                    or self._value(meta, "model", "")
                    or provider_id
                )
                model_getter = getattr(item, "get_model", None)
                current_model = (
                    str(model_getter() or "")
                    if callable(model_getter)
                    else ""
                )
                models = self._value(item, "models", [])
                if isinstance(models, dict):
                    models = list(models)
                if not isinstance(models, (list, tuple, set)):
                    models = []
                result[provider_id] = {
                    "provider_id": provider_id,
                    "name": name,
                    "model": current_model,
                    "models": [
                        str(self._value(model, "id", "") or model)
                        for model in models
                        if str(self._value(model, "id", "") or model)
                    ],
                }
        return sorted(
            result.values(),
            key=lambda item: item["name"].casefold(),
        )

    @staticmethod
    def _value(source: Any, name: str, default: Any = None) -> Any:
        if isinstance(source, dict):
            return source.get(name, default)
        return getattr(source, name, default)

    @staticmethod
    def _life_library_name(value: Any) -> str:
        name = str(value or "").strip()
        if not name:
            raise PolicyConfigError("日程库名称不能为空。")
        if len(name) > 60:
            raise PolicyConfigError("日程库名称不能超过 60 个字符。")
        return name

    @classmethod
    def _life_record(cls, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            raise PolicyConfigError("日程记录必须是对象。")
        return {
            "date": cls._date(value.get("date")),
            "outfit_style": cls._text(
                value.get("outfit_style"),
                "穿搭风格",
                300,
            ),
            "outfit": cls._text(value.get("outfit"), "穿搭", 2000),
            "schedule": cls._text(
                value.get("schedule"),
                "日程内容",
                10000,
            ),
            "status": cls._text(
                value.get("status", "ok"),
                "日程状态",
                60,
            ) or "ok",
        }

    @staticmethod
    def _date(value: Any) -> str:
        text = str(value or "").strip()
        try:
            return date_type.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise PolicyConfigError(
                "日期必须使用 YYYY-MM-DD 格式。"
            ) from exc

    @staticmethod
    def _text(value: Any, label: str, maximum: int) -> str:
        text = str(value or "").strip()
        if len(text) > maximum:
            raise PolicyConfigError(
                f"{label}不能超过 {maximum} 个字符。"
            )
        return text

    @classmethod
    def _life_saved(
        cls,
        config: dict[str, Any],
        message: str,
        **extra: Any,
    ):
        return cls._ok(
            {
                "message": message,
                "revision": int(config.get("revision", 0)),
                "life_schedule_libraries": config.get(
                    "life_schedule_libraries",
                    {},
                ),
                "life_schedule_persona_map": config.get(
                    "life_schedule_persona_map",
                    {},
                ),
                **extra,
            }
        )

    @staticmethod
    async def _json_payload() -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise PolicyConfigError("请求内容必须是对象。")
        return payload

    @staticmethod
    def _revision(payload: dict[str, Any]) -> int:
        try:
            return int(payload.get("revision"))
        except (TypeError, ValueError) as exc:
            raise PolicyConfigError("缺少有效的数据版本号，请刷新页面。") from exc

    @classmethod
    def _saved(
        cls,
        config: dict[str, Any],
        message: str,
    ):
        return cls._ok(
            {
                "message": message,
                "revision": int(config.get("revision", 0)),
            }
        )

    @staticmethod
    def _missing_personas(
        config: dict[str, Any],
        personas: list[dict[str, Any]],
    ) -> set[str]:
        configured = set()
        for rule in config.get("private_users", {}).values():
            if rule.get("persona_id"):
                configured.add(str(rule["persona_id"]))
            configured.update(
                str(item)
                for item in rule.get("auto_persona", {}).get(
                    "persona_ids",
                    [],
                )
                if item
            )
        for group in config.get("groups", {}).values():
            if group.get("persona_id"):
                configured.add(str(group["persona_id"]))
            configured.update(
                str(item)
                for item in group.get("auto_persona", {}).get(
                    "persona_ids",
                    [],
                )
                if item
            )
            for member in group.get("users", {}).values():
                if member.get("persona_id"):
                    configured.add(str(member["persona_id"]))
                configured.update(
                    str(item)
                    for item in member.get("auto_persona", {}).get(
                        "persona_ids",
                        [],
                    )
                    if item
                )
        for schedule in config.get("schedules", {}).values():
            if schedule.get("persona_id"):
                configured.add(str(schedule["persona_id"]))
            configured.update(
                str(item)
                for item in schedule.get("persona_ids", [])
                if item
            )
        configured.update(
            str(item)
            for item in config.get(
                "life_schedule_persona_map",
                {},
            )
        )
        available = {
            str(persona.get("persona_id", ""))
            for persona in personas
        }
        return configured - available

    def _meme_library_manager(self) -> MemePersonaLibraryManager:
        adapter = getattr(self.plugin, "meme_manager_adapter", None)
        if adapter is None:
            raise PolicyConfigError("表情包库隔离适配器尚未初始化。")
        return MemePersonaLibraryManager(adapter)

    def _guard(self, handler, description: str):
        @wraps(handler)
        async def guarded():
            try:
                return await handler()
            except PolicyConflictError as exc:
                return self._error(str(exc), 409)
            except PolicyConfigError as exc:
                return self._error(str(exc), 400)
            except Exception as exc:
                log.exception("%s失败", description)
                return self._error(f"{description}失败：{exc}", 500)

        return guarded

    @staticmethod
    def _ok(data: dict[str, Any]):
        return {"status": "ok", "data": data}

    @staticmethod
    def _error(message: str, status: int):
        return {"status": "error", "message": message, "code": status}
