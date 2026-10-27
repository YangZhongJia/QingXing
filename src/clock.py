"""可注入时钟：生产用系统时间，测试用 FakeClock 瞬移。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now_utc(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()


class FakeClock:
    """测试专用：时间只在显式调用时前进。"""

    def __init__(self, start: datetime | None = None) -> None:
        base = start or datetime(2026, 10, 26, 6, 0, 0, tzinfo=timezone.utc)
        self._now = base.astimezone(timezone.utc)
        self._monotonic = 0.0

    def now_utc(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> datetime:
        delta = timedelta(seconds=seconds)
        self._now = self._now + delta
        self._monotonic += seconds
        return self._now

    def advance_minutes(self, minutes: float) -> datetime:
        return self.advance(minutes * 60)

    def set_now(self, moment: datetime) -> datetime:
        """直接设置墙钟（用于模拟用户手动改系统时间）。"""
        moment = moment.astimezone(timezone.utc)
        self._monotonic += (moment - self._now).total_seconds()
        self._now = moment
        return self._now
