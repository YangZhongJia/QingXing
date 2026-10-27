"""桌面健康提醒 v1.0 — 应用装配与入口（Lead 负责接线）。

启动顺序（PRD 5.7 / 6）：
    日志 → QApplication（唯一）→ 单实例抢占 → 读配置 → Scheduler（唯一）
    → 托盘 → 主窗 → 浮窗管理器 → 1s 全局 Timer → 事件循环
退出顺序（PRD FR-20）：
    停 Timer → 保存配置 → 关闭浮窗（唯一允许主动关窗的路径）→ 停托盘 → 释放单实例
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from src import storage
from src.clock import SystemClock
from src.logger import install_excepthook, setup_logging
from src.reminder_manager import ReminderManager
from src.scheduler import Scheduler
from src.single_instance import SingleInstance
from src.startup import apply as apply_startup
from src.tray_manager import TrayManager
from src.ui import load_stylesheet
from src.ui.main_window import MainWindow
from src.ui.reminder_popup import ReminderPopup

APP_NAME = "DesktopHealthReminder"
APP_VERSION = "1.0.0"
TICK_MS = 1000

LOG = logging.getLogger("app")


def _local_clock_text() -> str:
    return datetime.now().astimezone().strftime("%H:%M:%S")


class AppCore(QObject):
    """把所有部件装配在一起；测试脚本也可以只构造它来验证接线。"""

    def __init__(
        self,
        app: QApplication,
        config_path,
        exit_after_seconds: Optional[float] = None,
        schedule_offset_minutes: int = 0,
    ) -> None:
        super().__init__()
        self.app = app
        self.config_path = config_path
        self._quitting = False
        self._tray_hint_shown = False
        self._handlers_ready = False
        self._popup: Optional[ReminderPopup] = None

        clock = SystemClock()
        config, warnings = storage.load(config_path, clock.now_utc())
        for warning in warnings:
            LOG.warning(warning)
        repaired = config.repair(clock.now_utc())
        if repaired:
            LOG.warning("配置自愈: %s", "; ".join(repaired))
            storage.save(config_path, config)

        self.scheduler = Scheduler(
            config,
            clock,
            persist=lambda cfg: storage.save(self.config_path, cfg),
            on_change=self._on_state_changed,
            log=logging.getLogger("scheduler"),
        )
        # 仅用于 E2E：把两张卡的下次提醒整体平移到“现在 + offset”，不影响落盘逻辑。
        if schedule_offset_minutes:
            self._shift_schedule(schedule_offset_minutes)

        self.window = MainWindow(self.scheduler, version=APP_VERSION)
        self.tray = TrayManager(self.scheduler)
        self.manager = ReminderManager(self.scheduler, popup_factory=self._make_popup)
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self._handlers_ready = True

        if exit_after_seconds:
            QTimer.singleShot(int(exit_after_seconds * 1000), self.quit_app)

    # ---------------- 启动 ----------------
    def start(self) -> None:
        self.app.setStyleSheet(load_stylesheet())
        self._wire()
        if not self.tray.start():
            LOG.warning("系统托盘不可用，程序将仅显示主窗口")
            self.window.show_message("系统托盘不可用，退出请使用主窗口的关闭按钮。", "warning")
        self.tray.set_paused(self.scheduler.is_paused())
        self.window.show()
        self.window.refresh()
        self.timer.start()
        # 上次异常退出遗留的待确认提醒：启动时补显示
        self.manager.sync()
        ok, msg = apply_startup(bool(self.scheduler.config.settings.get("start_with_windows")))
        LOG.info("开机自启同步: %s %s", "成功" if ok else "失败", msg)
        LOG.info("启动完成，版本 %s，数据文件 %s", APP_VERSION, self.config_path)

    # ---------------- 信号接线 ----------------
    def _wire(self) -> None:
        self.window.settings_saved.connect(self._on_settings_saved)
        self.window.hidden_to_tray.connect(self._on_hidden_to_tray)
        self.window.quit_requested.connect(self.quit_app)

        self.tray.open_settings_requested.connect(self.show_main_window)
        self.tray.pause_toggle_requested.connect(self._on_pause_toggle)
        self.tray.test_requested.connect(self.manager.test_reminder)
        self.tray.quit_requested.connect(self.quit_app)

        self.timer.timeout.connect(self._on_tick)

    # ---------------- 每秒心跳 ----------------
    def _on_tick(self) -> None:
        due = self.scheduler.tick()
        if due:
            LOG.info("提醒到期: %s", ", ".join(due))
        self.manager.on_due(due)
        if self._popup is not None:
            self._popup.update_clock(_local_clock_text())
        self.window.refresh()
        self.tray.refresh()

    # ---------------- 回调 ----------------
    def _on_state_changed(self) -> None:
        self.window.refresh()
        self.tray.set_paused(self.scheduler.is_paused())
        if self._handlers_ready:
            self.manager.on_state_changed()

    def _make_popup(self) -> ReminderPopup:
        popup = ReminderPopup()
        popup.set_always_on_top(
            bool(self.scheduler.config.settings.get("popup_always_on_top", True))
        )
        self._popup = popup
        return popup

    def _on_settings_saved(self, settings: dict) -> None:
        ok, message = apply_startup(bool(settings.get("start_with_windows")))
        if not ok:
            self.window.show_message(message, "warning")
        if self._popup is not None:
            self._popup.set_always_on_top(bool(settings.get("popup_always_on_top", True)))

    def _on_hidden_to_tray(self) -> None:
        """closeEvent 已被 ignore，这里负责真正隐藏主窗（FR-19 关闭到托盘）。"""
        self.window.hide()
        if self._tray_hint_shown or not self.tray.available():
            return
        self._tray_hint_shown = True
        self.tray.show_message("健康提醒", "程序已最小化到托盘，仍会在后台按时提醒你。")

    def _on_pause_toggle(self) -> None:
        if self.scheduler.is_paused():
            self.scheduler.resume()
            LOG.info("恢复提醒")
        else:
            self.scheduler.pause()
            LOG.info("暂停提醒")
        self.tray.set_paused(self.scheduler.is_paused())
        self.window.refresh()

    def show_main_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        LOG.info("窗口已唤到前台（托盘/二次启动）")

    def _shift_schedule(self, offset_minutes: int) -> None:
        """E2E 辅助：把各任务下次提醒平移到 now+offset 分钟（不改变间隔配置）。"""
        from datetime import timedelta

        tasks = self.scheduler.config.tasks
        if isinstance(tasks, dict):
            tasks = list(tasks.values())
        for task in tasks:
            if task.enabled and task.next_due_at is not None:
                task.next_due_at = task.next_due_at + timedelta(minutes=offset_minutes)
        LOG.warning("E2E 辅助：下次提醒已整体平移 %d 分钟", offset_minutes)

    # ---------------- 退出 ----------------
    def quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        LOG.info("开始退出")
        self.timer.stop()
        try:
            self.scheduler.commit()
        except Exception:  # pragma: no cover - 退出兜底
            LOG.exception("退出前保存配置失败")
        self.manager.quit_cleanup()
        self.tray.stop()
        self.window.close()
        self.app.quit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="桌面健康提醒")
    parser.add_argument("--config", default=None, help="配置文件路径（默认位于用户数据目录）")
    parser.add_argument(
        "--exit-after", type=float, default=None, metavar="秒", help="调试用：N 秒后自动退出"
    )
    parser.add_argument(
        "--shift-minutes",
        type=int,
        default=0,
        metavar="分钟",
        help="调试/E2E 用：把下次提醒整体平移到 now+N 分钟",
    )
    return parser


def main(argv=None) -> int:
    raw_argv = list(sys.argv if argv is None else argv)
    args, qt_argv = build_parser().parse_known_args(raw_argv[1:])

    app = QApplication([raw_argv[0]] + qt_argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("健康提醒")
    app.setQuitOnLastWindowClosed(False)

    log_dir = storage.default_log_dir()
    setup_logging(log_dir)
    install_excepthook()
    LOG.info("进程启动: %s", raw_argv)

    instance = SingleInstance()
    if not instance.try_acquire():
        LOG.warning("检测到已有实例，通知其显示窗口后退出")
        return 0

    config_path = args.config or storage.default_config_path()
    core = AppCore(
        app,
        config_path,
        exit_after_seconds=args.exit_after,
        schedule_offset_minutes=args.shift_minutes,
    )
    # NFR-07：第二个实例被拦下后，首个实例把窗口唤到前台
    instance.activate_requested.connect(core.show_main_window)
    app.aboutToQuit.connect(core.quit_app)
    core.start()
    try:
        return app.exec()
    finally:
        instance.release()
        LOG.info("进程退出")


if __name__ == "__main__":
    sys.exit(main())
