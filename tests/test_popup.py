"""提醒浮窗测试（FR-08/09/10/11，PRD 3.3 / 5.5 / 6 的 TC-010~TC-013）。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QPushButton, QToolButton

from src.models import Reminder
from src.ui.reminder_popup import (
    POPUP_MARGIN,
    POPUP_MAX_HEIGHT,
    POPUP_MIN_HEIGHT,
    POPUP_WIDTH,
    ReminderPopup,
    compute_position,
)

TASK_HEIGHT = 40  # 1080p 屏幕底部 40px 任务栏


def _reminder() -> Reminder:
    return Reminder(
        "stand_up",
        "该起来活动一下啦",
        "已经坐了一会儿，起身走动或伸展一下吧。",
        "stand",
    )


# ---------------------------------------------------------------- compute_position


def test_compute_position_bottom_right() -> None:
    available = QRect(0, 0, 1920, 1080 - TASK_HEIGHT)
    assert available == QRect(0, 0, 1920, 1040)
    pos = compute_position(available, QSize(360, 150))
    assert (pos.x(), pos.y()) == (1544, 874)
    # 右边/底边各留 16px
    assert 1920 - (pos.x() + 360) == POPUP_MARGIN
    assert 1040 - (pos.y() + 150) == POPUP_MARGIN


def test_compute_position_second_screen() -> None:
    available = QRect(1920, 0, 1920, 1040)
    pos = compute_position(available, QSize(POPUP_WIDTH, POPUP_MIN_HEIGHT))
    assert pos.x() >= 1920 + 1544, "必须落在第二块屏的可用区内"
    assert pos.x() == 1920 + 1544
    assert pos.x() + POPUP_WIDTH <= available.right() + 1


def test_compute_position_small_screen_keeps_inside() -> None:
    available = QRect(0, 0, 400, 300)
    pos = compute_position(available, QSize(POPUP_WIDTH, POPUP_MIN_HEIGHT))
    assert pos.x() >= available.left()
    assert pos.y() >= available.top()
    assert pos.x() + POPUP_WIDTH <= available.right() + 1
    assert pos.y() + POPUP_MIN_HEIGHT <= available.bottom() + 1


# ---------------------------------------------------------------- 窗口形态


def test_window_flags(qtbot, make_ctx) -> None:
    popup = ReminderPopup()
    qtbot.addWidget(popup)
    flags = popup.windowFlags()
    assert bool(flags & Qt.Tool)
    assert bool(flags & Qt.FramelessWindowHint)
    assert bool(flags & Qt.WindowStaysOnTopHint)
    assert popup.testAttribute(Qt.WA_ShowWithoutActivating)

    reminder = make_ctx().scheduler.reminder_for("stand_up")
    popup.show_reminder(reminder)
    assert popup.isVisible()
    assert popup.current_task_id() == "stand_up"
    assert popup.title_label.text() == reminder.title
    assert popup.message_label.text() == reminder.message


def test_no_close_button_and_single_primary_button(qtbot) -> None:
    popup = ReminderPopup()
    qtbot.addWidget(popup)
    popup.show_reminder(_reminder())

    assert popup.windowTitle() == ""  # 无标题栏文本
    # 不存在任何 QToolButton / 关闭类按钮
    assert popup.findChildren(QToolButton) == []
    texts = [b.text() for b in popup.findChildren(QPushButton)]
    assert "✕" not in "".join(texts)
    for forbidden in ("关闭", "退出", "×", "✕", "X"):
        assert forbidden not in texts
    assert texts == ["我知道了"], f"只允许唯一主按钮，实际：{texts}"


def test_esc_deactivate_and_close_are_ignored(qtbot) -> None:
    popup = ReminderPopup()
    qtbot.addWidget(popup)
    popup.show_reminder(_reminder())
    assert popup.isVisible()

    # Esc 不关闭 —— 直接投递按键事件，因为窗口不抢焦点拿不到真实键盘焦点
    QApplication.sendEvent(
        popup, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    )
    assert popup.isVisible()

    # 失焦不关闭
    QApplication.sendEvent(popup, QEvent(QEvent.Type.WindowDeactivate))
    assert popup.isVisible()

    # 系统 close（Alt+F4 走同一条路径）被 closeEvent 拦截
    assert popup.close() is False
    assert popup.isVisible()
    assert popup.current_task_id() == "stand_up"


def test_ack_emits_once_with_task_id(qtbot) -> None:
    popup = ReminderPopup()
    qtbot.addWidget(popup)
    popup.show_reminder(_reminder())

    emissions: list[str] = []
    popup.ack_requested.connect(emissions.append)

    with qtbot.waitSignal(popup.ack_requested, timeout=2000) as blocker:
        qtbot.mouseClick(popup.ack_button, Qt.LeftButton)
    assert blocker.args == ["stand_up"]

    # 重复点击不重复发信号；确认后窗口也必须仍然可见（等应用落盘才关）
    qtbot.mouseClick(popup.ack_button, Qt.LeftButton)
    assert emissions == ["stand_up"]
    assert popup.isVisible()


def test_hide_after_ack_allows_close(qtbot) -> None:
    popup = ReminderPopup()
    qtbot.addWidget(popup)
    popup.show_reminder(_reminder())
    assert popup.close() is False  # 落盘前拦下

    popup.show_reminder(_reminder())
    popup.hide_after_ack()
    assert not popup.isVisible()


def test_allow_app_close_allows_close(qtbot) -> None:
    popup = ReminderPopup()
    qtbot.addWidget(popup)
    popup.show_reminder(_reminder())
    popup.allow_app_close()
    assert popup.close() is True
    assert not popup.isVisible()


def test_size_is_clamped(qtbot) -> None:
    popup = ReminderPopup()
    qtbot.addWidget(popup)
    popup.show_reminder(_reminder())
    assert popup.width() == POPUP_WIDTH
    assert POPUP_MIN_HEIGHT <= popup.height() <= POPUP_MAX_HEIGHT

    long_reminder = Reminder(
        "drink_water",
        "记得喝点水",
        "工作辛苦了，喝几口水吧。" * 12,
        "drink",
    )
    popup.show_reminder(long_reminder)
    assert POPUP_MIN_HEIGHT <= popup.height() <= POPUP_MAX_HEIGHT


def test_position_uses_screen_available_geometry(qtbot) -> None:
    screen = QApplication.primaryScreen()
    assert screen is not None
    popup = ReminderPopup()
    qtbot.addWidget(popup)
    popup.show_reminder(_reminder(), screen=screen)
    expected = compute_position(screen.availableGeometry(), popup.size())
    assert popup.pos() == expected


def test_always_on_top_toggle(qtbot) -> None:
    popup = ReminderPopup()
    qtbot.addWidget(popup)
    popup.show_reminder(_reminder())

    popup.set_always_on_top(False)
    assert not bool(popup.windowFlags() & Qt.WindowStaysOnTopHint)

    popup.set_always_on_top(True)
    assert bool(popup.windowFlags() & Qt.WindowStaysOnTopHint)
    assert popup.isVisible()


def test_popup_does_not_steal_focus(qtbot) -> None:
    popup = ReminderPopup()
    qtbot.addWidget(popup)
    popup.show_reminder(_reminder())
    assert popup.focusPolicy() == Qt.NoFocus
    focus = QApplication.focusWidget()
    assert focus is not popup
    assert focus is not popup.ack_button


def test_update_clock_and_visibility(qtbot) -> None:
    popup = ReminderPopup()
    qtbot.addWidget(popup)
    popup.show_reminder(_reminder())
    assert popup.isVisible()
    popup.update_clock("14:05:09")
    assert popup.time_label.text() == "14:05:09"
    popup.update_clock("14:05:10")
    assert popup.time_label.text() == "14:05:10"
    assert popup.isVisible()
    assert popup.current_task_id() == "stand_up"


def test_compute_position_custom_margin() -> None:
    available = QRect(0, 0, 800, 600)
    pos = compute_position(available, QSize(360, 150), margin=0)
    assert pos == QPoint(800 - 360, 600 - 150)
