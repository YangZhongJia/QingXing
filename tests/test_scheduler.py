"""调度核心测试：覆盖 TC-001/002/003/006/007/008/012/013/014/015/016/017/018/021/028/029。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.models import TaskStatus
from src.scheduler import SchedulerError

def test_first_run_creates_two_active_tasks(make_ctx):
    """TC-001 首次启动：两个默认任务均开启且已开始倒计时。"""
    ctx = make_ctx()
    ids = [task.id for task in ctx.scheduler.tasks]
    assert ids == ["stand_up", "drink_water"]
    for task_id, interval in (("stand_up", 30), ("drink_water", 40)):
        task = ctx.scheduler.task(task_id)
        assert task.enabled is True
        assert task.status is TaskStatus.ACTIVE
        assert task.interval_minutes == interval
        assert task.next_due_at == ctx.clock.now_utc() + timedelta(minutes=interval)
    # 启动后不立即弹出
    assert ctx.tick() == []
    assert ctx.head is None


def test_stand_up_due_at_30_minutes(make_ctx):
    """TC-006 推进 30 分钟：只有久坐提醒到期。"""
    ctx = make_ctx()
    ctx.advance_minutes(30)
    assert ctx.tick() == ["stand_up"]
    assert ctx.scheduler.task("stand_up").status is TaskStatus.PENDING
    assert ctx.scheduler.task("drink_water").status is TaskStatus.ACTIVE
    assert ctx.head == "stand_up"


def test_drink_water_due_at_40_minutes(make_ctx):
    """TC-007 推进 40 分钟：喝水提醒到期。"""
    ctx = make_ctx(stand=300)
    ctx.advance_minutes(40)
    assert ctx.tick() == ["drink_water"]
    assert ctx.head == "drink_water"


def test_acknowledge_rearms_from_ack_time(make_ctx):
    """TC-008 点击我知道了：以确认时刻为起点重计时。"""
    ctx = make_ctx(drink=600)
    ctx.advance_minutes(30)
    ctx.tick()
    ctx.advance_minutes(7)  # 10:37 确认
    assert ctx.scheduler.acknowledge("stand_up") is None
    task = ctx.scheduler.task("stand_up")
    assert task.status is TaskStatus.ACTIVE
    assert task.next_due_at == ctx.clock.now_utc() + timedelta(minutes=30)
    ctx.advance_minutes(29)
    assert ctx.tick() == []
    ctx.advance_minutes(1)
    assert ctx.tick() == ["stand_up"]


def test_same_task_overdue_two_hours_keeps_single_pending(make_ctx):
    """TC-013 同一任务逾期 2 小时：只有一条待确认。"""
    ctx = make_ctx()
    ctx.advance_minutes(120)
    assert ctx.tick() == ["stand_up", "drink_water"]
    assert ctx.config.queue_ids() == ["stand_up", "drink_water"]
    assert ctx.tick() == []
    ctx.advance_minutes(60)
    assert ctx.tick() == []
    assert len(ctx.config.pending_queue) == 2


def test_ack_does_not_touch_other_task(make_ctx):
    """TC-014 确认喝水不影响久坐倒计时。"""
    ctx = make_ctx(stand=60)
    ctx.advance_minutes(40)
    ctx.tick()
    stand_due_before = ctx.scheduler.task("stand_up").next_due_at
    assert ctx.scheduler.acknowledge("drink_water") is None
    assert ctx.head is None
    assert ctx.scheduler.task("stand_up").next_due_at == stand_due_before


def test_simultaneous_due_queues_fifo_then_stable_by_id(make_ctx):
    """TC-012 同时到期：一次一条，按到期时刻、同刻按任务 ID 排序。"""
    ctx = make_ctx(stand=30, drink=30)
    ctx.advance_minutes(30)
    assert ctx.tick() == ["stand_up", "drink_water"]
    assert ctx.config.queue_ids() == ["drink_water", "stand_up"]  # 同刻按 ID 稳定排序
    assert ctx.scheduler.head_id() == "drink_water"
    assert ctx.scheduler.acknowledge("drink_water") == "stand_up"
    assert ctx.scheduler.acknowledge("stand_up") is None
    assert ctx.head is None


def test_only_head_can_be_acknowledged(make_ctx):
    ctx = make_ctx(stand=30, drink=30)
    ctx.advance_minutes(30)
    ctx.tick()
    with pytest.raises(SchedulerError):
        ctx.scheduler.acknowledge("stand_up")
    with pytest.raises(SchedulerError):
        ctx.scheduler.acknowledge("not_exists")


def test_acknowledge_without_pending_raises(make_ctx):
    ctx = make_ctx()
    with pytest.raises(SchedulerError):
        ctx.scheduler.acknowledge("stand_up")


def test_interval_change_while_active_recomputes_from_save_time(make_ctx):
    """TC-015 修改未到期任务的间隔：从保存时刻重算。"""
    ctx = make_ctx()
    ctx.advance_minutes(10)
    ctx.scheduler.set_interval("stand_up", 20)
    assert ctx.scheduler.task("stand_up").next_due_at == ctx.clock.now_utc() + timedelta(minutes=20)
    ctx.advance_minutes(19)
    assert ctx.tick() == []
    ctx.advance_minutes(1)
    assert ctx.tick() == ["stand_up"]


def test_interval_change_while_pending_keeps_popup(make_ctx):
    """TC-016 弹窗期间改间隔：弹窗保留，确认后按新间隔计时。"""
    ctx = make_ctx()
    ctx.advance_minutes(30)
    ctx.tick()
    ctx.scheduler.set_interval("stand_up", 15)
    assert ctx.scheduler.task("stand_up").status is TaskStatus.PENDING
    assert ctx.head == "stand_up"
    ctx.scheduler.acknowledge("stand_up")
    assert ctx.scheduler.task("stand_up").next_due_at == ctx.clock.now_utc() + timedelta(minutes=15)


def test_disable_task_cancels_its_pending_reminder(make_ctx):
    """TC-017 关闭任务：对应定时停止，待确认提醒撤销并记 cancelled 事件。"""
    ctx = make_ctx()
    ctx.advance_minutes(30)
    ctx.tick()
    ctx.scheduler.set_enabled("stand_up", False)
    assert ctx.scheduler.task("stand_up").status is TaskStatus.DISABLED
    assert ctx.head is None
    ctx.advance_minutes(120)
    assert ctx.tick() == ["drink_water"]
    assert ctx.scheduler.task("stand_up").status is TaskStatus.DISABLED


def test_enable_task_starts_full_interval(make_ctx):
    ctx = make_ctx()
    ctx.scheduler.set_enabled("stand_up", False)
    ctx.advance_minutes(5)
    ctx.scheduler.set_enabled("stand_up", True)
    task = ctx.scheduler.task("stand_up")
    assert task.status is TaskStatus.ACTIVE
    assert task.next_due_at == ctx.clock.now_utc() + timedelta(minutes=30)


def test_pause_freezes_remaining_and_resume_continues(make_ctx):
    """TC-018 全局暂停冻结剩余时间，恢复后继续。"""
    ctx = make_ctx()
    ctx.advance_minutes(10)
    ctx.scheduler.pause()
    assert ctx.scheduler.is_paused() is True
    stand = ctx.scheduler.task("stand_up")
    assert stand.status is TaskStatus.PAUSED
    assert stand.remaining_seconds == 20 * 60
    ctx.advance_minutes(30)
    assert ctx.tick() == []
    ctx.scheduler.resume()
    assert ctx.scheduler.task("stand_up").next_due_at == ctx.clock.now_utc() + timedelta(minutes=20)
    ctx.advance_minutes(19)
    assert ctx.tick() == []
    ctx.advance_minutes(1)
    assert ctx.tick() == ["stand_up"]


def test_ack_while_paused_restarts_full_interval_on_resume(make_ctx):
    """PRD 3.2：暂停期间确认弹窗，恢复后从完整间隔开始。"""
    ctx = make_ctx()
    ctx.advance_minutes(30)
    ctx.tick()
    ctx.scheduler.pause()
    ctx.scheduler.acknowledge("stand_up")
    assert ctx.scheduler.task("stand_up").status is TaskStatus.PAUSED
    assert ctx.scheduler.task("stand_up").remaining_seconds == 30 * 60
    ctx.scheduler.resume()
    assert ctx.scheduler.task("stand_up").next_due_at == ctx.clock.now_utc() + timedelta(minutes=30)


def test_pause_keeps_pending_popup(make_ctx):
    ctx = make_ctx(drink=600)
    ctx.advance_minutes(30)
    ctx.tick()
    ctx.scheduler.pause()
    assert ctx.head == "stand_up"
    assert ctx.scheduler.task("stand_up").status is TaskStatus.PENDING
    ctx.scheduler.acknowledge("stand_up")
    ctx.scheduler.resume()
    ctx.advance_minutes(30)
    assert ctx.tick() == ["stand_up"]


def test_wake_after_two_hours_single_reminder_each(make_ctx):
    """TC-021 休眠 2 小时后唤醒：每任务最多一条，不批量爆发。"""
    ctx = make_ctx()
    ctx.clock.advance(2 * 3600)  # 模拟休眠期间定时器未触发
    assert sorted(ctx.tick()) == ["drink_water", "stand_up"]
    assert len(ctx.config.pending_queue) == 2
    assert ctx.tick() == []
    ctx.advance_minutes(180)
    assert ctx.tick() == []


def test_clock_backward_jump_clamps_schedule(make_ctx):
    """TC-029 系统时间回拨 1 小时：不崩溃，到期时间夹回一个间隔内。"""
    ctx = make_ctx()
    ctx.advance_minutes(10)
    ctx.clock.set_now(ctx.clock.now_utc() - timedelta(hours=1))
    assert ctx.tick() == []
    task = ctx.scheduler.task("stand_up")
    assert task.next_due_at is not None
    assert task.next_due_at <= ctx.clock.now_utc() + timedelta(minutes=30)


def test_clock_forward_jump_does_not_storm(make_ctx):
    """TC-029 系统时间前跳 5 小时：每任务只补发一条。"""
    ctx = make_ctx()
    ctx.clock.advance(5 * 3600)
    assert len(ctx.tick()) == 2
    assert len(ctx.config.pending_queue) == 2
    assert ctx.tick() == []


def test_test_reminder_does_not_touch_schedule(make_ctx):
    """TC-028 测试按钮：不改状态、不落盘、不影响倒计时。"""
    ctx = make_ctx()
    ctx.advance_minutes(30)
    ctx.tick()
    before = ctx.config.to_dict()
    saves_before = len(ctx.saves)
    reminder = ctx.scheduler.make_test_reminder("drink_water")
    assert reminder.is_test is True
    assert reminder.title == "记得喝点水"
    assert ctx.config.to_dict() == before
    assert len(ctx.saves) == saves_before


def test_persist_happens_before_ui_notification(make_ctx):
    """PRD 3.3：先持久化已确认状态，再关闭窗口。"""
    ctx = make_ctx()
    ctx.advance_minutes(30)
    ctx.tick()
    ctx.order.clear()
    ctx.scheduler.acknowledge("stand_up")
    assert ctx.order[:2] == ["persist", "ui"]


def test_countdown_helpers(make_ctx):
    ctx = make_ctx()
    assert ctx.scheduler.remaining_seconds_for("stand_up") == 30 * 60
    ctx.advance_minutes(10)
    assert ctx.scheduler.remaining_seconds_for("stand_up") == 20 * 60
    assert ctx.scheduler.next_due_for("stand_up") == ctx.clock.now_utc() + timedelta(minutes=20)
    ctx.scheduler.set_enabled("stand_up", False)
    assert ctx.scheduler.remaining_seconds_for("stand_up") is None
