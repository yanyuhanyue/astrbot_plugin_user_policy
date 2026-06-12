"""Life Scheduler 人格日程库管理。"""

from __future__ import annotations

import asyncio
import copy
import datetime
from types import SimpleNamespace
from typing import Any

from .policy_store import LIFE_POOL_KEYS


LIFE_POOL_CONTAINER_KEYS = (
    "pool",
    "creative_pool",
    "creative_pools",
    "life_pool",
)


class _OverlayConfig:
    """在不修改原插件配置对象的前提下，覆盖运行期读取值。"""

    def __init__(self, base: Any, overlay: dict[str, Any]):
        self._base = base
        self._overlay = overlay

    def __getitem__(self, key: str) -> Any:
        if key in self._overlay:
            return self._overlay[key]
        return self._base[key]

    def __contains__(self, key: object) -> bool:
        return key in self._overlay or key in self._base

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._overlay:
            return self._overlay[key]
        getter = getattr(self._base, "get", None)
        if callable(getter):
            return getter(key, default)
        try:
            return self._base[key]
        except Exception:
            return default

    def __getattr__(self, name: str) -> Any:
        if name in self._overlay:
            return self._overlay[name]
        return getattr(self._base, name)


class _MemoryScheduleDataManager:
    def __init__(self, records: dict[str, Any]):
        self.records = {
            date: SimpleNamespace(**record)
            for date, record in records.items()
            if isinstance(record, dict)
        }

    @staticmethod
    def _key(value: Any) -> str:
        if isinstance(value, datetime.datetime):
            return value.date().isoformat()
        if isinstance(value, datetime.date):
            return value.isoformat()
        return str(value or "")[:10]

    def get(self, value: Any) -> Any | None:
        return self.records.get(self._key(value))

    def set(self, data: Any) -> None:
        self.records[str(getattr(data, "date", "") or "")] = data

    def all(self) -> dict[str, Any]:
        return dict(self.records)


class LifeScheduleLibraryManager:
    """读取策略中的命名日程库，并安全借用原插件生成器。"""

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self.lock = asyncio.Lock()

    def libraries(self) -> dict[str, Any]:
        store = getattr(self.plugin, "store", None)
        value = (
            store.config.get("life_schedule_libraries", {})
            if store is not None
            else {}
        )
        return value if isinstance(value, dict) else {}

    def persona_map(self) -> dict[str, str]:
        store = getattr(self.plugin, "store", None)
        value = (
            store.config.get("life_schedule_persona_map", {})
            if store is not None
            else {}
        )
        return value if isinstance(value, dict) else {}

    def library_id_for_persona(self, persona_id: str) -> str:
        target = str(self.persona_map().get(str(persona_id), "") or "")
        return target if target in self.libraries() else ""

    def record_for_persona(
        self,
        persona_id: str,
        date: str | datetime.date | datetime.datetime,
    ) -> dict[str, Any] | None:
        library_id = self.library_id_for_persona(persona_id)
        if not library_id:
            return None
        date_key = self._date_key(date)
        record = (
            self.libraries()
            .get(library_id, {})
            .get("records", {})
            .get(date_key)
        )
        return dict(record) if isinstance(record, dict) else None

    def injection_for_persona(
        self,
        persona_id: str,
        date: str | datetime.date | datetime.datetime | None = None,
    ) -> str:
        record = self.record_for_persona(
            persona_id,
            date or datetime.datetime.now(),
        )
        if not record or record.get("status") != "ok":
            return ""
        return (
            "\n\n【当前人格生活日程】\n"
            f"日期：{record.get('date', '')}\n"
            f"穿搭风格：{record.get('outfit_style') or '未设置'}\n"
            f"今日穿搭：{record.get('outfit') or '未设置'}\n"
            f"日程安排：\n{record.get('schedule') or '未设置'}"
        )

    async def generate(
        self,
        target: Any,
        persona_id: str,
        date: datetime.datetime,
        umo: str,
        extra: str = "",
    ) -> dict[str, Any] | None:
        library_id = self.library_id_for_persona(persona_id)
        generator = getattr(target, "generator", None)
        generate = getattr(generator, "generate_schedule", None)
        if not library_id or not callable(generate):
            return None
        library = self.libraries().get(library_id, {})
        memory_manager = _MemoryScheduleDataManager(
            library.get("records", {})
        )
        library_pool = library.get("pool")
        persona_prompt = await self.plugin.personas.get_persona_prompt(
            persona_id
        )

        async with self.lock:
            async def selected_persona() -> str:
                return persona_prompt

            isolated_generator = copy.copy(generator)
            isolated_generator.data_mgr = memory_manager
            isolated_generator._get_persona = selected_persona
            isolated_generator._gen_lock = asyncio.Lock()
            isolated_generator._generating = False
            self._apply_pool(isolated_generator, generator, library_pool)
            isolated_generate = getattr(
                isolated_generator,
                "generate_schedule",
            )
            data = await isolated_generate(date, umo, extra=extra or None)

        if data is None:
            return None
        record = {
            "date": str(getattr(data, "date", "") or self._date_key(date)),
            "outfit_style": str(
                getattr(data, "outfit_style", "") or ""
            ),
            "outfit": str(getattr(data, "outfit", "") or ""),
            "schedule": str(getattr(data, "schedule", "") or ""),
            "status": str(getattr(data, "status", "ok") or "ok"),
        }
        await self.save_record(library_id, record)
        return record

    async def save_record(
        self,
        library_id: str,
        record: dict[str, Any],
    ) -> None:
        store = getattr(self.plugin, "store", None)
        if store is None:
            return
        expected = store.revision

        def mutate(config: dict[str, Any]) -> None:
            library = config.get("life_schedule_libraries", {}).get(
                library_id
            )
            if not isinstance(library, dict):
                return
            library.setdefault("records", {})[
                self._date_key(record.get("date"))
            ] = dict(record)

        await self.plugin.update_policy(expected, mutate)

    @classmethod
    def _pool_overlay(cls, library_pool: Any) -> dict[str, Any]:
        if not isinstance(library_pool, dict):
            return {}
        pool = {
            key: list(library_pool.get(key) or [])
            for key in LIFE_POOL_KEYS
            if isinstance(library_pool.get(key), list)
        }
        overlay: dict[str, Any] = {key: list(value) for key, value in pool.items()}
        for key in LIFE_POOL_CONTAINER_KEYS:
            overlay[key] = {name: list(value) for name, value in pool.items()}
        return overlay

    @classmethod
    def _apply_pool(
        cls,
        isolated_generator: Any,
        generator: Any,
        library_pool: Any,
    ) -> None:
        """让不同版本的 Life Scheduler 都能读到当前日程库创意池。"""

        overlay = cls._pool_overlay(library_pool)
        if not overlay:
            return
        base_config = getattr(generator, "config", None)
        if isinstance(base_config, dict):
            isolated_generator.config = {**base_config, **overlay}
        elif base_config is not None:
            isolated_generator.config = _OverlayConfig(
                base_config,
                {
                    key: copy.deepcopy(value)
                    for key, value in overlay.items()
                },
            )
        else:
            isolated_generator.config = {
                key: copy.deepcopy(value)
                for key, value in overlay.items()
            }
        for key, value in overlay.items():
            try:
                setattr(isolated_generator, key, copy.deepcopy(value))
            except Exception:
                pass

    @staticmethod
    def _date_key(value: Any) -> str:
        if isinstance(value, datetime.datetime):
            return value.date().isoformat()
        if isinstance(value, datetime.date):
            return value.isoformat()
        return str(value or "")[:10]
