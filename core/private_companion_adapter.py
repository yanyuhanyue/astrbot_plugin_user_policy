"""Private Companion 私聊主动对话的用户策略门控。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import time
from functools import wraps
from typing import Any, Callable

from .matcher import EventIdentity, parse_session_identity
from .runtime_helpers import find_plugin


PRIVATE_COMPANION_NAMES = {"astrbot_plugin_private_companion"}
PROACTIVE_METHOD = "_user_enabled_for_proactive"
IMAGE_DEBOUNCE_METHOD = "_message_debounce_seconds"
IMAGE_VISION_METHOD = "_transcribe_private_inbound_images"
IMAGE_FAILURE_COOLDOWN_SECONDS = 30.0
log = logging.getLogger(__name__)


class PrivateCompanionProactiveAdapter:
    """在原插件判断之后附加全局、用户和插件权限检查。"""

    def __init__(
        self,
        plugin: Any,
        fallback_plugins: dict[str, Any] | None = None,
    ):
        self.plugin = plugin
        self.fallback_plugins = fallback_plugins or {}
        self.target: Any | None = None
        self.installed = False
        self.activated = False
        self.applied = False
        self.message = "未检测到 Private Companion。"
        self._original: Callable[..., Any] | None = None
        self._wrapped: Callable[..., Any] | None = None
        self.image_applied = False
        self.image_message = "快速识图保护尚未应用。"
        self._image_originals: dict[str, Callable[..., Any]] = {}
        self._image_wrapped: dict[str, Callable[..., Any]] = {}
        self._vision_tasks: dict[str, asyncio.Task[Any]] = {}
        self._vision_failures: dict[str, float] = {}

    def configure(self) -> None:
        target, metadata = find_plugin(
            self.plugin.context,
            self.fallback_plugins,
            PRIVATE_COMPANION_NAMES,
        )
        if target is None:
            self.restore()
            self.installed = False
            self.activated = False
            self.applied = False
            self.message = "未检测到 Private Companion。"
            return

        self.installed = True
        self.activated = bool(
            getattr(metadata, "activated", True)
            if not isinstance(metadata, dict)
            else metadata.get("activated", True)
        )
        if not self.activated:
            self.restore()
            self.target = target
            self.applied = False
            self.message = "Private Companion 当前未启用。"
            return

        if self.target is not None and self.target is not target:
            self.restore()
        self.target = target
        current = getattr(target, PROACTIVE_METHOD, None)
        if self._compatible_method(current):
            if current is not self._wrapped:
                self._original = current
                wrapped = self._wrap(current)
                self._wrapped = wrapped
                setattr(target, PROACTIVE_METHOD, wrapped)
            self.applied = True
        else:
            self.applied = False
            self.message = (
                "当前 Private Companion 版本暂不兼容，"
                "已安全回退为插件自身控制。"
            )
        self._configure_image_latency(target)
        self.message = self._success_message()

    def restore(self) -> None:
        if (
            self.target is not None
            and self._original is not None
            and getattr(self.target, PROACTIVE_METHOD, None) is self._wrapped
        ):
            setattr(self.target, PROACTIVE_METHOD, self._original)
        self._original = None
        self._wrapped = None
        for method_name, original in self._image_originals.items():
            wrapped = self._image_wrapped.get(method_name)
            if (
                self.target is not None
                and wrapped is not None
                and getattr(self.target, method_name, None) is wrapped
            ):
                setattr(self.target, method_name, original)
        for task in self._vision_tasks.values():
            if not task.done():
                task.cancel()
        self._image_originals.clear()
        self._image_wrapped.clear()
        self._vision_tasks.clear()
        self._vision_failures.clear()
        self.image_applied = False
        self.image_message = "快速识图保护尚未应用。"
        self.applied = False

    def report(self) -> dict[str, Any]:
        globally_enabled = self._globally_enabled()
        return {
            "installed": self.installed,
            "activated": self.activated,
            "applied": self.applied,
            "enabled": globally_enabled,
            "global_enabled": globally_enabled,
            "message": self.message,
            "image_latency": {
                "enabled": self._image_fast_enabled(),
                "applied": self.image_applied,
                "debounce_seconds": self._image_debounce_seconds(),
                "vision_timeout_seconds": self._image_timeout_seconds(),
                "message": self.image_message,
            },
            "scope": (
                "全局总闸开启，逐用户开关可继续关闭。"
                if globally_enabled
                else "全局总闸关闭，逐用户开关无法单独放行。"
            ),
        }

    def _wrap(
        self,
        original: Callable[..., Any],
    ) -> Callable[..., Any]:
        @wraps(original)
        def wrapper(*args, **kwargs):
            if not bool(original(*args, **kwargs)):
                return False
            try:
                user_id = (
                    args[0]
                    if args
                    else kwargs.get("user_id", "")
                )
                return self._allowed_by_policy(str(user_id or ""))
            except Exception:
                return True

        setattr(wrapper, "_user_policy_private_companion_wrapped", True)
        setattr(wrapper, "_user_policy_private_companion_original", original)
        return wrapper

    def _configure_image_latency(self, target: Any) -> None:
        if not self._image_fast_enabled():
            self.image_applied = False
            self.image_message = "快速识图保护已关闭，完全沿用原插件等待设置。"
            return
        debounce = getattr(target, IMAGE_DEBOUNCE_METHOD, None)
        vision = getattr(target, IMAGE_VISION_METHOD, None)
        if not self._compatible_debounce_method(debounce):
            self.image_applied = False
            self.image_message = "当前版本缺少兼容的图片防抖入口。"
            return
        if not self._compatible_vision_method(vision):
            self.image_applied = False
            self.image_message = "当前版本缺少兼容的图片视觉入口。"
            return

        debounce_wrapped = self._wrap_image_debounce(debounce)
        vision_wrapped = self._wrap_image_vision(vision)
        self._image_originals = {
            IMAGE_DEBOUNCE_METHOD: debounce,
            IMAGE_VISION_METHOD: vision,
        }
        self._image_wrapped = {
            IMAGE_DEBOUNCE_METHOD: debounce_wrapped,
            IMAGE_VISION_METHOD: vision_wrapped,
        }
        setattr(target, IMAGE_DEBOUNCE_METHOD, debounce_wrapped)
        setattr(target, IMAGE_VISION_METHOD, vision_wrapped)
        self.image_applied = True
        self.image_message = (
            "快速识图保护已启用：图片防抖最多 "
            f"{self._image_debounce_seconds():g} 秒，单次视觉任务最多 "
            f"{self._image_timeout_seconds():g} 秒，相同图片超时后不重复识图。"
        )

    def _wrap_image_debounce(
        self,
        original: Callable[..., Any],
    ) -> Callable[..., Any]:
        @wraps(original)
        def wrapper(kind: str = "text", *args, **kwargs):
            value = original(kind, *args, **kwargs)
            if kind != "image" or not self._image_fast_enabled():
                return value
            try:
                return min(
                    max(0.0, float(value)),
                    self._image_debounce_seconds(),
                )
            except (TypeError, ValueError):
                return self._image_debounce_seconds()

        self._mark_wrapper(wrapper, original)
        return wrapper

    def _wrap_image_vision(
        self,
        original: Callable[..., Any],
    ) -> Callable[..., Any]:
        @wraps(original)
        async def wrapper(*args, **kwargs):
            if not self._image_fast_enabled():
                return await original(*args, **kwargs)
            key = self._vision_key(args, kwargs)
            now = time.monotonic()
            if self._vision_failures.get(key, 0.0) > now:
                return ""
            task = self._vision_tasks.get(key)
            if task is None or task.done():
                task = asyncio.create_task(original(*args, **kwargs))
                self._vision_tasks[key] = task
                task.add_done_callback(
                    lambda completed, cache_key=key: (
                        self._vision_task_done(cache_key, completed)
                    )
                )
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=self._image_timeout_seconds(),
                )
            except asyncio.TimeoutError:
                self._vision_failures[key] = (
                    time.monotonic() + IMAGE_FAILURE_COOLDOWN_SECONDS
                )
                if not task.done():
                    task.cancel()
                log.info(
                    "[用户策略] Private Companion 图片视觉任务达到 %.1f 秒"
                    "上限，已停止本轮并阻止同图重复识别。",
                    self._image_timeout_seconds(),
                )
                return ""
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
                return ""
            if not str(result or "").strip():
                self._vision_failures[key] = (
                    time.monotonic() + IMAGE_FAILURE_COOLDOWN_SECONDS
                )
            return result

        self._mark_wrapper(wrapper, original)
        return wrapper

    def _vision_task_done(
        self,
        key: str,
        task: asyncio.Task[Any],
    ) -> None:
        if self._vision_tasks.get(key) is task:
            self._vision_tasks.pop(key, None)
        try:
            task.result()
        except BaseException:
            pass

    @staticmethod
    def _mark_wrapper(
        wrapper: Callable[..., Any],
        original: Callable[..., Any],
    ) -> None:
        setattr(wrapper, "_user_policy_private_companion_wrapped", True)
        setattr(wrapper, "_user_policy_private_companion_original", original)

    @staticmethod
    def _vision_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        sources = args[0] if args else kwargs.get("image_sources", [])
        if not isinstance(sources, (list, tuple)):
            sources = [sources]
        raw = "\n".join(str(item or "") for item in sources)
        raw += f"\n{kwargs.get('umo', '')}"
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()

    def _allowed_by_policy(self, user_id: str) -> bool:
        if not self._globally_enabled():
            return False
        matcher = getattr(self.plugin, "matcher", None)
        if matcher is None:
            return True
        identity = EventIdentity(
            platform="",
            user_id=user_id,
            chat_type="private",
        )
        decision = matcher.match_private_aliases(
            identity,
            self._private_aliases(user_id),
        )
        if decision.is_blocked:
            return False
        if not matcher.check_plugin_access(
            decision,
            "astrbot_plugin_private_companion",
        ).allowed:
            return False
        return bool(
            (decision.user_rule or {}).get(
                "private_companion_proactive",
                True,
            )
        )

    def _globally_enabled(self) -> bool:
        store = getattr(self.plugin, "store", None)
        if store is None:
            return True
        config = store.config.get("private_companion_proactive", {})
        if not isinstance(config, dict):
            return True
        return bool(config.get("enabled", True))

    def _image_rule(self) -> dict[str, Any]:
        store = getattr(self.plugin, "store", None)
        if store is None:
            return {}
        config = store.config.get("private_companion_proactive", {})
        return config if isinstance(config, dict) else {}

    def _image_fast_enabled(self) -> bool:
        return bool(self._image_rule().get("image_fast_mode", True))

    def _image_debounce_seconds(self) -> float:
        try:
            return max(
                0.0,
                min(
                    10.0,
                    float(
                        self._image_rule().get(
                            "image_debounce_seconds",
                            1.5,
                        )
                    ),
                ),
            )
        except (TypeError, ValueError):
            return 1.5

    def _image_timeout_seconds(self) -> float:
        try:
            return max(
                0.01,
                min(
                    30.0,
                    float(
                        self._image_rule().get(
                            "image_vision_timeout_seconds",
                            15.0,
                        )
                    ),
                ),
            )
        except (TypeError, ValueError):
            return 15.0

    def _success_message(self) -> str:
        image_suffix = f"；{self.image_message}"
        if not self._globally_enabled():
            return (
                "Private Companion 私聊主动对话已由全局总闸关闭；"
                f"逐用户开关不能覆盖全局关闭{image_suffix}"
            )
        proactive_message = (
            "已附加私聊主动对话权限控制；必须同时满足全局总闸、"
            "逐用户开关、插件权限和原插件的用户状态、免打扰、频率、"
            "每日上限。"
            if self.applied
            else (
                "私聊主动对话权限入口暂不兼容，已回退为原插件控制。"
            )
        )
        return f"{proactive_message}{image_suffix}"

    @staticmethod
    def _private_aliases(user_id: str) -> list[str]:
        raw = str(user_id or "").strip()
        if not raw:
            return []
        aliases = [raw]
        parsed = parse_session_identity(raw)
        if parsed is not None and parsed.is_private:
            aliases.append(parsed.user_id)
        parts = raw.split(":")
        if len(parts) >= 3:
            aliases.append(parts[-1])
        for candidate in list(aliases):
            if "!" not in candidate:
                continue
            pieces = [
                piece.strip()
                for piece in candidate.split("!")
                if piece.strip()
            ]
            aliases.extend(pieces)
            if pieces:
                aliases.append(pieces[-1])
        return list(dict.fromkeys(alias for alias in aliases if alias))

    @staticmethod
    def _compatible_method(method: Any) -> bool:
        if not callable(method) or inspect.iscoroutinefunction(method):
            return False
        try:
            inspect.signature(method).bind("user-id", {})
        except (TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _compatible_debounce_method(method: Any) -> bool:
        if not callable(method) or inspect.iscoroutinefunction(method):
            return False
        try:
            inspect.signature(method).bind("image")
        except (TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _compatible_vision_method(method: Any) -> bool:
        if not callable(method) or not inspect.iscoroutinefunction(method):
            return False
        try:
            inspect.signature(method).bind(["image"], umo="session")
        except (TypeError, ValueError):
            return False
        return True
