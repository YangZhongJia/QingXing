"""提醒队列与浮窗生命周期管理（FR-08 / FR-10 / FR-11）。

职责边界：
- Scheduler 只负责「什么时候哪条提醒到期」并落盘；
- 本模块负责「一次只显示一条」的展示顺序、测试提醒（会话内、不落盘）以及
  「先确认落盘、再关闭浮窗、再显示下一条」的时序；
- 任何业务状态变更仍然只通过 Scheduler，本模块不持有任何定时器。
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from .models import Reminder, TaskStatus
from .scheduler import Scheduler

LOG = logging.getLogger(__name__)


def _target_screen():
    """浮窗显示在鼠标所在屏幕；取不到时用主屏（PRD 5.4 兜底）。"""
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:
        return None
    screen = QApplication.screenAt(QCursor.pos())
    if screen is None:
        screen = QApplication.primaryScreen()
    return screen


class ReminderManager(QObject):
    """把 Scheduler 的待确认队列映射为“一次一张”的浮窗。"""

    ack_received = Signal(str)
    shown = Signal(str)
    closed = Signal(str)

    def __init__(
        self,
        scheduler: Scheduler,
        popup_factory: Optional[Callable[[], "object"]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._scheduler = scheduler
        self._popup_factory = popup_factory
        self._popup = None
        self._current: Optional[Reminder] = None
        self._tests: list[Reminder] = []
        self._acking = False

    # ---------------- 对外 ----------------
    @property
    def current(self) -> Optional[Reminder]:
        return self._current

    @property
    def pending_test_count(self) -> int:
        return len(self._tests)

    def test_reminder(self, task_id: str) -> Optional[Reminder]:
        """测试提醒：立即排队，不持久化、不影响真实倒计时（FR-11）。"""
        try:
            reminder = self._scheduler.make_test_reminder(task_id)
        except Exception:  # 未知任务 ID
            LOG.warning("测试提醒请求了未知任务: %s", task_id)
            return None
        # 同一任务的测试提醒不重复堆叠
        if any(r.task_id == task_id for r in self._tests):
            return None
        if self._current is not None and self._current.is_test and self._current.task_id == task_id:
            return None
        self._tests.append(reminder)
        LOG.info("测试提醒入队: %s", task_id)
        self.sync()
        return reminder

    def sync(self) -> None:
        """按「队列保持顺序」刷新浮窗；已有浮窗时不打断当前提醒。"""
        if self._current is not None:
            return
        if self._tests:
            self._show(self._tests.pop(0))
            return
        head_id = self._scheduler.head_id()
        if head_id is None:
            return
        reminder = self._scheduler.reminder_for(head_id)
        if reminder is not None:
            self._show(reminder)

    def on_state_changed(self) -> None:
        """Scheduler 每次落盘后回调：处理“当前提醒被取消/任务被禁用”等情况。"""
        if self._acking:  # 确认流程中：落盘回调不得抢先弹下一条
            return
        current = self._current
        if current is not None and not current.is_test:
            # 用 get_task 而非 task：任务可能在弹窗显示期间被删除（PRD 4.2 支持删除自定义提醒）
            task = self._scheduler.config.get_task(current.task_id)
            if task is None or task.status is not TaskStatus.PENDING:
                LOG.info("当前提醒已失效，关闭浮窗: %s", current.task_id)
                self._dismiss()
                self.sync()
                return
        self.sync()

    def on_due(self, task_ids: list[str]) -> None:
        """tick 返回新到期任务后的入口；队列已由 Scheduler 维护，这里只管展示。"""
        self.sync()

    def quit_cleanup(self) -> None:
        """退出程序：这是唯一允许程序主动关闭浮窗的路径（PRD 5.2 例外）。"""
        if self._popup is not None:
            try:
                self._popup.allow_app_close()
                self._popup.close()
            except Exception:  # pragma: no cover - 退出路径兜底
                LOG.exception("关闭浮窗失败")
        self._popup = None
        self._current = None
        self._tests.clear()

    # ---------------- 内部 ----------------
    def _ensure_popup(self):
        if self._popup is None:
            if self._popup_factory is None:
                raise RuntimeError("未提供浮窗工厂")
            popup = self._popup_factory()
            popup.ack_requested.connect(self._on_ack)
            self._popup = popup
        return self._popup

    def _show(self, reminder: Reminder) -> None:
        popup = self._ensure_popup()
        popup.set_always_on_top(
            bool(self._scheduler.config.settings.get("popup_always_on_top", True))
        )
        self._current = reminder
        popup.show_reminder(reminder, screen=_target_screen())
        LOG.info("显示提醒: %s (%s)", reminder.task_id, "测试" if reminder.is_test else "到期")
        self.shown.emit(reminder.task_id)

    def _dismiss(self) -> None:
        current = self._current
        if current is None:
            return
        if self._popup is not None:
            self._popup.hide_after_ack()
        self._current = None
        self.closed.emit(current.task_id)

    def _on_ack(self, task_id: str) -> None:
        current = self._current
        if current is None or current.task_id != task_id:
            LOG.warning("忽略过期确认: %s", task_id)
            return
        if current.is_test:
            self._dismiss()
            self.ack_received.emit(task_id)
            self.sync()
            return
        # 真实提醒：先落盘（Scheduler.acknowledge 内部 persist → on_change），再关窗，
        # 再显示下一条。确认期间用 _acking 抑制 on_change 抢跑。
        self._acking = True
        try:
            next_id = self._scheduler.acknowledge(task_id)
        finally:
            self._acking = False
        LOG.info("确认提醒: %s，下一条: %s", task_id, next_id)
        self._dismiss()
        self.ack_received.emit(task_id)
        self.sync()
