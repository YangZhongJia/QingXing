"""ReminderManager 测试（FR-08 / FR-10 / FR-11 / FR-18 的展示时序）。

用假浮窗替换真实 QWidget：只验证「一次一张、先落盘再关窗、测试提醒不脏数据」，
真实窗口的焦点/关闭拦截由 tests/test_popup.py 覆盖。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal

from src.reminder_manager import ReminderManager


class FakePopup(QObject):
    ack_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.shown = []
        self.hidden_count = 0
        self.allow_close_count = 0
        self.on_top = None

    def show_reminder(self, reminder, screen=None):
        self.shown.append(reminder)

    def hide_after_ack(self):
        self.hidden_count += 1

    def allow_app_close(self):
        self.allow_close_count += 1

    def set_always_on_top(self, on):
        self.on_top = on


@pytest.fixture()
def manager(make_ctx):
    ctx = make_ctx()
    popup = FakePopup()
    mgr = ReminderManager(ctx.scheduler, popup_factory=lambda: popup)
    # 模拟 app.py 的接线：Scheduler 每次落盘后通知管理器刷新
    original = ctx.scheduler._on_change

    def chained():
        if original is not None:
            original()
        mgr.on_state_changed()

    ctx.scheduler._on_change = chained
    return ctx, mgr, popup


def _due_both(ctx):
    ctx.advance_minutes(40)
    assert set(ctx.tick()) == {"stand_up", "drink_water"}


def test_only_one_popup_at_a_time(manager):
    """TC-013 两条同时到期：一次只显示一张。"""
    ctx, mgr, popup = manager
    _due_both(ctx)
    mgr.on_due(["stand_up", "drink_water"])
    assert mgr.current.task_id == "stand_up"
    mgr.on_due(["stand_up", "drink_water"])
    assert len(popup.shown) == 1
    assert ctx.config.queue_ids() == ["stand_up", "drink_water"]


def test_ack_shows_next_and_persists_first(manager):
    """TC-009/TC-013：确认后按序显示下一条；关窗前已落盘。"""
    ctx, mgr, popup = manager
    _due_both(ctx)
    mgr.on_due([])
    assert mgr.current.task_id == "stand_up"

    ctx.order.clear()
    popup.ack_requested.emit("stand_up")

    assert popup.shown[-1].task_id == "drink_water"
    assert mgr.current.task_id == "drink_water"
    assert ctx.config.queue_ids() == ["drink_water"]
    # 落盘（persist）必须先于隐藏浮窗；顺序里 persist 在 ui 之前
    assert ctx.order[:2] == ["persist", "ui"]
    assert popup.hidden_count == 1


def test_ack_unknown_or_stale_id_ignored(manager):
    ctx, mgr, popup = manager
    _due_both(ctx)
    mgr.on_due([])
    popup.ack_requested.emit("drink_water")  # 不是队首
    assert mgr.current.task_id == "stand_up"
    assert ctx.config.queue_ids() == ["stand_up", "drink_water"]


def test_test_reminder_does_not_touch_state(manager):
    """TC-006 测试提醒：即时弹出、不落盘、不影响真实倒计时。"""
    ctx, mgr, popup = manager
    before = ctx.scheduler.task("stand_up").next_due_at
    saved_before = len(ctx.saves)
    reminder = mgr.test_reminder("stand_up")
    assert reminder is not None and reminder.is_test
    assert popup.shown[-1].is_test
    assert ctx.scheduler.task("stand_up").next_due_at == before
    assert ctx.config.queue_ids() == []
    assert len(ctx.saves) == saved_before

    popup.ack_requested.emit("stand_up")
    assert mgr.current is None
    assert ctx.scheduler.task("stand_up").next_due_at == before


def test_duplicate_test_reminder_ignored(manager):
    ctx, mgr, popup = manager
    assert mgr.test_reminder("stand_up") is not None
    assert mgr.test_reminder("stand_up") is None
    popup.ack_requested.emit("stand_up")
    assert mgr.current is None


def test_test_reminders_queue_in_order(manager):
    ctx, mgr, popup = manager
    mgr.test_reminder("stand_up")
    mgr.test_reminder("drink_water")
    assert mgr.current.task_id == "stand_up"
    assert mgr.pending_test_count == 1
    popup.ack_requested.emit("stand_up")
    assert mgr.current.task_id == "drink_water"
    popup.ack_requested.emit("drink_water")
    assert mgr.current is None


def test_disabling_task_dismisses_its_popup(manager):
    """TC-017 关闭任务后，其浮窗不得继续显示。"""
    ctx, mgr, popup = manager
    _due_both(ctx)
    mgr.on_due([])
    assert mgr.current.task_id == "stand_up"
    ctx.scheduler.set_enabled("stand_up", False)
    assert popup.hidden_count == 1
    # 关闭的任务不再显示；若队列里还有别的到期提醒，继续按序显示
    assert mgr.current is not None and mgr.current.task_id == "drink_water"


def test_quit_cleanup_is_only_forced_close_path(manager):
    ctx, mgr, popup = manager
    _due_both(ctx)
    mgr.on_due([])
    mgr.quit_cleanup()
    assert popup.allow_close_count == 1
    assert mgr.current is None


def test_removing_shown_task_dismisses_popup(manager):
    """回归：删除正在弹窗的自定义提醒，不能因 task() 抛 KeyError 把弹窗永久卡死。"""
    ctx, mgr, popup = manager
    ctx.scheduler.add_task("眼保健操", "看看远处", "5")
    ctx.advance_minutes(5)
    assert ctx.tick() == ["custom_1"]
    assert mgr.current is not None and mgr.current.task_id == "custom_1"

    ctx.scheduler.remove_task("custom_1")  # on_change 链会通知 manager

    assert mgr.current is None
    assert popup.hidden_count == 1
    assert ctx.scheduler.config.get_task("custom_1") is None


def test_unknown_task_test_reminder_returns_none(manager):
    ctx, mgr, popup = manager
    assert mgr.test_reminder("no_such_task") is None
    assert popup.shown == []
