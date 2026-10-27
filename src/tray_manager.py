"""系统托盘（FR-19 / FR-08 / FR-09 / FR-20 / PRD 4.2、5.6）。

硬规则（PRD 3.3 / 5.1 / 开发提示词第 5 条）：健康提醒只能由 ReminderManager 的
自定义浮窗展示、且只能点「我知道了」关闭。本模块的 `show_message()` 只允许用于
「已在后台运行」这类可自动消失的一次性系统提示，**绝不能**用于健康提醒。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

logger = logging.getLogger(__name__)

# 不 import src.ui（会形成循环依赖），自己按项目根定位资源。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_APP_ICON_PATH = _PROJECT_ROOT / "assets" / "app.ico"

TOOLTIP_PREFIX = "健康提醒"
TASK_LABELS: dict[str, str] = {"stand_up": "久坐", "drink_water": "喝水"}


def task_label(task) -> str:
    """托盘里的短标签：内置两条用「久坐/喝水」，自定义提醒用标题。"""
    return TASK_LABELS.get(task.id, task.title)


def _system_tray_available() -> bool:
    """单独一层包装，测试可 monkeypatch，无需真实桌面托盘。"""
    return bool(QSystemTrayIcon.isSystemTrayAvailable())


def _format_hhmm(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%H:%M")


class TrayManager(QObject):
    """托盘图标与右键菜单；业务动作只发信号，由 app.py 接线到 Scheduler。"""

    open_settings_requested = Signal()
    pause_toggle_requested = Signal()
    test_requested = Signal(str)  # "stand_up" | "drink_water"
    quit_requested = Signal()

    def __init__(self, scheduler, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scheduler = scheduler
        self._tray: QSystemTrayIcon | None = None
        self._tooltip_text = TOOLTIP_PREFIX
        self._tooltip_suffix = ""

        # 菜单在 __init__ 里建好：即便系统托盘不可用，也能查询动作、单测菜单结构。
        self._menu = QMenu()
        self._add_action("打开设置", self.open_settings_requested.emit)
        self._menu.addSeparator()
        self._act_pause = self._add_action("暂停提醒", self.pause_toggle_requested.emit)
        self._menu.addSeparator()
        self._act_tests: list[QAction] = []
        self._test_signature: tuple[str, ...] = ()
        self._sep_before_quit = self._menu.addSeparator()
        self._add_action("退出程序", self.quit_requested.emit)
        self._sync_test_actions()
        self.set_paused(bool(self._scheduler.is_paused()))

    # ---------------- 对外 ----------------

    def available(self) -> bool:
        return _system_tray_available()

    def start(self) -> bool:
        """创建并显示托盘图标；托盘不可用时返回 False，不抛异常。"""
        if self._tray is not None:
            return True
        if not self.available():
            logger.warning("当前环境没有可用的系统托盘，跳过托盘图标创建")
            return False
        try:
            tray = QSystemTrayIcon(self._icon(), self)
            tray.setToolTip(self._tooltip_text)
            tray.setContextMenu(self._menu)
            tray.activated.connect(self._on_activated)
            tray.show()
        except Exception:  # pragma: no cover - 依赖桌面环境，防御性兜底
            logger.exception("创建系统托盘失败")
            return False
        self._tray = tray
        self.refresh()
        return True

    def set_paused(self, paused: bool) -> None:
        """在「暂停提醒 / 恢复提醒」之间切换菜单项文案（状态由 app 同步）。"""
        self._act_pause.setText("恢复提醒" if paused else "暂停提醒")
        self.refresh()

    def refresh(self) -> None:
        """每秒由 app 的 QTimer 调用：只更新 tooltip，不碰图标可见性。"""
        self._sync_test_actions()
        if self._scheduler.is_paused():
            text = f"{TOOLTIP_PREFIX} · 已暂停"
        else:
            parts = []
            for task in self._scheduler.tasks:
                due = self._scheduler.next_due_for(task.id)
                if due is not None:
                    parts.append(f"{task_label(task)} {_format_hhmm(due)}")
            detail = " / ".join(parts)
            text = f"{TOOLTIP_PREFIX} · 下次{detail}" if detail else f"{TOOLTIP_PREFIX} · 暂无计时任务"
        if self._tooltip_suffix:
            text = f"{text} {self._tooltip_suffix}"
        self._tooltip_text = text
        if self._tray is not None:
            self._tray.setToolTip(text)

    def _sync_test_actions(self) -> None:
        """任务列表变化时重建「测试 X 提醒」菜单项（新增/删除自定义提醒后自动生效）。"""
        signature = tuple(task.id for task in self._scheduler.tasks)
        if signature == self._test_signature:
            return
        for action in self._act_tests:
            self._menu.removeAction(action)
            action.deleteLater()
        self._act_tests = []
        for task in self._scheduler.tasks:
            label = task_label(task)
            text = f"测试{label}提醒" if task.id in TASK_LABELS else f"测试「{label}」"
            action = QAction(text, self)
            action.triggered.connect(
                lambda _checked=False, task_id=task.id: self.test_requested.emit(task_id)
            )
            self._menu.insertAction(self._sep_before_quit, action)
            self._act_tests.append(action)
        self._test_signature = signature

    def apply_settings(self, tooltip_suffix: str = "") -> None:
        """设置保存后刷新托盘状态；suffix 用于附加一次性说明（可为空）。"""
        self._tooltip_suffix = tooltip_suffix or ""
        self.refresh()

    def show_message(self, title: str, message: str) -> None:
        """仅一次性系统提示（如首次最小化到托盘），允许自动消失。

        健康提醒严禁走这里：showMessage 无法保证「点我知道了才关闭」（PRD 3.3）。
        """
        if self._tray is None:
            return
        self._tray.showMessage(
            title, message, QSystemTrayIcon.MessageIcon.Information, 4000
        )

    def set_icon_tooltip(self, text: str) -> None:
        """只写托盘 tooltip，不触发任何提醒语义。"""
        self._tooltip_text = text
        if self._tray is not None:
            self._tray.setToolTip(text)

    def tooltip_text(self) -> str:
        """当前 tooltip 文本（无托盘环境下也可断言）。"""
        return self._tooltip_text

    def context_menu(self) -> QMenu:
        return self._menu

    def stop(self) -> None:
        if self._tray is None:
            return
        tray = self._tray
        self._tray = None
        tray.hide()
        tray.setContextMenu(None)
        tray.deleteLater()

    # ---------------- 内部 ----------------

    def _add_action(self, text: str, slot) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(lambda _checked=False: slot())
        self._menu.addAction(action)
        return action

    def _icon(self) -> QIcon:
        if _APP_ICON_PATH.exists():
            icon = QIcon(str(_APP_ICON_PATH))
            if not icon.isNull():
                return icon
        style = QApplication.style()
        if style is not None:
            return style.standardIcon(QStyle.StandardPixmap.SP_DesktopIcon)
        return QIcon()  # pragma: no cover - 无 QApplication 时才会走到

    def _on_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_settings_requested.emit()
