"""AstrBot 多人格管理插件入口。"""

from __future__ import annotations

import asyncio
import inspect
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register

try:
    from astrbot.core.star.star import star_map
except ImportError:
    star_map = {}

from .core import (
    AUTO_PERSONA_EVENT_KEY,
    AstrBotPersonaAdapter,
    AutoPersonaManager,
    EventIdentity,
    GiteeAiimgPersonaAdapter,
    IntegrationInspector,
    LifeScheduleLibraryManager,
    LifeSchedulerPersonaAdapter,
    LivingMemoryPersonaAdapter,
    MemoryIsolationManager,
    MemeManagerPersonaAdapter,
    PersonaScheduler,
    PolicyConfigError,
    PolicyDecision,
    PolicyMatcher,
    PolicyStore,
    PrivateCompanionProactiveAdapter,
    ProactiveChatPersonaAdapter,
    SELECTED_MODEL_EVENT_KEY,
    AstrBotSessionPersonaManager,
    SessionImporter,
    collect_plugin_catalog,
    default_plugin_access,
    filter_plugin_scope,
    imported_group_rule,
    imported_private_rule,
    is_group_manager,
    is_protected_plugin,
    parse_session_identity,
    restore_user_policy_wrappers,
)


PLUGIN_NAME = "astrbot_plugin_user_policy"


