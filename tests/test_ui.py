"""主窗口与任务卡片测试（FR-01/02/03/10/11/12/13/14，PRD 4.1）。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from PySide6.QtCore import QSize, Qt

from src.models import TaskStatus, ValidationError
from src.ui.main_window import MainWindow, SettingsPanel
from src.ui.task_card import DISABLED_OPACITY, TaskCard


@pytest.fixture
def ctx(make_ctx):
    return make_ctx()


@pytest.fixture
def win(qtbot, ctx):
    window = MainWindow(ctx.scheduler, version="1.0.0")
    qtbot.addWidget(window)
    window.show()
    return window


# ---------------------------------------------------------------- 主窗骨架


def test_window_shell(win, ctx) -> None:
    assert win.windowTitle() == "桌面健康提醒"
    assert win.size() == QSize(780, 600)
    assert win.minimumSize() == QSize(700, 530)
    assert win.title_label.text() == "健康提醒"
    assert set(win.cards) == {"stand_up", "drink_water"}
    assert isinstance(win.cards["stand_up"], TaskCard)
    assert win.version_label.text() == "版本 v1.0.0"
    assert win.cards["stand_up"].title_label.text() == "该起来活动一下啦"
    assert win.cards["drink_water"].title_label.text() == "记得喝点水"
    # 间隔默认值来自 scheduler
    assert win.cards["stand_up"].input.text() == "30"
    assert win.cards["drink_water"].input.text() == "40"


def test_status_badge_follows_pause(win, ctx) -> None:
    assert win.status_badge.text() == "运行中"
    assert win.pause_button.text() == "暂停提醒"

    ctx.scheduler.pause()
    win.refresh()
    assert win.status_badge.text() == "已暂停"
    assert win.pause_button.text() == "恢复提醒"
    assert win.cards["stand_up"].badge_label.text() == "已暂停"

    ctx.scheduler.resume()
    win.refresh()
    assert win.status_badge.text() == "运行中"
    assert win.pause_button.text() == "暂停提醒"


def test_pause_button_clicks_scheduler(win, ctx, qtbot) -> None:
    assert ctx.scheduler.is_paused() is False
    qtbot.mouseClick(win.pause_button, Qt.LeftButton)
    assert ctx.scheduler.is_paused() is True
    assert win.status_badge.text() == "已暂停"
    qtbot.mouseClick(win.pause_button, Qt.LeftButton)
    assert ctx.scheduler.is_paused() is False


# ---------------------------------------------------------------- 间隔编辑


def test_chip_click_sets_interval_and_recomputes_due(win, ctx, qtbot) -> None:
    card = win.cards["stand_up"]
    qtbot.mouseClick(card.chips[15], Qt.LeftButton)

    task = ctx.scheduler.task("stand_up")
    assert task.interval_minutes == 15
    assert card.input.text() == "15"
    expected_due = ctx.clock.now_utc() + timedelta(minutes=15)
    assert ctx.scheduler.next_due_for("stand_up") == expected_due
    assert ctx.saves, "保存间隔必须落盘"


def test_save_button_applies_typed_interval(win, ctx, qtbot) -> None:
    card = win.cards["drink_water"]
    card.input.setText("45")
    qtbot.mouseClick(card.save_button, Qt.LeftButton)
    assert ctx.scheduler.task("drink_water").interval_minutes == 45
    assert card.error_label.isVisible() is False


@pytest.mark.parametrize("raw", ["0", "721", "abc", "", "1.5"])
def test_invalid_interval_shows_error_without_changing_task(win, ctx, qtbot, raw) -> None:
    card = win.cards["stand_up"]
    before_saves = len(ctx.saves)
    card.input.setText(raw)
    qtbot.mouseClick(card.save_button, Qt.LeftButton)

    assert ctx.scheduler.task("stand_up").interval_minutes == 30
    assert len(ctx.saves) == before_saves, "校验失败不落盘"
    assert card.error_label.isVisible()
    assert card.input.property("invalid") == "true"

    # 每秒 refresh 不得把用户输入覆盖回已保存值（否则红字会和合法值并存）
    card.refresh()
    assert card.input.text() == raw
    assert card.error_label.isVisible()

    # 恢复合法值后错误清除
    card.input.setText("20")
    qtbot.mouseClick(card.save_button, Qt.LeftButton)
    assert card.error_label.isVisible() is False
    assert card.input.property("invalid") == "false"
    assert ctx.scheduler.task("stand_up").interval_minutes == 20


def test_scheduler_validation_error_is_the_one_surfaced(win, ctx) -> None:
    card = win.cards["stand_up"]
    with pytest.raises(ValidationError):
        ctx.scheduler.set_interval("stand_up", "abc")
    card.show_error("间隔必须是 1~720 的整数分钟。")
    assert "1~720" in card.error_label.text()
    card.clear_error()
    assert card.error_label.text() == ""


# ---------------------------------------------------------------- 开关 / 半透明


def test_disable_then_enable_card(win, ctx, qtbot) -> None:
    card = win.cards["drink_water"]
    assert card.opacity_effect.opacity() == pytest.approx(1.0)

    qtbot.mouseClick(card.enabled_check, Qt.LeftButton)
    assert card.enabled_check.isChecked() is False
    assert ctx.scheduler.task("drink_water").status == TaskStatus.DISABLED
    assert card.badge_label.text() == "已关闭"
    assert card.opacity_effect.opacity() == pytest.approx(DISABLED_OPACITY)
    assert card.property("cardDisabled") == "true"

    qtbot.mouseClick(card.enabled_check, Qt.LeftButton)
    assert card.enabled_check.isChecked() is True
    assert ctx.scheduler.task("drink_water").status == TaskStatus.ACTIVE
    assert card.opacity_effect.opacity() == pytest.approx(1.0)
    assert card.property("cardDisabled") == "false"


def test_main_window_omits_redundant_test_controls(win) -> None:
    assert not hasattr(win, "test_buttons")
    assert all(not hasattr(card, "test_button") for card in win.cards.values())


# ---------------------------------------------------------------- 关闭行为


def test_close_with_close_to_tray_ignored(win, ctx) -> None:
    ctx.config.settings["close_to_tray"] = True
    emitted: list[bool] = []
    win.hidden_to_tray.connect(lambda: emitted.append(True))
    quit_emitted: list[bool] = []
    win.quit_requested.connect(lambda: quit_emitted.append(True))

    win.close()
    assert emitted == [True]
    assert quit_emitted == []
    assert win.isVisible() is True


def test_close_without_close_to_tray_requests_quit(win, ctx) -> None:
    ctx.config.settings["close_to_tray"] = False
    quit_emitted: list[bool] = []
    win.quit_requested.connect(lambda: quit_emitted.append(True))
    win.hidden_to_tray.connect(lambda: pytest.fail("不应最小化到托盘"))

    win.close()
    assert quit_emitted == [True]
    assert win.isVisible() is False


# ---------------------------------------------------------------- 设置面板


def test_settings_panel_roundtrip(win, ctx) -> None:
    panel = win.open_settings()
    assert isinstance(panel, SettingsPanel)
    assert set(panel.checks) == {
        "start_with_windows",
        "close_to_tray",
        "popup_always_on_top",
        "sound_enabled",
    }
    # 打开时载入当前值
    assert panel.checks["close_to_tray"].isChecked() == ctx.config.settings["close_to_tray"]


def test_settings_panel_save_persists_and_signals(win, ctx, qtbot) -> None:
    panel = win.open_settings()
    panel.checks["close_to_tray"].setChecked(False)
    panel.checks["sound_enabled"].setChecked(True)

    with qtbot.waitSignal(win.settings_saved, timeout=2000) as blocker:
        qtbot.mouseClick(panel.save_button, Qt.LeftButton)

    payload = blocker.args[0]
    assert payload["close_to_tray"] is False
    assert payload["sound_enabled"] is True
    assert ctx.config.settings["close_to_tray"] is False
    assert ctx.config.settings["sound_enabled"] is True
    assert ctx.saves, "保存设置必须落盘"
    assert panel.isVisible() is False


def test_settings_cancel_does_not_change(win, ctx, qtbot) -> None:
    panel = win.open_settings()
    panel.checks["close_to_tray"].setChecked(False)
    qtbot.mouseClick(panel.cancel_button, Qt.LeftButton)
    assert ctx.config.settings["close_to_tray"] is True
    assert panel.isVisible() is False


# ---------------------------------------------------------------- 轻提示


def test_show_message(qtbot, ctx) -> None:
    window = MainWindow(ctx.scheduler)
    qtbot.addWidget(window)
    window.show()
    window.show_message("设置已保存。", "success")
    assert window.toast.isVisible()
    assert window.toast.text() == "设置已保存。"
    assert window.toast.property("kind") == "success"
    window.toast.hide()
    assert window.toast.isVisible() is False
