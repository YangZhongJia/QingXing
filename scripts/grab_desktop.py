"""真实桌面全屏截图（E2E 用，必须在 windows 平台运行，不要 offscreen）。

    python scripts/grab_desktop.py --out docs/screenshots --prefix e2e --delay 0.5

抓取每一块物理屏幕，文件名 `<prefix>_<序号>_<屏幕名>.png`。
多屏场景下提醒浮窗会出现在鼠标所在屏，单抓主屏会漏。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--prefix", default="screen")
    parser.add_argument("--delay", type=float, default=1.0, help="等待秒数，等界面稳定")
    args = parser.parse_args()

    app = QApplication(sys.argv[:1])
    screens = app.screens()
    if not screens:
        print("没有可用屏幕", file=sys.stderr)
        return 2

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    def grab() -> None:
        for index, screen in enumerate(screens):
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", screen.name()) or "screen"
            target = out_dir / f"{args.prefix}_{index}_{safe}.png"
            pixmap = screen.grabWindow(0)
            ok = pixmap.save(str(target), "PNG")
            print(
                f"saved {target} ok={ok} {pixmap.width()}x{pixmap.height()} dpr={screen.devicePixelRatio():g}",
                flush=True,
            )
        app.quit()

    QTimer.singleShot(int(args.delay * 1000), grab)
    QTimer.singleShot(int(args.delay * 1000) + 4000, app.quit)  # 兜底
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
