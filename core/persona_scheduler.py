"""持久化的人格定时与随机切换调度器。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable


STATE_SCHEMA_VERSION = 1


class PersonaScheduler:
    """按本地时区执行人格计划，并保存下次执行状态。"""

    def __init__(
        self,
        data_dir: Path,
        plans_provider: Callable[[], dict[str, dict[str, Any]]],
        apply_persona: Callable[
            [str, dict[str, Any], str],
            Awaitable[None],
        ],
    ):
        self.path = data_dir.resolve() / "schedule_state.json"
        self.plans_provider = plans_provider
        self.apply_persona = apply_persona
        self.random = random.SystemRandom()
        self.state: dict[str, Any] = {
            "schema_version": STATE_SCHEMA_VERSION,
            "plans": {},
        }
        self.task: asyncio.Task | None = None
        self.wake_event = asyncio.Event()
        self.lock = asyncio.Lock()
        self.load()

    def load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        plans = raw.get("plans") if isinstance(raw, dict) else None
        if isinstance(plans, dict):
            self.state = {
                "schema_version": STATE_SCHEMA_VERSION,
                "plans": plans,
            }

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(
                self._run(),
                name="astrbot-user-policy-persona-scheduler",
            )

    async def stop(self) -> None:
        task = self.task
        self.task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def notify_changed(self) -> None:
        self.wake_event.set()

    def status_for_ui(self) -> dict[str, dict[str, Any]]:
        result = {}
        for schedule_id, item in self.state.get("plans", {}).items():
            if not isinstance(item, dict):
                continue
            result[schedule_id] = {
                "next_run_at": item.get("next_run_at"),
                "last_run_at": item.get("last_run_at"),
                "last_persona_id": item.get("last_persona_id", ""),
                "last_status": item.get("last_status", ""),
                "last_message": item.get("last_message", ""),
            }
        return result

    async def process_once(self, now: datetime | None = None) -> None:
        """处理一次到期任务，供后台循环与测试共同使用。"""

        local_now = now or datetime.now().astimezone()
        now_ts = local_now.timestamp()
        plans = self.plans_provider()
        if not isinstance(plans, dict):
            plans = {}

        async with self.lock:
            states = self.state.setdefault("plans", {})
            changed = False
            for schedule_id in list(states):
                if schedule_id not in plans:
                    states.pop(schedule_id, None)
                    changed = True

            for schedule_id, plan in plans.items():
                if not isinstance(plan, dict):
                    continue
                item = states.setdefault(schedule_id, {})
                signature = self._signature(plan)
                if item.get("signature") != signature:
                    item.clear()
                    item["signature"] = signature
                    changed = True

                if not plan.get("enabled", True):
                    if item.get("next_run_at") is not None:
                        item["next_run_at"] = None
                        changed = True
                    item["last_status"] = "disabled"
                    continue

                next_run_at = item.get("next_run_at")
                if not isinstance(next_run_at, (int, float)):
                    item["next_run_at"] = self._next_run(
                        plan,
                        local_now,
                    )
                    item["last_status"] = item.get(
                        "last_status",
                        "waiting",
                    )
                    changed = True
                    continue
                if next_run_at > now_ts:
                    continue

                persona_id = self._select_persona(plan, item, local_now)
                item["last_run_at"] = now_ts
                item["last_persona_id"] = persona_id
                try:
                    await self.apply_persona(
                        schedule_id,
                        plan,
                        persona_id,
                    )
                except Exception as exc:
                    item["last_status"] = "error"
                    item["last_message"] = str(exc)
                else:
                    item["last_status"] = "success"
                    item["last_message"] = "人格切换成功"
                item["next_run_at"] = self._next_run(
                    plan,
                    local_now,
                    after_execution=True,
                )
                changed = True

            if changed:
                self._atomic_write()

    async def _run(self) -> None:
        while True:
            self.wake_event.clear()
            try:
                await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # 单次调度异常不能终止后续计划。
                pass
            try:
                await asyncio.wait_for(
                    self.wake_event.wait(),
                    timeout=15,
                )
            except asyncio.TimeoutError:
                pass

    @staticmethod
    def _signature(plan: dict[str, Any]) -> str:
        fields = {
            key: plan.get(key)
            for key in (
                "enabled",
                "target_type",
                "user_id",
                "group_id",
                "mode",
                "daily_time",
                "weekly_time",
                "interval_minutes",
                "persona_id",
                "persona_ids",
                "weekly_rules",
            )
        }
        encoded = json.dumps(
            fields,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _select_persona(
        self,
        plan: dict[str, Any],
        item: dict[str, Any],
        now: datetime | None = None,
    ) -> str:
        if plan.get("mode") == "daily":
            return str(plan.get("persona_id", "") or "")
        if plan.get("mode") == "weekly":
            weekday = (now or datetime.now().astimezone()).weekday()
            for rule in plan.get("weekly_rules", []):
                if int(rule.get("weekday", -1)) == weekday:
                    return str(rule.get("persona_id", "") or "")
            return ""

        candidates = [
            str(persona_id)
            for persona_id in plan.get("persona_ids", [])
            if str(persona_id)
        ]
        last_persona = str(item.get("last_persona_id", "") or "")
        alternatives = [
            persona_id
            for persona_id in candidates
            if persona_id != last_persona
        ]
        return self.random.choice(alternatives or candidates)

    @staticmethod
    def _next_run(
        plan: dict[str, Any],
        now: datetime,
        *,
        after_execution: bool = False,
    ) -> float:
        if plan.get("mode") == "random_interval":
            minutes = int(plan.get("interval_minutes", 60))
            return (now + timedelta(minutes=minutes)).timestamp()
        if plan.get("mode") == "weekly":
            return PersonaScheduler._next_weekly_run(
                plan,
                now,
                after_execution=after_execution,
            )

        hour, minute = (
            int(part)
            for part in str(plan.get("daily_time", "00:00")).split(":")
        )
        candidate = now.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if candidate <= now or after_execution:
            candidate += timedelta(days=1)
        return candidate.timestamp()

    @staticmethod
    def _next_weekly_run(
        plan: dict[str, Any],
        now: datetime,
        *,
        after_execution: bool = False,
    ) -> float:
        hour, minute = (
            int(part)
            for part in str(plan.get("weekly_time", "00:00")).split(":")
        )
        weekdays = sorted(
            {
                int(rule.get("weekday", -1))
                for rule in plan.get("weekly_rules", [])
                if rule.get("enabled", True)
                and str(rule.get("persona_id", "") or "")
            }
        )
        if not weekdays:
            return (now + timedelta(days=7)).timestamp()

        best = None
        for offset in range(0, 8):
            day = now + timedelta(days=offset)
            if day.weekday() not in weekdays:
                continue
            candidate = day.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if candidate <= now or (after_execution and offset == 0):
                continue
            if best is None or candidate < best:
                best = candidate
        if best is None:
            best = now + timedelta(days=7)
        return best.timestamp()

    def _atomic_write(self) -> None:
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                self.state,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
