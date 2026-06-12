"""AstrBot 人格读取与单次请求适配。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


PERSONA_OVERRIDE_MARKER = "【用户策略选择的 AstrBot 人格】"
ASTRBOT_PERSONA_HEADING = "# Persona Instructions"
ASTRBOT_PERSONA_BLOCK_RE = re.compile(
    rf"(?ms)(^|\r?\n){re.escape(ASTRBOT_PERSONA_HEADING)}[ \t]*(?:\r?\n)+(.*?)(?=\r?\n# |\Z)"
)
TRAILING_POLICY_PERSONA_BLOCK_RE = re.compile(
    rf"(?ms)(^|\r?\n){re.escape(PERSONA_OVERRIDE_MARKER)}\r?\n人格 ID：.*\Z"
)


def _replace_astrbot_persona_block(
    system_prompt: str,
    selected_persona_prompt: str,
) -> tuple[str, bool]:
    """替换 AstrBot 已注入的原生人格段。"""

    match = ASTRBOT_PERSONA_BLOCK_RE.search(system_prompt)
    if match is None:
        return system_prompt, False

    newline = "\r\n" if "\r\n" in system_prompt else "\n"
    before = system_prompt[: match.start()].rstrip()
    after = system_prompt[match.end() :].lstrip("\r\n")
    block = (
        f"{ASTRBOT_PERSONA_HEADING}"
        f"{newline}{newline}"
        f"{selected_persona_prompt}"
    )
    parts = [part for part in (before, block, after) if part]
    return f"{newline}{newline}".join(parts), True


def _remove_trailing_policy_persona_block(system_prompt: str) -> tuple[str, bool]:
    """清理早期版本追加在末尾的用户策略人格覆盖段。"""

    match = TRAILING_POLICY_PERSONA_BLOCK_RE.search(system_prompt)
    if match is None:
        return system_prompt, False
    return system_prompt[: match.start()].rstrip(), True


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def replace_persona_prompt(
    system_prompt: str,
    current_persona_prompt: str,
    selected_persona_prompt: str,
    persona_id: str,
) -> tuple[str, str]:
    """替换当前人格段，并保留其他插件附加的系统提示。"""

    original = system_prompt or ""
    current = (current_persona_prompt or "").strip()
    selected = (selected_persona_prompt or "").strip()
    if not selected:
        return original, "empty"

    original, policy_removed = _remove_trailing_policy_persona_block(original)
    without_native, native_replaced = _replace_astrbot_persona_block(
        original,
        selected,
    )
    if native_replaced:
        original = without_native
    prompt_changed = policy_removed or native_replaced

    if current and current in original:
        return original.replace(current, selected, 1), "replaced"

    section = f"{PERSONA_OVERRIDE_MARKER}\n人格 ID：{persona_id}\n{selected}"
    if section in original:
        return original, "replaced" if prompt_changed else "existing"
    if selected in original and (not current or current == selected):
        return original, "replaced" if prompt_changed else "existing"
    if not original.strip():
        return section, "overlay"
    if native_replaced:
        return original, "replaced"
    return f"{original.rstrip()}\n\n{section}", "overlay"


@dataclass(frozen=True)
class PersonaApplyResult:
    """一次人格应用结果。"""

    persona_id: str
    found: bool
    prompt_mode: str = "skipped"
    conversation_persona_id: str = ""
    removed_tools: tuple[str, ...] = ()


class AstrBotPersonaAdapter:
    """通过 AstrBot 官方 PersonaManager 读取并应用人格。"""

    def __init__(self, context: Any):
        self.context = context

    @property
    def available(self) -> bool:
        manager = getattr(self.context, "persona_manager", None)
        return manager is not None and callable(
            getattr(manager, "get_all_personas", None)
        )

    async def list_personas(self) -> list[dict[str, Any]]:
        manager = getattr(self.context, "persona_manager", None)
        getter = getattr(manager, "get_all_personas", None)
        if not callable(getter):
            return []

        personas = await getter()
        result = []
        for persona in personas or []:
            persona_id = str(
                _value(persona, "persona_id", _value(persona, "id", ""))
                or ""
            ).strip()
            if not persona_id:
                continue
            prompt = str(_value(persona, "system_prompt", "") or "")
            tools = _value(persona, "tools", None)
            begin_dialogs = _value(persona, "begin_dialogs", None)
            result.append(
                {
                    "persona_id": persona_id,
                    "name": str(
                        _value(
                            persona,
                            "name",
                            _value(persona, "persona_name", persona_id),
                        )
                        or persona_id
                    ).strip(),
                    "prompt_summary": prompt.strip()[:160],
                    "tools": list(tools) if isinstance(tools, list) else None,
                    "begin_dialogs_count": (
                        len(begin_dialogs)
                        if isinstance(begin_dialogs, list)
                        else 0
                    ),
                }
            )
        result.sort(key=lambda item: item["persona_id"].casefold())
        return result

    async def resolve_persona(self, query: str) -> tuple[str, str]:
        """按准确 ID、唯一 ID 或显示名称匹配人格。"""

        value = str(query or "").strip()
        personas = await self.list_personas()
        exact = [
            item for item in personas if item["persona_id"] == value
        ]
        if exact:
            return exact[0]["persona_id"], ""

        folded = value.casefold()
        matches = [
            item
            for item in personas
            if item["persona_id"].casefold() == folded
            or str(item.get("name", "")).casefold() == folded
        ]
        unique_ids = list(
            dict.fromkeys(item["persona_id"] for item in matches)
        )
        if len(unique_ids) == 1:
            return unique_ids[0], ""
        if len(unique_ids) > 1:
            return "", "人格名称不唯一，请改用人格 ID：" + "、".join(
                unique_ids
            )

        candidates = "、".join(
            item["persona_id"] for item in personas[:8]
        )
        suffix = f"\n可用人格：{candidates}" if candidates else ""
        return "", f"未找到人格“{value}”。{suffix}"

    async def get_persona_prompt(self, persona_id: str) -> str:
        """读取指定 AstrBot 人格的完整系统提示。"""

        selected_id = str(persona_id or "").strip()
        if not selected_id:
            return ""
        manager = getattr(self.context, "persona_manager", None)
        getter = getattr(manager, "get_persona", None)
        if not callable(getter):
            return ""
        persona = await getter(selected_id)
        if persona is None:
            return ""
        return str(
            _value(persona, "system_prompt", _value(persona, "prompt", ""))
            or ""
        )

    async def apply_to_request(
        self,
        persona_id: str,
        request: Any,
        unified_msg_origin: str = "",
        source_conversation: Any = None,
    ) -> PersonaApplyResult:
        selected_id = str(persona_id or "").strip()
        if not selected_id and source_conversation is None:
            return PersonaApplyResult(persona_id="", found=True)

        manager = getattr(self.context, "persona_manager", None)
        getter = getattr(manager, "get_persona", None)
        if manager is None:
            return PersonaApplyResult(persona_id=selected_id, found=False)

        selected = None
        effective_id = selected_id
        if selected_id and callable(getter):
            selected = await getter(selected_id)
        elif not selected_id:
            target_conversation = getattr(request, "conversation", None)
            effective_id = str(
                _value(target_conversation, "persona_id", "") or ""
            ).strip()
            if effective_id and callable(getter):
                selected = await getter(effective_id)
            if selected is None:
                default_getter = getattr(
                    manager,
                    "get_default_persona_v3",
                    None,
                )
                if callable(default_getter) and unified_msg_origin:
                    selected = await default_getter(unified_msg_origin)

        if selected is None:
            return PersonaApplyResult(
                persona_id=selected_id,
                found=not bool(selected_id),
            )

        selected_prompt = str(
            _value(
                selected,
                "system_prompt",
                _value(selected, "prompt", ""),
            )
            or ""
        )
        current_prompt = await self._current_persona_prompt(
            manager,
            request,
            unified_msg_origin,
            source_conversation=source_conversation,
        )
        updated_prompt, prompt_mode = replace_persona_prompt(
            str(getattr(request, "system_prompt", "") or ""),
            current_prompt,
            selected_prompt,
            effective_id or "AstrBot 默认人格",
        )
        request.system_prompt = updated_prompt
        conversation_persona_id = self._apply_conversation_persona_id(
            request,
            effective_id,
        )
        removed_tools = self._limit_tools(
            getattr(request, "func_tool", None),
            _value(selected, "tools", None),
        )
        return PersonaApplyResult(
            persona_id=selected_id,
            found=True,
            prompt_mode=prompt_mode,
            conversation_persona_id=conversation_persona_id,
            removed_tools=tuple(removed_tools),
        )

    async def _current_persona_prompt(
        self,
        manager: Any,
        request: Any,
        unified_msg_origin: str,
        source_conversation: Any = None,
    ) -> str:
        conversation = (
            source_conversation
            if source_conversation is not None
            else getattr(request, "conversation", None)
        )
        current_id = str(_value(conversation, "persona_id", "") or "").strip()
        getter = getattr(manager, "get_persona", None)
        if current_id and callable(getter):
            current = await getter(current_id)
            if current is not None:
                return str(_value(current, "system_prompt", "") or "")

        default_getter = getattr(manager, "get_default_persona_v3", None)
        if callable(default_getter) and unified_msg_origin:
            current = await default_getter(unified_msg_origin)
            return str(
                _value(
                    current,
                    "prompt",
                    _value(current, "system_prompt", ""),
                )
                or ""
            )
        return ""

    @staticmethod
    def _apply_conversation_persona_id(
        request: Any,
        persona_id: str,
    ) -> str:
        conversation = getattr(request, "conversation", None)
        if conversation is None:
            return ""
        selected_id = str(persona_id or "").strip()
        if not selected_id:
            return ""
        try:
            setattr(conversation, "persona_id", selected_id)
        except Exception:
            return ""
        return selected_id

    @staticmethod
    def _limit_tools(tool_set: Any, allowed_tools: Any) -> list[str]:
        if allowed_tools is None or tool_set is None:
            return []
        allowed = {
            str(name).strip()
            for name in allowed_tools
            if str(name).strip()
        }
        tools = list(getattr(tool_set, "tools", []) or [])
        removed = []
        for tool in tools:
            name = str(getattr(tool, "name", "") or "")
            if not name or name in allowed:
                continue
            remover = getattr(tool_set, "remove_tool", None)
            if callable(remover):
                remover(name)
            else:
                tool_set.tools = [
                    candidate
                    for candidate in tool_set.tools
                    if getattr(candidate, "name", None) != name
                ]
            removed.append(name)
        return removed

    async def diagnostics(
        self,
        configured_ids: set[str],
    ) -> list[dict[str, str]]:
        if not self.available:
            return [
                {
                    "level": "error",
                    "title": "AstrBot 人格接口不可用",
                    "message": "当前 AstrBot 未提供 persona_manager，无法使用人格下拉选择。",
                }
            ]
        personas = await self.list_personas()
        available_ids = {item["persona_id"] for item in personas}
        missing = sorted(configured_ids - available_ids)
        if not missing:
            return []
        return [
            {
                "level": "warning",
                "title": "存在失效的人格绑定",
                "message": "以下人格已不在 AstrBot 人格设定中：" + "、".join(missing),
            }
        ]

    @staticmethod
    def serialize_result(result: PersonaApplyResult) -> dict[str, Any]:
        return asdict(result)
