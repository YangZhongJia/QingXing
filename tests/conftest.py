"""pytest 公共装置：离屏 Qt + 可控时钟 + 调度上下文。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")

from src.clock import FakeClock  # noqa: E402
from src.models import AppConfig, default_config_now  # noqa: E402
from src.scheduler import Scheduler  # noqa: E402

START = datetime(2026, 10, 26, 1, 0, 0, tzinfo=timezone.utc)


@dataclass
class Ctx:
    clock: FakeClock
    config: AppConfig
    scheduler: Scheduler
    saves: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    order: list = field(default_factory=list)

    def advance_minutes(self, minutes: float) -> None:
        self.clock.advance_minutes(minutes)

    def tick(self) -> list[str]:
        return self.scheduler.tick()

    @property
    def head(self):
        return self.scheduler.head_id()


@pytest.fixture
def make_ctx(tmp_path):
    storage_path = tmp_path / "config.json"

    def _make(stand: int = 30, drink: int = 40, persist_to_disk: bool = False) -> Ctx:
        from src import storage

        clock = FakeClock(START)
        config = default_config_now(clock.now_utc())
        for task_id, interval in (("stand_up", stand), ("drink_water", drink)):
            task = config.task(task_id)
            task.interval_minutes = interval
            task.next_due_at = clock.now_utc() + timedelta(minutes=interval)
        ctx = Ctx(clock=clock, config=config, scheduler=None)  # type: ignore[arg-type]

        def persist(cfg: AppConfig) -> None:
            ctx.order.append("persist")
            ctx.saves.append(cfg.to_dict())
            if persist_to_disk:
                storage.save(storage_path, cfg)

        def on_change() -> None:
            ctx.order.append("ui")
            ctx.changes.append(len(ctx.saves))

        ctx.scheduler = Scheduler(config, clock, persist=persist, on_change=on_change)
        ctx.storage_path = storage_path  # type: ignore[attr-defined]
        return ctx

    return _make
