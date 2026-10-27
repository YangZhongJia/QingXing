"""自定义提醒：任意新增的提醒也能进入调度并触发强提醒浮窗。

覆盖三层：
- Scheduler.add_task / remove_task 的状态机与持久化；
- 主窗：新增面板、动态卡片与删除按钮；
- 托盘：菜单「测试 X」与 tooltip 自动包含新提醒。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.models import TaskStatus, ValidationError, default_config_now
from src.scheduler import Scheduler, SchedulerError
from src.tray_manager import TrayManager
from src.ui.main_window import MainWindow
from tests.conftest import START


# ---------------- 调度层 ----------------

def test_add_task_persists_and_schedules(make_ctx):
    ctx = make_ctx()
    task = ctx.scheduler.add_task("眼保健操", "看看远处，放松眼睛", "20")

    assert task.id == "custom_1"
    assert (task.title, task.interval_minutes) == ("眼保健操", 20)
    assert task.next_due_at == ctx.clock.now_utc() + timedelta(minutes=20)
    assert ctx.order[-2:] == ["persist", "ui"]  # 先落盘，再通知界面
    assert any(item["id"] == "custom_1" for item in ctx.saves[-1]["tasks"])


def test_add_task_ids_increment(make_ctx):
    ctx = make_ctx()
    first = ctx.scheduler.add_task("眼保健操", "", "20")
    second = ctx.scheduler.add_task("颈椎操", "", "45")

    assert (first.id, second.id) == ("custom_1", "custom_2")
    assert [t.id for t in ctx.scheduler.tasks][:2] == ["stand_up", "drink_water"]


def test_add_task_defaults_for_empty_text(make_ctx):
    ctx = make_ctx()
    task = ctx.scheduler.add_task("   ", "", "30")

    assert task.title == "自定义提醒"
    assert task.message
    assert task.enabled is True


@pytest.mark.parametrize("raw", ["0", "721", "abc", "", "1.5", None, True])
def test_add_task_rejects_bad_interval_without_saving(make_ctx, raw):
    ctx = make_ctx()
    saves_before = len(ctx.saves)

    with pytest.raises(ValidationError):
        ctx.scheduler.add_task("眼保健操", "", raw)

    assert len(ctx.saves) == saves_before
    assert ctx.scheduler.config.get_task("custom_1") is None


def test_custom_task_fires_and_reschedules(make_ctx):
    ctx = make_ctx(stand=300, drink=600)
    ctx.scheduler.add_task("眼保健操", "看看远处", "10")

    ctx.advance_minutes(10)
    assert ctx.tick() == ["custom_1"]
    reminder = ctx.scheduler.head_reminder()
    assert reminder is not None and reminder.title == "眼保健操"

    ctx.scheduler.acknowledge("custom_1")
    task = ctx.scheduler.config.task("custom_1")
    assert task.next_due_at == ctx.clock.now_utc() + timedelta(minutes=10)


def test_remove_custom_task_drops_pending(make_ctx):
    ctx = make_ctx(stand=300, drink=600)
    ctx.scheduler.add_task("眼保健操", "", "5")
    ctx.advance_minutes(5)
    ctx.tick()
    assert "custom_1" in ctx.scheduler.config.queue_ids()

    ctx.scheduler.remove_task("custom_1")

    assert ctx.scheduler.config.get_task("custom_1") is None
    assert "custom_1" not in ctx.scheduler.config.queue_ids()
    assert ctx.saves[-1]["tasks"]


def test_remove_builtin_or_unknown_is_rejected(make_ctx):
    ctx = make_ctx()
    with pytest.raises(SchedulerError):
        ctx.scheduler.remove_task("stand_up")
    with pytest.raises(SchedulerError):
        ctx.scheduler.remove_task("custom_404")
    assert len(ctx.scheduler.tasks) == 2


def test_add_task_while_paused_starts_frozen(make_ctx):
    """PRD 3.2：暂停期间新增的提醒也要冻结剩余时间，恢复后从完整间隔开始。"""
    ctx = make_ctx()
    ctx.scheduler.pause()
    task = ctx.scheduler.add_task("护眼", "", "5")

    assert task.status is TaskStatus.PAUSED
    assert task.remaining_seconds == 300
    assert task.next_due_at is None

    ctx.advance_minutes(10)
    assert ctx.tick() == []  # 暂停中新加的提醒不该立刻到期

    ctx.scheduler.resume()
    resumed = ctx.scheduler.config.task("custom_1")
    assert resumed.next_due_at == ctx.clock.now_utc() + timedelta(minutes=5)


def test_custom_task_survives_reload(tmp_path):
    from src import storage
    from src.clock import FakeClock

    clock = FakeClock(START)
    path = tmp_path / "config.json"
    config = default_config_now(clock.now_utc())
    scheduler = Scheduler(config, clock, persist=lambda cfg: storage.save(path, cfg))
    scheduler.add_task("眼保健操", "看看远处", "20")

    loaded, warnings = storage.load(path, clock.now_utc())

    assert warnings == []
    task = loaded.get_task("custom_1")
    assert task is not None and task.title == "眼保健操"


# ---------------- 主窗 ----------------

@pytest.fixture
def win(qtbot, make_ctx):
    ctx = make_ctx()
    window = MainWindow(ctx.scheduler, version="1.0.0")
    qtbot.addWidget(window)
    window.show()
    return window, ctx


def test_new_task_gets_card(win):
    window, ctx = win
    ctx.scheduler.add_task("眼保健操", "看看远处", "20")
    window.refresh()

    assert len(window.cards) == 3
    card = window.cards["custom_1"]
    assert card.title_label.text() == "眼保健操"


def test_delete_button_only_on_custom_cards(win):
    window, ctx = win
    ctx.scheduler.add_task("眼保健操", "", "20")
    window.refresh()

    assert window.cards["stand_up"].delete_button.isHidden()
    assert not window.cards["custom_1"].delete_button.isHidden()


def test_delete_custom_card_removes_task(win):
    window, ctx = win
    ctx.scheduler.add_task("眼保健操", "", "20")
    window.refresh()

    window.cards["custom_1"].delete_button.click()

    assert ctx.scheduler.config.get_task("custom_1") is None
    assert "custom_1" not in window.cards
    assert len(window.cards) == 2
    assert window.cards_layout.indexOf(window.cards["stand_up"]) == 0
    assert window.cards_layout.indexOf(window.cards["drink_water"]) == 1
    assert window.cards_layout.itemAt(2).spacerItem() is not None
    assert window.cards_area.verticalScrollBar().value() == 0


def test_add_dialog_creates_task(win):
    window, ctx = win
    dialog = window.open_add_dialog()
    dialog.title_input.setText("眼保健操")
    dialog.message_input.setText("看看远处")
    dialog.interval_input.setText("25")
    dialog.create_button.click()

    task = ctx.scheduler.config.get_task("custom_1")
    assert task is not None
    assert (task.title, task.message, task.interval_minutes) == ("眼保健操", "看看远处", 25)
    assert dialog.isVisible() is False
    assert "custom_1" in window.cards
    assert window.toast.isVisible()
    assert "眼保健操" in window.toast.text()


def test_add_dialog_invalid_interval_keeps_dialog_open(win):
    window, ctx = win
    dialog = window.open_add_dialog()
    dialog.title_input.setText("眼保健操")
    dialog.interval_input.setText("0")
    dialog.create_button.click()

    assert ctx.scheduler.config.get_task("custom_1") is None
    assert dialog.isVisible() is True
    assert dialog.error_label.isVisible()
    assert "1~720" in dialog.error_label.text()


def test_add_dialog_requires_title(win):
    window, ctx = win
    dialog = window.open_add_dialog()
    dialog.title_input.setText("   ")
    dialog.interval_input.setText("20")
    dialog.create_button.click()

    assert ctx.scheduler.config.get_task("custom_1") is None
    assert "标题" in dialog.error_label.text()


def test_close_flushes_unsaved_interval(win):
    """PRD 4.2：未保存的间隔编辑在关闭前不静默丢弃（这里自动保存并落盘）。"""
    window, ctx = win
    card = window.cards["stand_up"]
    card.input.setText("25")

    window.close()

    assert ctx.scheduler.config.task("stand_up").interval_minutes == 25
    assert ctx.order[-2:] == ["persist", "ui"]


def test_close_keeps_invalid_edit_and_shows_error(win):
    window, ctx = win
    card = window.cards["stand_up"]
    card.input.setText("abc")

    window.close()

    assert ctx.scheduler.config.task("stand_up").interval_minutes == 30
    assert card.error_label.isVisible()


# ---------------- 托盘 ----------------

def test_tray_menu_and_tooltip_follow_task_list(qapp, make_ctx):
    ctx = make_ctx()
    ctx.scheduler.add_task("眼保健操", "", "20")
    tray = TrayManager(ctx.scheduler)

    texts = [action.text() for action in tray.context_menu().actions()]
    assert "测试「眼保健操」" in texts
    assert "测试久坐提醒" in texts

    tray.refresh()
    assert "眼保健操" in tray.tooltip_text()
