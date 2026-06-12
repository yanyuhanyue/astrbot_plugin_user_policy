"""第三方插件运行时发现与处理器包装工具。"""

from __future__ import annotations

from typing import Any, Callable, Iterable


USER_POLICY_WRAPPED_MARKERS = (
    "_user_policy_meme_wrapped",
    "_user_policy_life_wrapped",
    "_user_policy_gitee_aiimg_wrapped",
    "_user_policy_livingmemory_wrapped",
    "_user_policy_livingmemory_engine_wrapped",
    "_user_policy_private_companion_wrapped",
    "_user_policy_proactive_wrapped",
)
USER_POLICY_ORIGINAL_ATTRS = (
    "_user_policy_meme_original",
    "_user_policy_life_original",
    "_user_policy_gitee_aiimg_original",
    "_user_policy_livingmemory_original",
    "_user_policy_livingmemory_engine_original",
    "_user_policy_private_companion_original",
    "_user_policy_proactive_original",
    "__wrapped__",
)


def value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def plugin_instance(item: Any) -> Any | None:
    for name in ("star_cls", "instance", "star", "plugin", "obj"):
        candidate = value(item, name)
        if candidate is not None and not isinstance(candidate, type):
            return candidate
    metadata = value(item, "metadata")
    if metadata is not None and metadata is not item:
        return plugin_instance(metadata)
    return item if item is not None and not isinstance(item, type) else None


def find_plugin(
    context: Any,
    fallback_plugins: dict[str, Any],
    names: set[str],
) -> tuple[Any | None, Any | None]:
    values: list[Any] = []
    getter = getattr(context, "get_all_stars", None)
    if callable(getter):
        try:
            loaded = getter() or []
            values.extend(
                loaded.values() if isinstance(loaded, dict) else loaded
            )
        except Exception:
            pass
    values.extend(fallback_plugins.values())
    seen: set[int] = set()
    for item in values:
        if id(item) in seen:
            continue
        seen.add(id(item))
        name = str(value(item, "name", "") or "").strip()
        if not name:
            metadata = value(item, "metadata")
            name = str(value(metadata, "name", "") or "").strip()
        instance = plugin_instance(item)
        module_name = str(
            getattr(getattr(instance, "__class__", None), "__module__", "")
            or ""
        )
        if name in names or any(part in module_name for part in names):
            return instance, item
    return None, None


def wrap_registry_handlers(
    target: Any,
    method_names: Iterable[str],
    wrapper_factory: Callable[[Callable[..., Any]], Callable[..., Any]],
    marker: str,
    originals: dict[str, Callable[..., Any]],
) -> None:
    module_path = str(getattr(target.__class__, "__module__", "") or "")
    if not module_path:
        return
    try:
        from astrbot.core.star.star_handler import star_handlers_registry
    except Exception:
        return
    getter = getattr(
        star_handlers_registry,
        "get_handlers_by_module_name",
        None,
    )
    handlers = (
        list(getter(module_path) or [])
        if callable(getter)
        else [
            handler
            for handler in star_handlers_registry
            if getattr(handler, "handler_module_path", "") == module_path
        ]
    )
    names = set(method_names)
    for handler in handlers:
        name = str(getattr(handler, "handler_name", "") or "")
        original = getattr(handler, "handler", None)
        if name not in names or not callable(original):
            continue
        key = str(getattr(handler, "handler_full_name", "") or id(handler))
        if key in originals or getattr(original, marker, False):
            continue
        wrapped = wrapper_factory(original)
        setattr(wrapped, marker, True)
        original_attr = marker.removesuffix("_wrapped") + "_original"
        try:
            setattr(wrapped, original_attr, original)
        except Exception:
            pass
        originals[key] = original
        handler.handler = wrapped


def unwrap_user_policy_callable(method: Any) -> Callable[..., Any] | None:
    if not callable(method):
        return None
    if not any(bool(getattr(method, marker, False)) for marker in USER_POLICY_WRAPPED_MARKERS):
        return None
    for attr in USER_POLICY_ORIGINAL_ATTRS:
        original = getattr(method, attr, None)
        if callable(original) and original is not method:
            return original
    return None


def restore_user_policy_wrappers(
    context: Any = None,
    fallback_plugins: dict[str, Any] | None = None,
) -> int:
    """尽力恢复本插件历史版本留在第三方插件上的运行时 wrapper。"""

    restored = 0
    values: list[Any] = []
    getter = getattr(context, "get_all_stars", None)
    if callable(getter):
        try:
            loaded = getter() or []
            values.extend(
                loaded.values() if isinstance(loaded, dict) else loaded
            )
        except Exception:
            pass
    values.extend((fallback_plugins or {}).values())

    seen: set[int] = set()
    for item in values:
        instance = plugin_instance(item)
        if instance is None or id(instance) in seen:
            continue
        seen.add(id(instance))
        restored += restore_wrapped_attributes(instance)

    restored += restore_wrapped_registry_handlers()
    return restored


def restore_wrapped_attributes(target: Any) -> int:
    restored = 0
    for name in dir(target):
        try:
            current = getattr(target, name)
        except Exception:
            continue
        original = unwrap_user_policy_callable(current)
        if original is None:
            continue
        try:
            setattr(target, name, original)
        except Exception:
            continue
        restored += 1
    return restored


def restore_wrapped_registry_handlers() -> int:
    try:
        from astrbot.core.star.star_handler import star_handlers_registry
    except Exception:
        return 0
    try:
        handlers = list(star_handlers_registry)
    except TypeError:
        handlers = list(getattr(star_handlers_registry, "handlers", []) or [])
    restored = 0
    for handler in handlers:
        current = getattr(handler, "handler", None)
        original = unwrap_user_policy_callable(current)
        if original is None:
            continue
        try:
            handler.handler = original
        except Exception:
            continue
        restored += 1
    return restored
