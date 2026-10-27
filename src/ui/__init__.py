"""UI 层公共工具：资源路径、样式表、图标位图与属性刷新。"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

#: 打包后 PyInstaller 把数据解到 sys._MEIPASS；开发态是项目根（src/ui/__init__.py 上两级）。
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
_UI_DIR = Path(__file__).resolve().parent
_ICON_SUPER_SAMPLE = 2.0


def asset_path(name: str) -> str:
    """返回 assets/ 下资源的绝对路径字符串。"""
    return str(BASE_DIR / "assets" / name)


def stylesheet_path() -> Path:
    """style.qss 的位置；打包后可能位于 _MEIPASS/src/ui。"""
    for candidate in (_UI_DIR / "style.qss", BASE_DIR / "src" / "ui" / "style.qss"):
        if candidate.exists():
            return candidate
    return _UI_DIR / "style.qss"


def load_stylesheet() -> str:
    """读取全局 QSS；文件缺失时返回空串（UI 不应因此崩溃）。"""
    try:
        return stylesheet_path().read_text(encoding="utf-8")
    except OSError:
        return ""


def icon_pixmap(name: str, size: int) -> QPixmap:
    """按逻辑像素取图标位图；SVG 以 2x 渲染，保证 125%/150% DPI 下清晰。"""
    path = asset_path(name)
    if path.lower().endswith(".svg"):
        renderer = QSvgRenderer(path)
        if not renderer.isValid():
            return QPixmap()
        physical = int(round(size * _ICON_SUPER_SAMPLE))
        pixmap = QPixmap(physical, physical)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        renderer.render(painter, QRectF(0, 0, physical, physical))
        painter.end()
        pixmap.setDevicePixelRatio(_ICON_SUPER_SAMPLE)
        return pixmap
    icon = QIcon(path)
    return QPixmap() if icon.isNull() else icon.pixmap(size, size)


def app_icon() -> QIcon:
    return QIcon(asset_path("app.ico"))


def repolish(widget: QWidget) -> None:
    """动态属性变化后重新应用 QSS（Qt 不会自动重算）。"""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()
