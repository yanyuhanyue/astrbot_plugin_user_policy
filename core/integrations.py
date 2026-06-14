"""第三方插件兼容状态与职责边界。"""

from __future__ import annotations

from typing import Any


INTEGRATION_CATALOG = (
    {
        "names": ("astrbot_plugin_private_companion",),
        "label": "Private Companion",
        "role": "关系、陪伴状态、记忆与主动行为",
        "boundary": (
            "可限制私聊主动对话并保护私聊识图等待；不读取陪伴数据，"
            "群聊插话仍由原插件控制。"
        ),
    },
    {
        "names": ("LivingMemory", "astrbot_plugin_livingmemory"),
        "label": "LivingMemory",
        "role": "长期记忆检索与写入",
        "boundary": "可按最终人格隔离召回与写入；不修改历史记忆数据库。",
    },
    {
        "names": ("meme_manager", "astrbot_plugin_meme_manager"),
        "label": "Meme Manager",
        "role": "表情管理与回复装饰",
        "boundary": "本插件不改变表情选择、收藏或发送逻辑。",
    },
    {
        "names": ("astrbot_plugin_smart_imagechat_hub",),
        "label": "Smart ImageChat Hub",
        "role": "智能搜图、主动表情、偷图与斗图",
        "boundary": (
            "可按最终人格隔离发图候选并管理独立标签；"
            "不修改原插件索引、配置或缓冲池结构。"
        ),
    },
    {
        "names": ("astrbot_plugin_proactive_chat",),
        "label": "Proactive Chat",
        "role": "主动聊天调度",
        "boundary": "主动消息可沿用会话最后有效人格和隔离对话历史。",
    },
    {
        "names": ("astrbot_plugin_life_scheduler",),
        "label": "Life Scheduler",
        "role": "生活日程生成与注入",
        "boundary": "已映射人格使用本插件日程库，未映射人格沿用全局日程。",
    },
    {
        "names": ("astrbot_plugin_gitee_aiimg",),
        "label": "Gitee AI Image",
        "role": "图片、改图与视频生成",
        "boundary": "本插件只控制用户能否触发，不修改生图服务参数。",
    },
    {
        "names": ("astrbot_plugin_qq_group_daily_analysis",),
        "label": "QQ Group Daily Analysis",
        "role": "群聊每日分析与定时报告",
        "boundary": "定时分析继续独立运行；仅用户主动命令受插件权限限制。",
    },
)


class IntegrationInspector:
    """读取已加载插件，但不读取任何第三方插件私有配置。"""

    def __init__(
        self,
        context: Any,
        fallback_plugins: dict[str, Any] | None = None,
        plugin: Any | None = None,
    ):
        self.context = context
        self.fallback_plugins = fallback_plugins or {}
        self.plugin = plugin

    def report(self) -> dict[str, Any]:
        loaded = self._loaded_plugins()
        items = []
        for integration in INTEGRATION_CATALOG:
            matched_name = next(
                (name for name in integration["names"] if name in loaded),
                "",
            )
            metadata = loaded.get(matched_name)
            item = (
                {
                    "name": matched_name or integration["names"][0],
                    "label": integration["label"],
                    "role": integration["role"],
                    "boundary": integration["boundary"],
                    "detected": metadata is not None,
                    "activated": (
                        bool(self._value(metadata, "activated", True))
                        if metadata is not None
                        else False
                    ),
                    "version": (
                        str(self._value(metadata, "version", "") or "")
                        if metadata is not None
                        else ""
                    ),
                }
            )
            if integration["names"][0] == "meme_manager":
                self._attach_meme_adapter_status(item)
            if integration["names"][0] == "astrbot_plugin_gitee_aiimg":
                self._attach_gitee_aiimg_adapter_status(item)
            if integration["names"][0] == "astrbot_plugin_smart_imagechat_hub":
                self._attach_adapter_status(
                    item,
                    "smart_imagechat_adapter",
                    "smart_image_isolation",
                )
            if integration["label"] == "LivingMemory":
                self._attach_adapter_status(
                    item,
                    "livingmemory_adapter",
                    "livingmemory_persona_adapter",
                )
            if integration["label"] == "Private Companion":
                self._attach_adapter_status(
                    item,
                    "private_companion_adapter",
                    "private_companion_proactive",
                )
            if integration["label"] == "Proactive Chat":
                self._attach_adapter_status(
                    item,
                    "proactive_chat_adapter",
                    "proactive_persona",
                )
            if integration["label"] == "Life Scheduler":
                self._attach_adapter_status(
                    item,
                    "life_scheduler_adapter",
                    "life_schedule_persona",
                )
            items.append(item)

        diagnostics = [
            {
                "level": "success",
                "title": "低冲突设计",
                "message": (
                    "这是兼容边界说明，不是可切换模式。本插件不会修改"
                    "其他插件的配置或数据库。"
                ),
            }
        ]
        daily_analysis = next(
            (
                item
                for item in items
                if item["name"] == "astrbot_plugin_qq_group_daily_analysis"
            ),
            None,
        )
        if daily_analysis and daily_analysis["activated"]:
            diagnostics.append(
                {
                    "level": "info",
                    "title": "群分析人格提示",
                    "message": (
                        "已检测到 QQ Group Daily Analysis。若该插件启用了"
                        "强制人格，其分析任务将优先使用自身设置；本插件不会读取"
                        "或覆盖该设置。"
                    ),
                }
            )
        return {"items": items, "diagnostics": diagnostics}

    def _attach_adapter_status(
        self,
        item: dict[str, Any],
        attribute: str,
        field: str,
    ) -> None:
        adapter = getattr(self.plugin, attribute, None)
        if adapter is None:
            return
        status = adapter.report()
        item[field] = status
        if status.get("message"):
            item["boundary"] = status["message"]

    def _attach_meme_adapter_status(self, item: dict[str, Any]) -> None:
        adapter = getattr(self.plugin, "meme_manager_adapter", None)
        if adapter is None:
            return
        status = adapter.report()
        item["meme_isolation"] = status
        if status.get("applied"):
            item["boundary"] = status["message"]
            return
        if status.get("enabled"):
            item["boundary"] = f"表情包库隔离已配置但未生效：{status['message']}"

    def _attach_gitee_aiimg_adapter_status(self, item: dict[str, Any]) -> None:
        adapter = getattr(self.plugin, "gitee_aiimg_adapter", None)
        if adapter is None:
            return
        status = adapter.report()
        item["gitee_aiimg_effects"] = status
        if status.get("applied"):
            item["boundary"] = status["message"]
            return
        if status.get("enabled"):
            item["boundary"] = f"人格生图效果已配置但未生效：{status['message']}"

    def _loaded_plugins(self) -> dict[str, Any]:
        getter = getattr(self.context, "get_all_stars", None)
        result = {}
        if callable(getter):
            try:
                stars = getter() or []
                values = stars.values() if isinstance(stars, dict) else stars
                self._collect(result, values)
            except Exception:
                pass
        self._collect(result, self.fallback_plugins.values())
        return result

    def _collect(self, result: dict[str, Any], values: Any) -> None:
        for metadata in values or []:
            name = str(self._value(metadata, "name", "") or "").strip()
            if name and name not in result:
                result[name] = metadata

    @staticmethod
    def _value(source: Any, name: str, default: Any = None) -> Any:
        if isinstance(source, dict):
            return source.get(name, default)
        return getattr(source, name, default)
