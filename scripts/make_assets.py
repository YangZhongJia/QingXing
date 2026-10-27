"""生成 UI 资源：assets/stand.svg、assets/drink.svg、assets/app.ico。

只依赖 PySide6（不引外部图形库），可重复运行且输出确定（幂等）。

用法：
    python scripts/make_assets.py
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # 纯绘制，不需要真实桌面

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QGuiApplication,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

STAND_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"
     fill="none" stroke="#2B7CD3" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="12" cy="4.6" r="2.1"/>
  <path d="M12 6.9v6.5"/>
  <path d="M12 8.8 8.2 5.8"/>
  <path d="M12 8.8 15.8 5.8"/>
  <path d="M12 13.4 9.2 19.8"/>
  <path d="M12 13.4 14.8 19.8"/>
  <path d="M6.4 20.9h11.2"/>
</svg>
"""

DRINK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"
     fill="none" stroke="#2E9E63" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <path d="M7.4 7.6h9.2l-.9 12.5a1.8 1.8 0 0 1-1.8 1.7h-3.8a1.8 1.8 0 0 1-1.8-1.7z"/>
  <path d="M8.3 11.4h7.4"/>
  <path d="M12 13.6c.9 1.2 1.4 1.8 1.4 2.5a1.4 1.4 0 1 1-2.8 0c0-.7.5-1.3 1.4-2.5z"/>
</svg>
"""

ICO_SIZES = (16, 32, 48, 256)


def _canvas(size: int) -> QImage:
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    return image


def render_app_icon(size: int) -> QImage:
    """蓝底圆角方块 + 白色心电图折线（256 版设计，按比例缩放）。"""
    image = _canvas(size)
    painter = QPainter(image)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    painter.scale(size / 256.0, size / 256.0)

    gradient = QLinearGradient(16, 16, 240, 240)
    gradient.setColorAt(0.0, QColor("#4A9BE8"))
    gradient.setColorAt(1.0, QColor("#2B7CD3"))
    path = QPainterPath()
    path.addRoundedRect(QRectF(8, 8, 240, 240), 56, 56)
    painter.fillPath(path, gradient)

    pen = QPen(QColor("#FFFFFF"), 17)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.drawPolyline(
        [
            _pt(44, 148),
            _pt(86, 148),
            _pt(110, 100),
            _pt(140, 186),
            _pt(162, 128),
            _pt(180, 148),
            _pt(212, 148),
        ]
    )
    painter.end()
    return image


def _pt(x: float, y: float):
    from PySide6.QtCore import QPointF

    return QPointF(x, y)


def _png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("PNG 编码失败")
    data = bytes(buffer.data())
    buffer.close()
    return data


def write_ico(path: Path, sizes: tuple[int, ...] = ICO_SIZES) -> None:
    """手写 ICO 容器：每帧内嵌 PNG（Vista 之后的标准做法）。"""
    images = [render_app_icon(size) for size in sizes]
    payloads = [_png_bytes(image) for image in images]

    header = struct.pack("<HHH", 0, 1, len(images))
    entries = bytearray()
    offset = 6 + 16 * len(images)
    for image, payload in zip(images, payloads):
        width = 0 if image.width() >= 256 else image.width()
        height = 0 if image.height() >= 256 else image.height()
        entries += struct.pack(
            "<BBBBHHII", width, height, 0, 0, 1, 32, len(payload), offset
        )
        offset += len(payload)
    path.write_bytes(header + bytes(entries) + b"".join(payloads))


def main() -> int:
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    ASSETS.mkdir(parents=True, exist_ok=True)

    for name, content in (("stand.svg", STAND_SVG), ("drink.svg", DRINK_SVG)):
        target = ASSETS / name
        target.write_text(content, encoding="utf-8", newline="\n")
        print(f"[assets] {target.relative_to(ROOT)}  {target.stat().st_size} bytes")

    ico = ASSETS / "app.ico"
    write_ico(ico)
    print(f"[assets] {ico.relative_to(ROOT)}  {ico.stat().st_size} bytes  sizes={ICO_SIZES}")

    # 自检：Qt 必须能读回这个 ICO，且包含全部尺寸
    icon = QIcon(str(ico))
    assert not icon.isNull(), "生成的 app.ico 无法被 QIcon 加载"
    available = sorted((s.width() for s in icon.availableSizes()))
    assert available == sorted(ICO_SIZES), f"ICO 尺寸不符：{available}"
    print(f"[assets] QIcon 校验通过，可用尺寸={available}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
