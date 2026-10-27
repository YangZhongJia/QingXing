"""调度核心：全局唯一的业务状态机（不依赖 Qt，可注入时钟）。

设计约束（PRD 3.2 / 3.4 / 3.5 / 5.4）：
- 计时正确性由持久化到期时间计算，不靠累加 tick 次数。
- 同一任务最多一条待确认；队列按 (到期时刻, 任务 ID) 排序。
- 确认后以「确认时刻 + 新间隔」重排下一次。
- 暂停冻结剩余时间；暂停期间确认的任务在恢复时给完整间隔。
- 任何状态变更先落盘（persist）再通知 UI（on_change）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

from .models import (
    AppConfig,
    PendingEntry,
    Reminder,
    Task,
    TaskStatus,
    ValidationError,
    validate_interval,
)

logger = logging.getLogger(__name__)

_BACKWARD_JUMP_TOLERANCE_SECONDS = 300
_FORWARD_JUMP_LOG_SECONDS = 3600

#: 用户新增提醒的 ID 前缀；内置提醒不允许删除
CUSTOM_TASK_PREFIX = "custom_"


class SchedulerError(RuntimeError):
    """调度状态机被非法调用（属于程序错误，不是用户输入错误）。"""


class Scheduler:
    def __init__(
        self,
        config: AppConfig,
        clock,
        persist: Callable[[AppConfig], None] | None = None,
        on_change: Callable[[], None] | None = None,
        log: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._persist = persist
        self._on_change = on_change
        self._log = log or logger
        self._last_now = clock.now_utc()
        self._config.repair(self._last_now)

    # ---------------- 只读访问 ----------------

    @property
    def config(self) -> AppConfig:
        return self._config

    @property
    def tasks(self) -> list[Task]:
        return self._config.tasks

    def task(self, task_id: str) -> Task:
        return self._config.task(task_id)

    def head_id(self) -> str | None:
        return self._config.head_id()

    def head_reminder(self) -> Reminder | None:
        task_id = self.head_id()
        return self.reminder_for(task_id, is_test=False) if task_id else None

    def reminder_for(self, task_id: str, is_test: bool = False) -> Reminder:
        task = self.task(task_id)
        return Reminder(
            task_id=task.id,
            title=task.title,
            message=task.message,
            icon=task.icon,
            is_test=is_test,
        )

    def is_paused(self) -> bool:
        return self._config.global_paused

    def next_due_for(self, task_id: str) -> datetime | None:
        task = self.task(task_id)
        if task.status is TaskStatus.ACTIVE:
            return task.next_due_at
        if task.status is TaskStatus.PAUSED and task.remaining_seconds is not None:
            return self._clock.now_utc() + timedelta(seconds=task.remaining_seconds)
        return None

    def remaining_seconds_for(self, task_id: str) -> int | None:
        return self.task(task_id).due_in_seconds(self._clock.now_utc())

    # ---------------- 核心动作 ----------------

    def tick(self) -> list[str]:
        """推进一次检查；返回本次新到期的任务 ID（同一任务最多一次）。"""
        now = self._clock.now_utc()
        self._guard_time_jump(now)
        new_pending: list[str] = []
        if not self._config.global_paused:
            for task in self._config.tasks:
                if not task.enabled or task.status is not TaskStatus.ACTIVE:
                    continue
                due_at = task.next_due_at
                if due_at is None or now < due_at:
                    continue
                self._config.pending_queue.append(PendingEntry(task.id, due_at))
                task.status = TaskStatus.PENDING
                task.next_due_at = None
                new_pending.append(task.id)
                self._log.info(
                    "到期入队 task=%s interval=%s due=%s",
                    task.id,
                    task.interval_minutes,
                    due_at.isoformat(),
                )
        if new_pending:
            self._config.pending_queue.sort(key=PendingEntry.sort_key)
            self._commit()
        return new_pending

    def acknowledge(self, task_id: str) -> str | None:
        """确认当前提醒：先落盘再返回下一条队首 ID。"""
        head = self.head_id()
        if head is None:
            raise SchedulerError("当前没有待确认提醒")
        if head != task_id:
            raise SchedulerError(f"只能确认队首提醒：期望 {head}，实际 {task_id}")
        task = self.task(task_id)
        if task.status is not TaskStatus.PENDING:
            raise SchedulerError(f"任务 {task_id} 不在待确认状态：{task.status.value}")

        now = self._clock.now_utc()
        self._config.pending_queue = [e for e in self._config.pending_queue if e.task_id != task_id]
        task.last_ack_at = now
        if not task.enabled:
            task.status = TaskStatus.DISABLED
            task.next_due_at = None
            task.remaining_seconds = None
        elif self._config.global_paused:
            # PRD 3.2：暂停期间确认，恢复时从完整间隔开始
            task.status = TaskStatus.PAUSED
            task.remaining_seconds = task.interval_seconds
            task.next_due_at = None
        else:
            task.status = TaskStatus.ACTIVE
            task.remaining_seconds = None
            task.next_due_at = now + timedelta(minutes=task.interval_minutes)
        self._commit()
        self._log.info("已确认 task=%s at=%s 下次=%s", task_id, now.isoformat(), task.next_due_at)
        return self.head_id()

    def pause(self) -> None:
        if self._config.global_paused:
            return
        now = self._clock.now_utc()
        self._config.global_paused = True
        for task in self._config.tasks:
            if task.status is TaskStatus.ACTIVE and task.next_due_at is not None:
                remaining = max(0, int((task.next_due_at - now).total_seconds()))
                task.status = TaskStatus.PAUSED
                task.remaining_seconds = remaining
                task.next_due_at = None
        self._commit()
        self._log.info("全局暂停")

    def resume(self) -> None:
        if not self._config.global_paused:
            return
        now = self._clock.now_utc()
        self._config.global_paused = False
        for task in self._config.tasks:
            if task.status is TaskStatus.PAUSED:
                remaining = task.remaining_seconds or task.interval_seconds
                task.status = TaskStatus.ACTIVE
                task.next_due_at = now + timedelta(seconds=remaining)
                task.remaining_seconds = None
        self._commit()
        self._log.info("全局恢复")

    def set_interval(self, task_id: str, minutes: object) -> int:
        """修改间隔：ACTIVE 从保存时刻重算；PENDING 弹窗保留，确认后生效。"""
        interval = validate_interval(minutes)
        task = self.task(task_id)
        now = self._clock.now_utc()
        task.interval_minutes = interval
        if task.status is TaskStatus.ACTIVE:
            task.next_due_at = now + timedelta(minutes=interval)
        elif task.status is TaskStatus.PAUSED:
            task.remaining_seconds = interval * 60
        self._commit()
        self._log.info("修改间隔 task=%s -> %s 分钟", task_id, interval)
        return interval

    def set_enabled(self, task_id: str, enabled: bool) -> None:
        """开关任务；关闭时撤销该任务的待确认提醒并记录 cancelled 事件。"""
        task = self.task(task_id)
        now = self._clock.now_utc()
        task.enabled = bool(enabled)
        if task.enabled:
            if self._config.global_paused:
                task.status = TaskStatus.PAUSED
                task.remaining_seconds = task.interval_seconds
                task.next_due_at = None
            else:
                task.status = TaskStatus.ACTIVE
                task.remaining_seconds = None
                task.next_due_at = now + timedelta(minutes=task.interval_minutes)
        else:
            if task.status is TaskStatus.PENDING:
                self._config.pending_queue = [e for e in self._config.pending_queue if e.task_id != task_id]
                self._log.info("cancelled task=%s at=%s reason=task_disabled", task_id, now.isoformat())
            task.status = TaskStatus.DISABLED
            task.next_due_at = None
            task.remaining_seconds = None
        self._commit()
        self._log.info("任务开关 task=%s enabled=%s", task_id, task.enabled)

    def add_task(self, title: str, message: str = "", interval_minutes: object = 30) -> Task:
        """新增一条自定义提醒（用户可加任意条）。

        内置两条只读，新增的 id 统一为 ``custom_N``，删除只允许删自定义提醒。
        间隔走同一套 validate_interval 校验，非法直接抛 ValidationError（不落盘）。
        """
        minutes = validate_interval(interval_minutes)
        title = (title or "").strip() or "自定义提醒"
        message = (message or "").strip() or "该休息一下了，起来活动活动吧。"
        existing = {t.id for t in self._config.tasks}
        index = 1
        while f"{CUSTOM_TASK_PREFIX}{index}" in existing:
            index += 1
        task_id = f"{CUSTOM_TASK_PREFIX}{index}"
        now = self._clock.now_utc()
        # 全局暂停时新增的提醒同样是「暂停」状态：恢复后从完整间隔开始（PRD 3.2）
        paused = self._config.global_paused
        task = Task(
            id=task_id,
            title=title[:40],
            message=message[:160],
            icon="custom",
            interval_minutes=minutes,
            enabled=True,
            status=TaskStatus.PAUSED if paused else TaskStatus.ACTIVE,
            next_due_at=None if paused else now + timedelta(minutes=minutes),
            remaining_seconds=minutes * 60 if paused else None,
        )
        self._config.tasks.append(task)
        self._commit()
        self._log.info("新增提醒 task=%s interval=%s title=%s", task_id, minutes, title)
        return task

    def remove_task(self, task_id: str) -> None:
        """删除自定义提醒；内置的两条不允许删除。"""
        task = self._config.get_task(task_id)
        if task is None:
            raise SchedulerError(f"任务不存在：{task_id}")
        if not task_id.startswith(CUSTOM_TASK_PREFIX):
            raise SchedulerError("内置提醒不可删除，可直接关闭")
        self._config.pending_queue = [
            entry for entry in self._config.pending_queue if entry.task_id != task_id
        ]
        self._config.tasks = [t for t in self._config.tasks if t.id != task_id]
        self._commit()
        self._log.info("删除提醒 task=%s", task_id)

    def make_test_reminder(self, task_id: str) -> Reminder:
        """手动测试：不写盘、不改状态、不影响正式倒计时（TC-028）。"""
        return self.reminder_for(task_id, is_test=True)

    def clear_pending(self, task_id: str, reason: str = "cancelled") -> None:
        """应用明确退出/关闭任务时撤销待确认提醒。"""
        if self._config.find_entry(task_id) is None:
            return
        self._config.pending_queue = [e for e in self._config.pending_queue if e.task_id != task_id]
        task = self.task(task_id)
        task.status = TaskStatus.DISABLED if not task.enabled else TaskStatus.ACTIVE
        if task.status is TaskStatus.ACTIVE:
            task.next_due_at = self._clock.now_utc() + timedelta(minutes=task.interval_minutes)
        self._commit()
        self._log.info("cancelled task=%s reason=%s", task_id, reason)

    def commit(self) -> None:
        """设置页改完 settings 等非调度字段后主动落盘并通知 UI。"""
        self._commit()

    # ---------------- 内部 ----------------

    def _guard_time_jump(self, now: datetime) -> None:
        delta = (now - self._last_now).total_seconds()
        if delta < -_BACKWARD_JUMP_TOLERANCE_SECONDS:
            self._log.warning("检测到系统时间回拨 %.0f 秒，进行重算保护", delta)
            for task in self._config.tasks:
                if task.status is TaskStatus.ACTIVE and task.next_due_at is not None:
                    limit = now + timedelta(minutes=task.interval_minutes)
                    if task.next_due_at > limit:
                        task.next_due_at = limit
                        self._log.info("时间回拨保护 task=%s 到期时间夹回 %s", task.id, limit.isoformat())
                elif task.status is TaskStatus.PAUSED and task.remaining_seconds is not None:
                    task.remaining_seconds = min(task.remaining_seconds, task.interval_seconds)
            self._commit()
        elif delta > _FORWARD_JUMP_LOG_SECONDS:
            self._log.warning("检测到系统时间前跳 %.0f 秒（可能为休眠恢复），按到期时间补发一次", delta)
        self._last_now = now

    def _commit(self) -> None:
        if self._persist is not None:
            self._persist(self._config)
        if self._on_change is not None:
            self._on_change()


def validate_and_build_interval(raw: object) -> int:
    """给 UI 复用的校验入口。"""
    return validate_interval(raw)
