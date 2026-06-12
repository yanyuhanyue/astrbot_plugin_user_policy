"""AstrBot 插件元数据的兼容枚举与展示结构。"""

from __future__ import annotations

from typing import Any, Callable, Iterable


def value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def iter_metadata(source: Any) -> Iterable[tuple[str, Any]]:
    """兼容列表、字典以及少数包装结构。"""

    if source is None:
        return ()
    if isinstance(source, dict):
        return tuple((str(key), item) for key, item in source.items())
    try:
        items = tuple(source)
    except TypeError:
        return ()

    result = []
    for item in items:
        if (
            isinstance(item, tuple)
            and len(item) == 2
            and not value(item, "name", "")
        ):
            result.append((str(item[0]), item[1]))
        else:
            result.append(("", item))
    return tuple(result)


def collect_plugin_catalog(
    sources: Iterable[Any],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for source in sources:
        for source_key, metadata in iter_metadata(source):
            name = str(value(metadata, "name", "") or "").strip()
            if not name:
                continue
            module_path = str(
                value(metadata, "module_path", source_key) or source_key
            ).strip()
            item = result.setdefault(
                name,
                {
                    "display_name": name,
                    "version": "",
                    "activated": False,
                    "module_path": module_path,
                    "reserved": False,
                },
            )
            item["display_name"] = str(
                value(metadata, "display_name", name) or name
            )
            item["version"] = str(value(metadata, "version", "") or "")
            item["activated"] = bool(value(metadata, "activated", True))
            item["reserved"] = bool(value(metadata, "reserved", False))
            if module_path:
                item["module_path"] = module_path
    return result


def is_protected_plugin(
    plugin_name: str,
    module_path: str = "",
    reserved: bool = False,
    *,
    own_plugin_name: str = "astrbot_plugin_user_policy",
) -> bool:
    return (
        reserved
        or plugin_name == own_plugin_name
        or plugin_name.startswith("astrbot.builtin")
        or module_path.startswith("astrbot.builtin_stars")
    )


def filter_plugin_scope(
    current: Any,
    catalog: dict[str, dict[str, Any]],
    check_plugin_access: Callable[[str], bool],
    *,
    protected: Callable[[str, str, bool], bool] | None = None,
) -> list[str] | None:
    """返回事件可用插件列表；目录缺失时返回 None 表示保持原样。

    AstrBot 热重载或部分合并消息插件制造二次事件时，运行时插件目录可能短暂
    为空。此时写入空列表会清掉后续基础回复链路，因此这里选择不改事件范围。
    """

    if not isinstance(current, list) or current == ["*"]:
        return None

    if not catalog:
        return list(dict.fromkeys(str(name) for name in current))

    active_names = [
        name
        for name, item in catalog.items()
        if item.get("activated")
    ]
    candidates = [str(name) for name in current]
    is_protected = protected or is_protected_plugin
    allowed = []
    for plugin_name in candidates:
        item = catalog.get(plugin_name, {})
        if is_protected(
            plugin_name,
            str(item.get("module_path", "")),
            bool(item.get("reserved", False)),
        ):
            allowed.append(plugin_name)
            continue
        if check_plugin_access(plugin_name):
            allowed.append(plugin_name)
    return list(dict.fromkeys(allowed))
