"""E2E 截图：主窗与强提醒浮窗（真实 windows 平台）。

同时产出两类证据：
1. ``*_widget.png``：控件自身渲染，像素精确，不受桌面 z-order 影响；
2. ``*_screen.png``：真实桌面整屏截图，用于证明浮窗确实浮在桌面上。

    python scripts/e2e_screens.py --out docs/screenshots
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    args = parser.parse_args()

    os.environ["QT_QPA_PLATFORM"] = "windows"

    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication

    from src.clock import SystemClock
    from src.models import Reminder, TaskStatus, default_config_now, utc_now
    from src.scheduler import Scheduler
    from src.ui import load_stylesheet
    from src.ui.main_window import MainWindow
    from src.ui.reminder_popup import ReminderPopup

    app = QApplication(sys.argv[:1])
    app.setApplicationName("DesktopHealthReminder")
    app.setStyleSheet(load_stylesheet())
    screen = app.primaryScreen()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    now = utc_now()
    config = default_config_now(now)
    config.task("drink_water").next_due_at = now + timedelta(minutes=27)
    scheduler = Scheduler(config, SystemClock())
    config.task("stand_up").status = TaskStatus.PENDING
    # 第三条：用户自定义提醒（验证动态卡片与浮窗标题都能跟随新增任务）
    scheduler.add_task("眼保健操", "看看远处，放松眼睛。", 20)

    window = MainWindow(scheduler, version="1.0.0")
    popup = ReminderPopup()
    reminder = Reminder(
        "stand_up",
        "该起来活动一下啦",
        "已经坐了一会儿，起身走动或伸展一下吧。",
        "stand",
    )
    custom_reminder = Reminder(
        "custom_1",
        "眼保健操",
        "看看远处，放松眼睛。",
        "custom",
    )

    def save_widget(widget, name: str) -> None:
        target = out_dir / name
        ok = widget.grab().save(str(target), "PNG")
        print(f"[e2e] {target} ok={ok}", flush=True)

    def save_screen(name: str) -> None:
        # 多显示器：逐屏保存，浮窗显示在鼠标所在屏，只抓主屏会漏拍
        screens = app.screens()
        for index, screen_item in enumerate(screens):
            if len(screens) == 1:
                target = out_dir / name
            else:
                stem = Path(name).stem
                safe = screen_item.name().replace(" ", "_").replace("\\", "_")
                target = out_dir / f"{stem}_{index}_{safe}.png"
            pixmap = screen_item.grabWindow(0)
            ok = pixmap.save(str(target), "PNG")
            print(f"[e2e] {target} ok={ok} {pixmap.width()}x{pixmap.height()}", flush=True)

    def step_window() -> None:
        # 截图期间强制置顶：否则从后台终端启动的窗口会被前台 IDE 遮住
        window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        window.move(160, 96)
        window.show()
        window.raise_()
        window.activateWindow()
        window.setWindowState(window.windowState() | Qt.WindowActive)

        def done() -> None:
            save_widget(window, "main_window_widget.png")
            save_screen("main_window_screen.png")
            window.hide()
            QTimer.singleShot(600, step_popup)

        QTimer.singleShot(900, done)

    def step_popup() -> None:
        popup.show_reminder(reminder, screen)
        popup.update_clock("14:32:07")

        def done_builtin() -> None:
            save_widget(popup, "reminder_popup_widget.png")
            save_screen("reminder_popup_screen.png")
            popup.allow_app_close()
            popup.close()
            QTimer.singleShot(500, step_custom_popup)

        QTimer.singleShot(900, done_builtin)

    def step_custom_popup() -> None:
        popup.show_reminder(custom_reminder, screen)
        popup.update_clock("14:32:41")

        def done() -> None:
            save_widget(popup, "custom_popup_widget.png")
            save_screen("custom_popup_screen.png")
            popup.allow_app_close()
            popup.close()
            app.quit()

        QTimer.singleShot(900, done)

    QTimer.singleShot(300, step_window)
    QTimer.singleShot(12000, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
