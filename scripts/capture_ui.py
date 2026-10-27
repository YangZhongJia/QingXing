"""真实桌面 UI 截图工具（不属于自动化测试，只在有桌面的机器上手动跑）。

用法：
    python scripts/capture_ui.py --out screenshots
    python scripts/capture_ui.py --out screenshots --scale 1.25

行为：
1. 用真实 ``windows`` 平台启动 QApplication（绝不 offscreen），填演示数据后依次显示
   主窗口与提醒浮窗，每次 ``QScreen.grabWindow(0)`` 存一张全屏 PNG 到 ``--out``。
2. ``--scale`` 先设 ``QT_SCALE_FACTOR`` 并重启自身一次（DPI 截图）。
3. 2000ms 兜底定时器 + ``os._exit``，任何异常下都不会挂住进程。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCALE_MARKER = "DH_REMINDER_CAPTURE_SCALED"
STEP_DELAY_MS = 700
FALLBACK_MS = 8000
DEMO_REMINDER = (
    "stand_up",
    "该起来活动一下啦",
    "已经坐了一会儿，起身走动或伸展一下吧。",
    "stand",
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="健康提醒 UI 真实桌面截图")
    parser.add_argument(
        "--out", default=str(ROOT / "screenshots"), help="PNG 输出目录（默认 ./screenshots）"
    )
    parser.add_argument(
        "--scale", type=float, default=None, help="高 DPI 缩放因子，例如 1.25 / 1.5"
    )
    return parser.parse_args(argv)


def _relaunch_for_scale(args: argparse.Namespace) -> int | None:
    """QT_SCALE_FACTOR 必须在 QApplication 创建前设置，所以带 --scale 时重启自身一次。"""
    if not args.scale or os.environ.get(SCALE_MARKER) == "1":
        return None
    os.environ[SCALE_MARKER] = "1"
    os.environ["QT_SCALE_FACTOR"] = f"{args.scale:g}"
    import subprocess

    if args.scale:
        print(f"[capture] 以 QT_SCALE_FACTOR={args.scale:g} 重启自身一次", flush=True)
    return subprocess.call(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--out",
            args.out,
            "--scale",
            f"{args.scale:g}",
        ]
    )


def _build_demo_scheduler():
    """两张卡：久坐 30 分钟（一条待确认）、喝水 40 分钟（倒计时中）。"""
    from src.clock import SystemClock
    from src.models import TaskStatus, default_config_now, utc_now
    from src.scheduler import Scheduler

    now = utc_now()
    config = default_config_now(now)
    config.task("stand_up").interval_minutes = 30
    config.task("drink_water").interval_minutes = 40
    config.task("drink_water").next_due_at = now + timedelta(minutes=27)
    scheduler = Scheduler(config, SystemClock())
    # Scheduler 构造之后再标记待确认，避免被初始化时的 repair 重排掉
    config.task("stand_up").status = TaskStatus.PENDING
    return scheduler


def _grab(app, screen, out_dir: Path, name: str) -> Path:
    app.processEvents()
    pixmap = screen.grabWindow(0)
    target = out_dir / name
    if pixmap.isNull() or not pixmap.save(str(target), "PNG"):
        raise SystemExit(f"[capture] 截图保存失败：{target}")
    print(f"[capture] {target}  {pixmap.width()}x{pixmap.height()}", flush=True)
    return target


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    relaunch_code = _relaunch_for_scale(args)
    if relaunch_code is not None:
        return relaunch_code

    # 必须在 QApplication 之前：真实桌面平台
    os.environ["QT_QPA_PLATFORM"] = "windows"

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from src.models import Reminder
    from src.ui.main_window import MainWindow
    from src.ui.reminder_popup import ReminderPopup

    app = QApplication(sys.argv[:1])
    screen = app.primaryScreen()
    if screen is None:
        print("[capture] 没有可用屏幕，无法截图", flush=True)
        return 2

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"[capture] platform={app.platformName()} screen={screen.name()} "
        f"geometry={screen.geometry().width()}x{screen.geometry().height()} "
        f"dpr={screen.devicePixelRatio():g} out={out_dir}",
        flush=True,
    )

    scheduler = _build_demo_scheduler()
    window = MainWindow(scheduler, version="1.0.0")
    popup = ReminderPopup()
    reminder = Reminder(*DEMO_REMINDER)
    finished = False

    def finish(code: int = 0) -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        popup.allow_app_close()
        popup.close()
        app.exit(code)

    def force_quit() -> None:
        print("[capture] 2000ms 兜底：强制退出", flush=True)
        sys.stdout.flush()
        os._exit(0)

    def shot_popup() -> None:
        window.hide()
        popup.show_reminder(reminder, screen)
        popup.update_clock(_now_text())

        def done() -> None:
            _grab(app, screen, out_dir, "reminder_popup.png")
            finish()

        QTimer.singleShot(STEP_DELAY_MS, done)

    def shot_window() -> None:
        window.show()
        window.raise_()

        def done() -> None:
            _grab(app, screen, out_dir, "main_window.png")
            shot_popup()

        QTimer.singleShot(STEP_DELAY_MS, done)

    QTimer.singleShot(STEP_DELAY_MS, shot_window)
    QTimer.singleShot(FALLBACK_MS, force_quit)
    code = app.exec()
    print(f"[capture] 完成，PNG 位于 {out_dir}", flush=True)
    return code


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    raise SystemExit(main())