@register(
    PLUGIN_NAME,
    "烟雨寒月",
    "为私聊用户、群聊和群成员自由切换人格并管理相关策略。",
    "3.4.13",
)
class UserPolicyPlugin(Star):
    """轻量、低冲突的用户人格与插件权限层。"""

    EXTRA_DECISION = "user_policy_decision"
    EXTRA_DECISION_OWNER = "user_policy_decision_owner"
    EXTRA_DECISION_REVISION = "user_policy_decision_revision"
    EXTRA_PERSONA_RESULT = "user_policy_persona_result"

    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | None = None,
    ):
        super().__init__(context)
        self.webui_config = config or {}
        self.plugin_root = Path(__file__).resolve().parent
        self.store: PolicyStore | None = None
        self.matcher: PolicyMatcher | None = None
        self.memory_isolation: MemoryIsolationManager | None = None
        self.persona_scheduler: PersonaScheduler | None = None
        self.auto_persona: AutoPersonaManager | None = None
        self.life_schedule_library: LifeScheduleLibraryManager | None = None
        self.personas = AstrBotPersonaAdapter(context)
        self.session_persona = AstrBotSessionPersonaManager(context)
        self.session_importer = SessionImporter(context)
        self.integrations = IntegrationInspector(context, star_map, self)
        self.meme_manager_adapter: MemeManagerPersonaAdapter | None = None
        self.gitee_aiimg_adapter: GiteeAiimgPersonaAdapter | None = None
        self.livingmemory_adapter: LivingMemoryPersonaAdapter | None = None
        self.private_companion_adapter: (
            PrivateCompanionProactiveAdapter | None
        ) = None
        self.proactive_chat_adapter: ProactiveChatPersonaAdapter | None = None
        self.life_scheduler_adapter: LifeSchedulerPersonaAdapter | None = None
        self._plugin_catalog_cache: dict[str, dict[str, Any]] | None = None
        self._module_plugin_cache: dict[str, str] = {}
        self._decision_owner = object()
        self.policy_lock = asyncio.Lock()
        self.page_api = None
        self._register_page_api_if_available()

    async def initialize(self):
        """加载 3.0 策略，并在首次启动时迁移旧 YAML。"""

        try:
            data_dir = StarTools.get_data_dir(PLUGIN_NAME)
            self.store = PolicyStore(
                data_dir,
                self.plugin_root / "data" / "config.yaml",
            )
            is_first_policy_load = not self.store.policy_path.exists()
            self.memory_isolation = MemoryIsolationManager(
                self.context,
                data_dir,
            )
            self.meme_manager_adapter = MemeManagerPersonaAdapter(
                self,
                data_dir,
                star_map,
            )
            self.gitee_aiimg_adapter = GiteeAiimgPersonaAdapter(
                self,
                star_map,
            )
            self.auto_persona = AutoPersonaManager(
                self.context,
                data_dir,
                lambda: self.store.config if self.store else {},
                self.personas,
            )
            self.life_schedule_library = LifeScheduleLibraryManager(self)
            self.livingmemory_adapter = LivingMemoryPersonaAdapter(
                self,
                star_map,
            )
            self.private_companion_adapter = (
                PrivateCompanionProactiveAdapter(
                    self,
                    star_map,
                )
            )
            self.proactive_chat_adapter = ProactiveChatPersonaAdapter(
                self,
                star_map,
            )
            self.life_scheduler_adapter = LifeSchedulerPersonaAdapter(
                self,
                star_map,
            )
            restored = restore_user_policy_wrappers(self.context, star_map)
            if restored:
                logger.info(
                    "[用户策略] 已清理历史运行时适配包裹：%s 处",
                    restored,
                )
            self._reload_runtime()
            if (
                is_first_policy_load
                and not self.store.config.get("private_users")
                and not self.store.config.get("groups")
            ):
                await self.import_existing_sessions()
            self.meme_manager_adapter.configure()
            self.gitee_aiimg_adapter.configure()
            self.livingmemory_adapter.configure()
            self.private_companion_adapter.configure()
            self.proactive_chat_adapter.configure()
            self.life_scheduler_adapter.configure()
            self.persona_scheduler = PersonaScheduler(
                data_dir,
                self._schedule_rules,
                self._apply_scheduled_persona,
            )
            self.persona_scheduler.start()
        except (OSError, RuntimeError, PolicyConfigError) as exc:
            logger.error(f"[用户策略] 初始化失败：{exc}")
            return
        logger.info(f"[用户策略] 已加载策略数据：{self.store.policy_path}")

    async def import_existing_sessions(self) -> dict[str, int]:
        if self.store is None:
            return {"private_users": 0, "groups": 0}
        try:
            candidates = await self.session_importer.collect()
        except Exception as exc:
            logger.warning(f"[用户策略] 自动导入已有会话失败：{exc}")
            return {"private_users": 0, "groups": 0}

        existing_users = self.store.config.get("private_users", {})
        existing_groups = self.store.config.get("groups", {})
        ignored = self.store.config.get("session_import_ignored", {})
        ignored_users = {
            str(item)
            for item in ignored.get("private_users", [])
        }
        ignored_groups = {
            str(item)
            for item in ignored.get("groups", [])
        }
        users_to_add = [
            item
            for item in candidates.get("private_users", [])
            if str(item.get("user_id", "") or "").strip()
            and str(item.get("user_id", "") or "").strip() not in existing_users
            and str(item.get("user_id", "") or "").strip() not in ignored_users
        ]
        groups_to_add = [
            item
            for item in candidates.get("groups", [])
            if str(item.get("group_id", "") or "").strip()
            and str(item.get("group_id", "") or "").strip() not in existing_groups
            and str(item.get("group_id", "") or "").strip() not in ignored_groups
        ]
        added = {
            "private_users": len(users_to_add),
            "groups": len(groups_to_add),
        }
        if not users_to_add and not groups_to_add:
            return added

        async with self.policy_lock:
            def mutate(config: dict[str, Any]) -> None:
                for item in users_to_add:
                    user_id = str(item.get("user_id", "") or "").strip()
                    config["private_users"][user_id] = imported_private_rule()
                for item in groups_to_add:
                    group_id = str(item.get("group_id", "") or "").strip()
                    config["groups"][group_id] = imported_group_rule(
                        str(item.get("label", "") or "")
                    )

            config = self.store.update(self.store.revision, mutate)
            self.matcher = PolicyMatcher(config)
        return added

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1000)
    async def apply_message_policy(self, event: AstrMessageEvent):
        """在当前入站消息中应用黑白名单和插件范围。"""

        if not self._is_available():
            return

        decision = self._decision_for_event(event)
        self._cache_decision(event, decision)
        self._log_match(decision)

        if decision.is_blocked:
            event.stop_event()
            if self._looks_like_command(event):
                yield event.plain_result("你当前没有权限使用机器人。")
            return

        self._apply_event_plugin_scope(event, decision)

    @filter.on_waiting_llm_request(priority=-1000)
    async def resolve_auto_persona(self, event: AstrMessageEvent):
        """在其他 LLM 请求钩子前确定本次消息的最终人格。"""

        if not self._is_available() or self.auto_persona is None:
            return
        decision = self._get_or_create_decision(event)
        await self.auto_persona.resolve(
            event,
            decision,
            str(event.get_message_str() or ""),
            allow_selection=(
                not self._is_private_companion_internal_image_event(event)
            ),
        )
        if decision.policy_configured:
            event.set_extra("dynamic_persona_decision", None)
            if decision.persona_mode != "auto":
                if not decision.provider_id:
                    event.set_extra("selected_provider", None)
                    event.set_extra("selected_model", None)

    @filter.on_llm_request(priority=-1000)
    async def apply_llm_policy(
        self,
        event: AstrMessageEvent,
        request: ProviderRequest,
    ):
        """为本次请求选择人格，并收窄第三方插件工具。"""

        if not self._is_available():
            return
        decision = self._get_or_create_decision(event)
        if (
            self.auto_persona is not None
            and not bool(event.get_extra(AUTO_PERSONA_EVENT_KEY))
        ):
            await self.auto_persona.resolve(
                event,
                decision,
                str(event.get_message_str() or ""),
                allow_selection=(
                    not self._is_private_companion_internal_image_event(
                        event
                    )
                ),
            )
        source_conversation = getattr(request, "conversation", None)
        if self.memory_isolation is not None:
            try:
                await self.memory_isolation.apply(
                    event,
                    request,
                    decision,
                )
            except Exception as exc:
                logger.warning(f"[用户策略] 应用记忆隔离失败：{exc}")
        result = await self.personas.apply_to_request(
            decision.persona_id,
            request,
            self._event_unified_msg_origin(event),
            source_conversation=source_conversation,
        )
        event.set_extra(
            self.EXTRA_PERSONA_RESULT,
            self.personas.serialize_result(result),
        )
        if decision.persona_id and not result.found:
            logger.warning(
                "[用户策略] 配置的人格不存在：%s",
                decision.persona_id,
            )
        selected_model = str(
            event.get_extra(SELECTED_MODEL_EVENT_KEY) or ""
        ).strip()
        if selected_model and hasattr(request, "model"):
            request.model = selected_model
        self._filter_llm_tools(request, decision)

    @filter.command("人格列表", alias={"persona_list"}, priority=1001)
    async def list_personas_command(self, event: AstrMessageEvent):
        """列出 AstrBot 人格设定中的可用人格。"""

        if not self._is_available():
            yield event.plain_result("多人格管理插件尚未成功加载。")
            return
        decision = self._get_or_create_decision(event)
        if not self._can_use_query_command(event, decision):
            yield event.plain_result(
                "你没有查看人格列表的权限，请联系管理员授权。"
            )
            return
        personas = await self.personas.list_personas()
        if not personas:
            yield event.plain_result("当前没有可用的 AstrBot 人格。")
            return
        lines = ["可用人格："]
        for persona in personas:
            persona_id = persona["persona_id"]
            name = persona.get("name") or persona_id
            label = persona_id if name == persona_id else f"{name}（{persona_id}）"
            lines.append(f"- {label}")
        yield event.plain_result("\n".join(lines))

    @filter.command("当前人格", alias={"persona_current"}, priority=1001)
    async def current_persona_command(self, event: AstrMessageEvent):
        """查看自己在当前会话命中的人格。"""

        if not self._is_available():
            yield event.plain_result("多人格管理插件尚未成功加载。")
            return
        decision = self._get_or_create_decision(event)
        if not self._can_use_query_command(event, decision):
            yield event.plain_result(
                "你没有查看当前人格的权限，请联系管理员授权。"
            )
            return
        if decision.persona_mode == "auto":
            current = decision.persona_id
            if not current and self.auto_persona is not None:
                current = str(
                    self.auto_persona.record_for_decision(decision).get(
                        "persona_id",
                        "",
                    )
                    or ""
                )
            persona = f"自动切换（当前：{current or '尚未选择'}）"
        else:
            persona = decision.persona_id or "跟随 AstrBot 会话默认"
        diagnostics = await self.session_persona.diagnostics(
            self._event_unified_msg_origin(event)
        )
        conversation_persona = (
            diagnostics.conversation_persona_id or "未绑定"
        )
        default_persona = diagnostics.default_persona_id or "未读取到"
        yield event.plain_result(
            f"当前人格：{persona}\n"
            f"来源：{decision.match_source}\n"
            f"AstrBot 当前会话绑定：{conversation_persona}\n"
            f"AstrBot 全局默认：{default_persona}"
        )

    @filter.command("切换人格", alias={"persona_switch"}, priority=1001)
    async def switch_persona_command(self, event: AstrMessageEvent):
        """修改自己的私聊人格或当前群内个人专属人格。"""

        if not self._is_available():
            yield event.plain_result("多人格管理插件尚未成功加载。")
            return
        decision = self._get_or_create_decision(event)
        if not decision.allow_persona_switch and not event.is_admin():
            yield event.plain_result(
                "你没有自行切换人格的权限，请联系管理员在多人格管理页面授权。"
            )
            return

        requested = self._command_argument(event, "切换人格", "persona_switch")
        if not requested:
            yield event.plain_result("用法：/切换人格 人格名称 或 /切换人格 自动")
            return
        if requested.casefold() in {"自动", "auto"}:
            if len(decision.auto_persona.get("persona_ids", [])) < 2:
                yield event.plain_result(
                    "当前规则尚未在 WebUI 配置至少两个自动候选人格。"
                )
                return
            await self._set_personal_persona(event, "", "auto")
            yield event.plain_result("已切换为自动人格模式。")
            return
        persona_id, error = await self._resolve_persona(requested)
        if error:
            yield event.plain_result(error)
            return

        try:
            await self._set_personal_persona(event, persona_id, "fixed")
        except PolicyConfigError as exc:
            yield event.plain_result(f"切换人格失败：{exc}")
            return
        yield event.plain_result(f"已切换为人格：{persona_id}")

    @filter.command("恢复人格", alias={"persona_reset"}, priority=1001)
    async def reset_persona_command(self, event: AstrMessageEvent):
        """清除个人覆盖并恢复继承的人格。"""

        if not self._is_available():
            yield event.plain_result("多人格管理插件尚未成功加载。")
            return
        decision = self._get_or_create_decision(event)
        if not decision.allow_persona_switch and not event.is_admin():
            yield event.plain_result(
                "你没有自行切换人格的权限，请联系管理员在多人格管理页面授权。"
            )
            return
        try:
            await self._set_personal_persona(
                event,
                "",
                "default" if event.is_private_chat() else "inherit",
            )
        except PolicyConfigError as exc:
            yield event.plain_result(f"恢复人格失败：{exc}")
            return
        message = (
            "已恢复跟随当前群的默认人格。"
            if not event.is_private_chat()
            else "已恢复跟随 AstrBot 会话默认人格。"
        )
        yield event.plain_result(message)

    @filter.command(
        "重置会话人格",
        alias={"persona_session_reset", "会话人格重置"},
        priority=1001,
    )
    async def reset_session_persona_command(self, event: AstrMessageEvent):
        """新建未绑定人格的当前会话，让 AstrBot 全局默认重新生效。"""

        if not self._is_available():
            yield event.plain_result("多人格管理插件尚未成功加载。")
            return
        decision = self._get_or_create_decision(event)
        if not decision.allow_persona_switch and not event.is_admin():
            yield event.plain_result(
                "你没有重置会话人格的权限，请联系管理员在多人格管理页面授权。"
            )
            return
        if decision.persona_id or decision.persona_mode == "auto":
            yield event.plain_result(
                "当前仍命中本插件固定/自动人格规则。请先在 WebUI 修改规则，"
                "或使用 /恢复人格 清除个人覆盖。"
            )
            return
        result = await self.session_persona.reset_to_global_default(
            self._event_unified_msg_origin(event),
            self._event_platform_id(event),
        )
        if not result.ok:
            yield event.plain_result("重置会话人格失败：" + result.message)
            return
        yield event.plain_result(
            "已重置当前会话人格：新会话未绑定人格，下一次回复将跟随 "
            "AstrBot 全局默认人格。\n"
            f"原会话：{result.old_conversation_id or '未知'}\n"
            f"新会话：{result.new_conversation_id}\n"
            f"已保留历史消息：{result.copied_messages} 条"
        )

    @filter.command("记忆隔离", alias={"memory_isolation"}, priority=1001)
    async def memory_isolation_command(self, event: AstrMessageEvent):
        """查看或修改自己的 AstrBot 人格对话记忆隔离设置。"""

        if not self._is_available():
            yield event.plain_result("多人格管理插件尚未成功加载。")
            return
        decision = self._get_or_create_decision(event)
        requested = self._command_argument(
            event,
            "记忆隔离",
            "memory_isolation",
        )
        if not requested:
            if not self._can_use_query_command(event, decision):
                yield event.plain_result(
                    "你没有查看记忆隔离的权限，请联系管理员授权。"
                )
                return
            status = "开启" if decision.memory_isolation else "关闭"
            yield event.plain_result(
                f"当前人格对话记忆隔离：{status}\n"
                "该设置只隔离 AstrBot 对话历史；LivingMemory 长期记忆"
                "请使用 /长期记忆隔离 查看或修改。"
            )
            return
        if not decision.allow_persona_switch and not event.is_admin():
            yield event.plain_result(
                "你没有修改记忆隔离的权限，请联系管理员在多人格管理页面授权。"
            )
            return

        enabled = self._parse_switch(requested)
        if enabled is None:
            yield event.plain_result("用法：/记忆隔离 开启 或 /记忆隔离 关闭")
            return
        try:
            await self._set_personal_memory_isolation(event, enabled)
        except PolicyConfigError as exc:
            yield event.plain_result(f"修改记忆隔离失败：{exc}")
            return
        status = "开启" if enabled else "关闭"
        yield event.plain_result(
            f"人格对话记忆隔离已{status}。"
        )

    @filter.command(
        "重置人格记忆",
        alias={"persona_memory_reset"},
        priority=1001,
    )
    async def reset_persona_memory_command(self, event: AstrMessageEvent):
        """解除当前范围内指定人格的隔离会话绑定。"""

        if not self._is_available() or self.memory_isolation is None:
            yield event.plain_result("多人格管理插件尚未成功加载。")
            return
        decision = self._get_or_create_decision(event)
        if not decision.allow_persona_switch and not event.is_admin():
            yield event.plain_result(
                "你没有重置人格记忆的权限，请联系管理员在多人格管理页面授权。"
            )
            return

        requested = self._command_argument(
            event,
            "重置人格记忆",
            "persona_memory_reset",
        )
        if not requested:
            yield event.plain_result("用法：/重置人格记忆 人格名称")
            return
        persona_id, error = await self._resolve_persona(requested)
        if error:
            yield event.plain_result(error)
            return

        old_cid = await self.memory_isolation.reset_persona_binding(
            decision,
            persona_id,
        )
        if not old_cid:
            yield event.plain_result(
                f"当前范围没有找到人格 {persona_id} 的隔离会话绑定。"
            )
            return
        yield event.plain_result(
            f"已重置人格 {persona_id} 的隔离会话绑定。"
            "下一次切到该人格会创建一条干净的新会话。"
        )

    @filter.command(
        "长期记忆隔离",
        alias={"livingmemory_isolation"},
        priority=1001,
    )
    async def livingmemory_isolation_command(
        self,
        event: AstrMessageEvent,
    ):
        """查看或修改自己的 LivingMemory 人格记忆隔离。"""

        if not self._is_available():
            yield event.plain_result("多人格管理插件尚未成功加载。")
            return
        decision = self._get_or_create_decision(event)
        requested = self._command_argument(
            event,
            "长期记忆隔离",
            "livingmemory_isolation",
        )
        if not requested:
            if not self._can_use_query_command(event, decision):
                yield event.plain_result(
                    "你没有查看长期记忆隔离的权限，请联系管理员授权。"
                )
                return
            status = "开启" if decision.livingmemory_isolation else "关闭"
            yield event.plain_result(
                f"当前 LivingMemory 人格记忆隔离：{status}"
            )
            return
        if not decision.allow_persona_switch and not event.is_admin():
            yield event.plain_result(
                "你没有修改长期记忆隔离的权限，请联系管理员授权。"
            )
            return
        enabled = self._parse_switch(requested)
        if enabled is None:
            yield event.plain_result(
                "用法：/长期记忆隔离 开启 或 /长期记忆隔离 关闭"
            )
            return
        try:
            await self._set_personal_livingmemory_isolation(
                event,
                enabled,
            )
        except PolicyConfigError as exc:
            yield event.plain_result(f"修改长期记忆隔离失败：{exc}")
            return
        status = "开启" if enabled else "关闭"
        yield event.plain_result(
            f"LivingMemory 人格记忆隔离已{status}。"
        )

    @filter.command("群当前人格", alias={"group_persona_current"}, priority=1001)
    async def current_group_persona_command(self, event: AstrMessageEvent):
        """查看当前群的默认人格。"""

        if event.is_private_chat():
            yield event.plain_result("该指令只能在群聊中使用。")
            return
        if not self._is_available():
            yield event.plain_result("多人格管理插件尚未成功加载。")
            return
        decision = self._get_or_create_decision(event)
        if not self._can_use_query_command(event, decision):
            yield event.plain_result(
                "你没有查看当前群人格的权限，请联系管理员授权。"
            )
            return
        group_rule = self._group_rule_for_event(event)
        persona_id = str(group_rule.get("persona_id", "") or "")
        if group_rule.get("persona_mode") == "auto":
            current = ""
            if self.auto_persona is not None:
                current = str(
                    self.auto_persona.record_for_decision(decision).get(
                        "persona_id",
                        "",
                    )
                    or ""
                )
            label = f"自动切换（当前：{current or '尚未选择'}）"
        else:
            label = persona_id or "跟随 AstrBot 会话默认"
        yield event.plain_result("当前群默认人格：" + label)

    @filter.command("群切换人格", alias={"group_persona_switch"}, priority=1001)
    async def switch_group_persona_command(self, event: AstrMessageEvent):
        """由群策略管理员修改当前群默认人格。"""

        if event.is_private_chat():
            yield event.plain_result("该指令只能在群聊中使用。")
            return
        if not await self._can_manage_group(event):
            yield event.plain_result("你没有修改当前群默认人格的权限。")
            return

        requested = self._command_argument(
            event,
            "群切换人格",
            "group_persona_switch",
        )
        if not requested:
            yield event.plain_result(
                "用法：/群切换人格 人格名称 或 /群切换人格 自动"
            )
            return
        if requested.casefold() in {"自动", "auto"}:
            group_rule = self._group_rule_for_event(event)
            if len(
                group_rule.get("auto_persona", {}).get("persona_ids", [])
            ) < 2:
                yield event.plain_result(
                    "当前群尚未在 WebUI 配置至少两个自动候选人格。"
                )
                return
            await self._set_group_persona(
                self._group_policy_id_for_event(event),
                "",
                "auto",
            )
            yield event.plain_result("当前群已切换为自动人格模式。")
            return
        persona_id, error = await self._resolve_persona(requested)
        if error:
            yield event.plain_result(error)
            return
        try:
            await self._set_group_persona(
                self._group_policy_id_for_event(event),
                persona_id,
                "fixed",
            )
        except PolicyConfigError as exc:
            yield event.plain_result(f"切换群人格失败：{exc}")
            return
        yield event.plain_result(f"当前群默认人格已切换为：{persona_id}")

    @filter.command("群恢复人格", alias={"group_persona_reset"}, priority=1001)
    async def reset_group_persona_command(self, event: AstrMessageEvent):
        """由群策略管理员清除当前群默认人格。"""

        if event.is_private_chat():
            yield event.plain_result("该指令只能在群聊中使用。")
            return
        if not await self._can_manage_group(event):
            yield event.plain_result("你没有修改当前群默认人格的权限。")
            return
        try:
            await self._set_group_persona(
                self._group_policy_id_for_event(event),
                "",
                "default",
            )
        except PolicyConfigError as exc:
            yield event.plain_result(f"恢复群人格失败：{exc}")
            return
        yield event.plain_result("当前群已恢复跟随 AstrBot 会话默认人格。")

    async def terminate(self):
        if self.persona_scheduler is not None:
            await self.persona_scheduler.stop()
        if self.meme_manager_adapter is not None:
            self.meme_manager_adapter.restore()
        if self.gitee_aiimg_adapter is not None:
            self.gitee_aiimg_adapter.restore()
        if self.livingmemory_adapter is not None:
            self.livingmemory_adapter.restore()
        if self.private_companion_adapter is not None:
            self.private_companion_adapter.restore()
        if self.proactive_chat_adapter is not None:
            self.proactive_chat_adapter.restore()
        if self.life_scheduler_adapter is not None:
            self.life_scheduler_adapter.restore()
        restored = restore_user_policy_wrappers(self.context, star_map)
        if restored:
            logger.info(
                "[用户策略] 停用时已清理历史运行时适配包裹：%s 处",
                restored,
            )
        if self.auto_persona is not None:
            await self.auto_persona.flush()
        logger.info("[用户策略] 插件已停用。")

    @filter.on_plugin_loaded()
    async def refresh_integrations_on_plugin_loaded(self, metadata: Any):
        """第三方插件加载后重新检测运行时适配。"""

        self._invalidate_plugin_catalog()
        self._configure_integration_adapters()

    @filter.on_plugin_unloaded()
    async def refresh_integrations_on_plugin_unloaded(self, metadata: Any):
        """第三方插件卸载后刷新兼容状态。"""

        self._invalidate_plugin_catalog()
        self._configure_integration_adapters()

    def _reload_runtime(self) -> None:
        if self.store is None:
            raise PolicyConfigError("策略存储尚未初始化。")
        config = self.store.load()
        self.matcher = PolicyMatcher(config)

    async def update_policy(
        self,
        expected_revision: int,
        mutator: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        if self.store is None:
            raise PolicyConfigError("策略存储尚未初始化。")
        async with self.policy_lock:
            config = self.store.update(expected_revision, mutator)
            self.matcher = PolicyMatcher(config)
            self._invalidate_plugin_catalog()
            if self.persona_scheduler is not None:
                self.persona_scheduler.notify_changed()
            self._configure_integration_adapters()
            return config

    def _configure_integration_adapters(self) -> None:
        adapters = (
            self.meme_manager_adapter,
            self.gitee_aiimg_adapter,
            self.livingmemory_adapter,
            self.private_companion_adapter,
            self.proactive_chat_adapter,
            self.life_scheduler_adapter,
        )
        for adapter in adapters:
            if adapter is None:
                continue
            try:
                adapter.restore()
            except Exception as exc:
                logger.warning(f"[用户策略] 恢复兼容适配失败：{exc}")

        restored = restore_user_policy_wrappers(self.context, star_map)
        if restored:
            logger.info(
                "[用户策略] 刷新前已清理历史运行时适配包裹：%s 处",
                restored,
            )

        for adapter in adapters:
            if adapter is None:
                continue
            try:
                adapter.configure()
            except Exception as exc:
                logger.warning(f"[用户策略] 刷新兼容适配失败：{exc}")

    def _register_page_api_if_available(self) -> None:
        register_web_api = getattr(self.context, "register_web_api", None)
        if not callable(register_web_api):
            logger.warning(
                "[用户策略] 当前 AstrBot 不支持插件页面 API，"
                "请升级到 4.24.2 或更高版本。"
            )
            return
        try:
            from .core.page_api import PluginPageApi

            self.page_api = PluginPageApi(self)
            self.page_api.register_routes()
        except Exception as exc:
            logger.exception(f"[用户策略] 注册插件页面 API 失败：{exc}")

    def _decision_for_event(self, event: AstrMessageEvent) -> PolicyDecision:
        if self.matcher is None:
            raise PolicyConfigError("策略匹配器尚未初始化。")
        is_private = bool(event.is_private_chat())
        identity = EventIdentity(
            platform=str(event.get_platform_name()),
            user_id=self._event_user_id(event),
            group_id="" if is_private else self._event_group_id(event),
            chat_type="private" if is_private else "group",
            role="admin" if event.is_admin() else "member",
        )
        if is_private:
            return self.matcher.match_private_aliases(
                identity,
                self._private_event_aliases(event),
            )
        return self.matcher.match_group_aliases(
            identity,
            self._group_event_aliases(event),
        )

    def _get_or_create_decision(
        self,
        event: AstrMessageEvent,
    ) -> PolicyDecision:
        decision = event.get_extra(self.EXTRA_DECISION)
        owner = event.get_extra(self.EXTRA_DECISION_OWNER)
        revision = event.get_extra(self.EXTRA_DECISION_REVISION)
        current_revision = self.store.revision if self.store is not None else -1
        if (
            isinstance(decision, PolicyDecision)
            and owner is self._decision_owner
            and revision == current_revision
        ):
            return decision
        decision = self._decision_for_event(event)
        self._cache_decision(event, decision)
        return decision

    def _cache_decision(
        self,
        event: AstrMessageEvent,
        decision: PolicyDecision,
    ) -> None:
        event.set_extra(self.EXTRA_DECISION, decision)
        event.set_extra(AUTO_PERSONA_EVENT_KEY, False)
        event.set_extra(
            self.EXTRA_DECISION_OWNER,
            self._decision_owner,
        )
        event.set_extra(
            self.EXTRA_DECISION_REVISION,
            self.store.revision if self.store is not None else -1,
        )

    def _is_available(self) -> bool:
        return (
            bool(self._setting("enabled", True))
            and self.store is not None
            and self.matcher is not None
        )

    def _setting(self, name: str, default: Any) -> Any:
        getter = getattr(self.webui_config, "get", None)
        return getter(name, default) if callable(getter) else default

    def _log_match(self, decision: PolicyDecision) -> None:
        if not bool(self._setting("log_policy_match", False)):
            return
        logger.info(
            "[用户策略] 平台=%s 用户=%s 群=%s 人格=%s 来源=%s",
            decision.identity.platform,
            decision.identity.user_id,
            decision.identity.group_id or "私聊",
            decision.persona_id or "跟随会话",
            decision.match_source,
        )

    def _can_use_query_command(
        self,
        event: AstrMessageEvent,
        decision: PolicyDecision,
    ) -> bool:
        if bool(self._setting("public_query_commands", False)):
            return True
        return bool(event.is_admin() or decision.allow_persona_switch)

    def _apply_event_plugin_scope(
        self,
        event: AstrMessageEvent,
        decision: PolicyDecision,
    ) -> None:
        catalog = self._runtime_plugin_catalog()
        current = getattr(event, "plugins_name", None)
        allowed = filter_plugin_scope(
            current,
            catalog,
            lambda plugin_name: bool(
                self.matcher
                and self.matcher.check_plugin_access(
                    decision,
                    plugin_name,
                ).allowed
            ),
            protected=self._is_protected_plugin,
        )
        if allowed is not None:
            event.plugins_name = allowed

        handlers = event.get_extra("activated_handlers")
        before_handler_count = len(handlers) if isinstance(handlers, list) else -1
        if isinstance(handlers, list):
            filtered = []
            for handler in handlers:
                module_path = str(
                    getattr(handler, "handler_module_path", "") or ""
                )
                plugin_name = self._plugin_name_for_module(module_path)
                if not plugin_name or self._is_protected_plugin(
                    plugin_name,
                    module_path,
                ):
                    filtered.append(handler)
                    continue
                if self.matcher and self.matcher.check_plugin_access(
                    decision,
                    plugin_name,
                ).allowed:
                    filtered.append(handler)
            # AstrBot 的处理阶段会持有该列表的原始引用，必须原地修改，
            # 仅替换事件字段无法阻止本次循环中的后续处理器。
            handlers[:] = filtered
            event.set_extra("activated_handlers", handlers)
        if bool(self._setting("log_policy_match", False)):
            logger.info(
                "[用户策略] 插件范围：plugins %s -> %s, handlers %s -> %s",
                current,
                getattr(event, "plugins_name", None),
                before_handler_count,
                len(handlers) if isinstance(handlers, list) else -1,
            )

    def _filter_llm_tools(
        self,
        request: ProviderRequest,
        decision: PolicyDecision,
    ) -> None:
        tool_set = getattr(request, "func_tool", None)
        tools = list(getattr(tool_set, "tools", []) or [])
        if not tools or self.matcher is None:
            return

        for tool in tools:
            tool_name = str(getattr(tool, "name", "") or "")
            module_path = str(
                getattr(tool, "handler_module_path", "") or ""
            )
            plugin_name = self._plugin_name_for_module(module_path)
            if (
                not tool_name
                or not plugin_name
                or self._is_protected_plugin(plugin_name, module_path)
            ):
                continue
            if self.matcher.check_plugin_access(
                decision,
                plugin_name,
            ).allowed:
                continue
            remover = getattr(tool_set, "remove_tool", None)
            if callable(remover):
                remover(tool_name)
            else:
                tool_set.tools = [
                    candidate
                    for candidate in tool_set.tools
                    if getattr(candidate, "name", None) != tool_name
                ]

    def plugin_catalog_for_ui(self) -> list[dict[str, Any]]:
        catalog = self._runtime_plugin_catalog()
        configured = self._configured_plugin_names()
        result = []
        for name, item in catalog.items():
            if self._is_protected_plugin(
                name,
                str(item.get("module_path", "")),
                bool(item.get("reserved", False)),
            ):
                continue
            result.append(
                {
                    "name": name,
                    "display_name": item["display_name"],
                    "version": item["version"],
                    "installed": True,
                    "activated": item["activated"],
                }
            )
        for name in configured - set(catalog):
            result.append(
                {
                    "name": name,
                    "display_name": name,
                    "version": "",
                    "installed": False,
                    "activated": False,
                }
            )
        result.sort(
            key=lambda item: (
                not item["installed"],
                item["display_name"].casefold(),
            )
        )
        return result

    def _runtime_plugin_catalog(self) -> dict[str, dict[str, Any]]:
        if self._plugin_catalog_cache is not None:
            return self._plugin_catalog_cache
        sources: list[Any] = []
        getter = getattr(self.context, "get_all_stars", None)
        if callable(getter):
            try:
                sources.append(getter())
            except Exception as exc:
                logger.warning(f"[用户策略] 读取 AstrBot 插件列表失败：{exc}")
        sources.append(star_map)
        self._plugin_catalog_cache = collect_plugin_catalog(sources)
        return self._plugin_catalog_cache

    def _invalidate_plugin_catalog(self) -> None:
        self._plugin_catalog_cache = None
        self._module_plugin_cache.clear()

    def _configured_plugin_names(self) -> set[str]:
        if self.store is None:
            return set()
        result = set()

        def add_access(access: Any) -> None:
            if isinstance(access, dict):
                result.update(
                    str(item) for item in access.get("plugins", [])
                )

        for rule in self.store.config.get("private_users", {}).values():
            add_access(rule.get("plugin_access"))
        for group in self.store.config.get("groups", {}).values():
            add_access(group.get("plugin_access"))
            for member in group.get("users", {}).values():
                add_access(member.get("plugin_access"))
        return {name for name in result if name}

    def _plugin_name_for_module(self, module_path: str) -> str:
        if not module_path:
            return ""
        cached = self._module_plugin_cache.get(module_path)
        if cached is not None:
            return cached
        metadata = star_map.get(module_path)
        if metadata is not None:
            result = str(self._value(metadata, "name", "") or "")
            self._module_plugin_cache[module_path] = result
            return result
        best_path = ""
        best_name = ""
        for registered_path, candidate in star_map.items():
            path = str(registered_path)
            if (
                module_path == path
                or module_path.startswith(f"{path}.")
            ) and len(path) > len(best_path):
                best_path = path
                best_name = str(
                    self._value(candidate, "name", "") or ""
                )
        self._module_plugin_cache[module_path] = best_name
        return best_name

    @staticmethod
    def _is_protected_plugin(
        plugin_name: str,
        module_path: str = "",
        reserved: bool = False,
    ) -> bool:
        return is_protected_plugin(
            plugin_name,
            module_path,
            reserved,
            own_plugin_name=PLUGIN_NAME,
        )

    async def _resolve_persona(
        self,
        requested: str,
    ) -> tuple[str, str]:
        return await self.personas.resolve_persona(requested)

    async def _set_personal_persona(
        self,
        event: AstrMessageEvent,
        persona_id: str,
        persona_mode: str,
    ) -> None:
        user_id = self._event_user_id(event)
        expected = self.store.revision if self.store else 0

        def mutate(config: dict[str, Any]) -> None:
            if event.is_private_chat():
                users = config["private_users"]
                rule = users.setdefault(
                    user_id,
                    {
                        "persona_mode": "default",
                        "persona_id": "",
                        "provider_id": "",
                        "auto_persona": {
                            "persona_ids": [],
                            "scenario": "",
                        },
                        "blocked": False,
                        "allow_persona_switch": True,
                        "memory_isolation": True,
                        "livingmemory_isolation": True,
                        "plugin_access": default_plugin_access(),
                    },
                )
                rule["persona_mode"] = persona_mode
                rule["persona_id"] = (
                    persona_id if persona_mode == "fixed" else ""
                )
                return

            group_id = self._event_group_id(event)
            groups = config["groups"]
            group = groups.setdefault(
                group_id,
                self._empty_group_rule(),
            )
            member = group["users"].setdefault(
                user_id,
                {
                    "persona_mode": "inherit",
                    "persona_id": "",
                    "provider_id": "",
                    "auto_persona": {
                        "persona_ids": [],
                        "scenario": "",
                    },
                    "allow_persona_switch": True,
                    "memory_isolation": None,
                    "livingmemory_isolation": None,
                    "plugin_access": default_plugin_access(inherit=True),
                },
            )
            member["persona_mode"] = persona_mode
            member["persona_id"] = (
                persona_id if persona_mode == "fixed" else ""
            )

        await self.update_policy(expected, mutate)

    async def _set_personal_memory_isolation(
        self,
        event: AstrMessageEvent,
        enabled: bool,
    ) -> None:
        user_id = self._event_user_id(event)
        expected = self.store.revision if self.store else 0

        def mutate(config: dict[str, Any]) -> None:
            if event.is_private_chat():
                rule = config["private_users"].setdefault(
                    user_id,
                    {
                        "persona_mode": "default",
                        "persona_id": "",
                        "provider_id": "",
                        "auto_persona": {
                            "persona_ids": [],
                            "scenario": "",
                        },
                        "blocked": False,
                        "allow_persona_switch": True,
                        "memory_isolation": True,
                        "livingmemory_isolation": True,
                        "plugin_access": default_plugin_access(),
                    },
                )
                rule["memory_isolation"] = enabled
                return

            group_id = self._event_group_id(event)
            group = config["groups"].setdefault(
                group_id,
                self._empty_group_rule(),
            )
            member = group["users"].setdefault(
                user_id,
                {
                    "persona_mode": "inherit",
                    "persona_id": "",
                    "provider_id": "",
                    "auto_persona": {
                        "persona_ids": [],
                        "scenario": "",
                    },
                    "allow_persona_switch": True,
                    "memory_isolation": None,
                    "livingmemory_isolation": None,
                    "plugin_access": default_plugin_access(inherit=True),
                },
            )
            member["memory_isolation"] = enabled

        await self.update_policy(expected, mutate)

    async def _set_group_persona(
        self,
        group_id: str,
        persona_id: str,
        persona_mode: str,
    ) -> None:
        expected = self.store.revision if self.store else 0

        def mutate(config: dict[str, Any]) -> None:
            group = config["groups"].setdefault(
                group_id,
                self._empty_group_rule(),
            )
            group["persona_mode"] = persona_mode
            group["persona_id"] = (
                persona_id if persona_mode == "fixed" else ""
            )

        await self.update_policy(expected, mutate)

    async def _set_personal_livingmemory_isolation(
        self,
        event: AstrMessageEvent,
        enabled: bool,
    ) -> None:
        user_id = self._event_user_id(event)
        expected = self.store.revision if self.store else 0

        def mutate(config: dict[str, Any]) -> None:
            if event.is_private_chat():
                rule = config["private_users"].setdefault(
                    user_id,
                    {
                        "persona_mode": "default",
                        "persona_id": "",
                        "provider_id": "",
                        "auto_persona": {
                            "persona_ids": [],
                            "scenario": "",
                        },
                        "blocked": False,
                        "allow_persona_switch": True,
                        "memory_isolation": True,
                        "livingmemory_isolation": True,
                        "plugin_access": default_plugin_access(),
                    },
                )
                rule["livingmemory_isolation"] = enabled
                return

            group = config["groups"].setdefault(
                self._event_group_id(event),
                self._empty_group_rule(),
            )
            member = group["users"].setdefault(
                user_id,
                {
                    "persona_mode": "inherit",
                    "persona_id": "",
                    "provider_id": "",
                    "auto_persona": {
                        "persona_ids": [],
                        "scenario": "",
                    },
                    "allow_persona_switch": True,
                    "memory_isolation": None,
                    "livingmemory_isolation": None,
                    "plugin_access": default_plugin_access(inherit=True),
                },
            )
            member["livingmemory_isolation"] = enabled

        await self.update_policy(expected, mutate)

    def _schedule_rules(self) -> dict[str, dict[str, Any]]:
        if self.store is None:
            return {}
        schedules = self.store.config.get("schedules", {})
        return schedules if isinstance(schedules, dict) else {}

    async def _apply_scheduled_persona(
        self,
        schedule_id: str,
        plan: dict[str, Any],
        persona_id: str,
    ) -> None:
        if self.store is None:
            raise PolicyConfigError("策略存储尚未初始化。")
        available_personas = {
            str(item.get("persona_id", ""))
            for item in await self.personas.list_personas()
        }
        if persona_id not in available_personas:
            raise PolicyConfigError(
                f"计划人格“{persona_id}”当前不可用，请编辑计划。"
            )

        async with self.policy_lock:
            def mutate(config: dict[str, Any]) -> None:
                schedule = config.get("schedules", {}).get(schedule_id)
                if not isinstance(schedule, dict) or not schedule.get(
                    "enabled",
                    True,
                ):
                    raise PolicyConfigError("人格计划已被删除或停用。")
                allowed_personas = (
                    {str(schedule.get("persona_id", ""))}
                    if schedule.get("mode") == "daily"
                    else {
                        str(item)
                        for item in schedule.get("persona_ids", [])
                    }
                )
                if persona_id not in allowed_personas:
                    raise PolicyConfigError(
                        "人格计划已被更新，本次旧任务不再执行。"
                    )

                target_type = str(schedule.get("target_type", ""))
                user_id = str(schedule.get("user_id", ""))
                group_id = str(schedule.get("group_id", ""))
                if target_type == "private":
                    target = config["private_users"].get(user_id)
                    if not isinstance(target, dict):
                        raise PolicyConfigError("计划目标私聊用户已不存在。")
                elif target_type == "group":
                    target = config["groups"].get(group_id)
                    if not isinstance(target, dict):
                        raise PolicyConfigError("计划目标群聊已不存在。")
                else:
                    group = config["groups"].get(group_id)
                    target = (
                        group.get("users", {}).get(user_id)
                        if isinstance(group, dict)
                        else None
                    )
                    if not isinstance(target, dict):
                        raise PolicyConfigError("计划目标群成员设置已不存在。")
                target["persona_mode"] = "fixed"
                target["persona_id"] = persona_id

            config = self.store.update(self.store.revision, mutate)
            self.matcher = PolicyMatcher(config)

    async def _can_manage_group(self, event: AstrMessageEvent) -> bool:
        user_id = self._event_user_id(event)
        group_rule = self._group_rule_for_event(event)

        raw = getattr(
            getattr(event, "message_obj", None),
            "raw_message",
            None,
        )
        raw_role = ""
        if isinstance(raw, dict):
            sender = raw.get("sender", {})
            if isinstance(sender, dict):
                raw_role = str(sender.get("role", "") or "")

        group = getattr(getattr(event, "message_obj", None), "group", None)
        if is_group_manager(
            user_id,
            group_rule,
            is_astrbot_admin=event.is_admin(),
            raw_role=raw_role,
            group=group,
        ):
            return True

        getter = getattr(event, "get_group", None)
        if not callable(getter):
            return False
        try:
            group = getter()
            if inspect.isawaitable(group):
                group = await group
        except Exception:
            return False
        return is_group_manager(user_id, group_rule, group=group)

    def _group_rule(self, group_id: str) -> dict[str, Any]:
        if self.store is None:
            return {}
        rule = self.store.config.get("groups", {}).get(group_id, {})
        return rule if isinstance(rule, dict) else {}

    def _group_rule_for_event(self, event: AstrMessageEvent) -> dict[str, Any]:
        if self.store is None:
            return {}
        groups = self.store.config.get("groups", {})
        for group_id in self._group_event_aliases(event):
            rule = groups.get(group_id, {})
            if isinstance(rule, dict):
                return rule
        return self._group_rule(self._event_group_id(event))

    def _group_policy_id_for_event(self, event: AstrMessageEvent) -> str:
        if self.store is None:
            return self._event_group_id(event)
        groups = self.store.config.get("groups", {})
        for group_id in self._group_event_aliases(event):
            if group_id in groups:
                return group_id
        return self._event_group_id(event)

    @staticmethod
    def _empty_group_rule() -> dict[str, Any]:
        return {
            "description": "",
            "persona_mode": "default",
            "persona_id": "",
            "provider_id": "",
            "auto_persona": {
                "persona_ids": [],
                "scenario": "",
            },
            "memory_isolation": True,
            "livingmemory_isolation": True,
            "plugin_access": default_plugin_access(),
            "member_access": {"mode": "all", "users": []},
            "policy_admins": [],
            "users": {},
        }

    @staticmethod
    def _command_argument(
        event: AstrMessageEvent,
        *command_names: str,
    ) -> str:
        values = [
            str(event.get_message_str() or ""),
            str(getattr(event.message_obj, "message_str", "") or ""),
        ]
        for value in values:
            normalized = value.strip()
            if normalized.startswith("/"):
                normalized = normalized[1:].lstrip()
            for name in command_names:
                if normalized == name:
                    return ""
                if normalized.startswith(f"{name} "):
                    return normalized[len(name) :].strip()
        return ""

    @staticmethod
    def _parse_switch(value: str) -> bool | None:
        normalized = str(value or "").strip().casefold()
        if normalized in {"开启", "打开", "开", "on", "true", "1"}:
            return True
        if normalized in {"关闭", "关", "off", "false", "0"}:
            return False
        return None

    @staticmethod
    def _looks_like_command(event: AstrMessageEvent) -> bool:
        return any(
            value.lstrip().startswith("/")
            for value in (
                str(event.get_message_str() or ""),
                str(getattr(event.message_obj, "message_str", "") or ""),
            )
        )

    @staticmethod
    def _is_private_companion_internal_image_event(
        event: AstrMessageEvent,
    ) -> bool:
        keys = (
            "private_companion_deferred_private_image_only",
            "private_companion_deferred_private_image_only_ready",
            "private_companion_delayed_image_sources",
            "private_companion_delayed_image_vision_text",
        )
        getter = getattr(event, "get_extra", None)
        for key in keys:
            if getattr(event, key, None):
                return True
            if callable(getter):
                try:
                    if getter(key):
                        return True
                except Exception:
                    pass
        return False

    @staticmethod
    def _event_unified_msg_origin(event: AstrMessageEvent) -> str:
        value = getattr(event, "unified_msg_origin", "")
        if callable(value):
            try:
                value = value()
            except Exception:
                value = ""
        return str(value or "")

    @staticmethod
    def _event_platform_id(event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_platform_id", None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return str(value)
            except Exception:
                pass
        getter = getattr(event, "get_platform_name", None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return str(value)
            except Exception:
                pass
        return ""

    def _private_event_aliases(self, event: AstrMessageEvent) -> list[str]:
        aliases = []
        self._append_identity_alias(
            aliases,
            self._event_unified_msg_origin(event),
        )
        for attr in (
            "session_id",
            "session",
            "conversation_id",
            "user_id",
            "sender_id",
        ):
            self._append_identity_alias(
                aliases,
                getattr(getattr(event, "message_obj", None), attr, ""),
            )
        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        for attr in ("user_id", "sender_id", "id"):
            self._append_identity_alias(aliases, getattr(sender, attr, ""))
        return aliases

    def _group_event_aliases(self, event: AstrMessageEvent) -> list[str]:
        aliases = []
        self._append_identity_alias(
            aliases,
            self._event_unified_msg_origin(event),
        )
        for attr in (
            "session_id",
            "session",
            "conversation_id",
            "group_id",
        ):
            self._append_identity_alias(
                aliases,
                getattr(getattr(event, "message_obj", None), attr, ""),
            )
        group = getattr(getattr(event, "message_obj", None), "group", None)
        self._append_identity_alias(aliases, getattr(group, "group_id", ""))
        return aliases

    @staticmethod
    def _append_identity_alias(aliases: list[str], value: Any) -> None:
        raw = str(value or "").strip()
        if not raw:
            return
        candidates = [raw]
        parsed = parse_session_identity(raw)
        if parsed is not None:
            if parsed.is_private:
                candidates.append(f"{parsed.platform}:{parsed.user_id}")
                candidates.append(parsed.user_id)
            else:
                candidates.append(f"{parsed.platform}:{parsed.group_id}")
                candidates.append(parsed.group_id)
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
    def _event_user_id(event: AstrMessageEvent) -> str:
        try:
            value = event.get_sender_id()
        except Exception:
            value = ""
        if value:
            return str(value)
        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        return str(getattr(sender, "user_id", "") or "")

    @staticmethod
    def _event_group_id(event: AstrMessageEvent) -> str:
        try:
            value = event.get_group_id()
        except Exception:
            value = ""
        if value:
            return str(value)
        message_obj = getattr(event, "message_obj", None)
        direct = getattr(message_obj, "group_id", "")
        if direct:
            return str(direct)
        group = getattr(message_obj, "group", None)
        nested = getattr(group, "group_id", "")
        return str(nested or "")

    @staticmethod
    def _value(source: Any, name: str, default: Any = None) -> Any:
        if isinstance(source, dict):
            return source.get(name, default)
        return getattr(source, name, default)
