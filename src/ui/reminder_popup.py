"""右下角持久提醒浮窗（FR-05/06，PRD 3.3 / 5.5）。

硬性行为（不可用系统 Toast 替代）：
- 非模态、无标题栏、无关闭按钮，只有唯一的「我知道了」。
- Esc / Alt+F4 / 失焦 一律不关闭；只有应用落盘后调 ``hide_after_ack()``
  或明确退出时调 ``allow_app_close()``，close 才生效。
- 不抢键盘焦点（``Qt.WindowDoesNotAcceptFocus`` + ``WA_ShowWithoutActivating``）。
- 位置由传入的可用区计算，多屏/DPI/侧边任务栏都不会越界。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models import Reminder
from . import icon_pixmap

POPUP_WIDTH = 360
POPUP_MARGIN = 16
POPUP_MIN_HEIGHT = 150
POPUP_MAX_HEIGHT = 190

_ICON_SIZE = 34
_SHADOW_INSET = 6  # 自绘柔和阴影占用的一圈内边距，保证阴影不被窗口边界裁掉
_RADIUS = 14

_POPUP_QSS = """
#popupBody {
    background: #FFFFFF;
    border: 1px solid #E1E7EF;
    border-radius: 14px;
}
#popupTitle {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 17px;
    font-weight: 600;
    color: #16202C;
}
#popupMessage {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
    color: #555555;
}
#popupClock {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 11px;
    color: #999999;
}
#ackButton {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
    font-weight: 600;
    color: #FFFFFF;
    background: #2B7CD3;
    border: none;
    border-radius: 8px;
    padding: 8px 20px;
}
#ackButton:hover {
    background: #2269B5;
}
#ackButton:pressed {
    background: #1B5698;
}
#ackButton:disabled {
    background: #A9C3E2;
}
"""


def compute_position(available_rect: QRect, size: QSize, margin: int = POPUP_MARGIN) -> QPoint:
    """可用区域右下角内缩 margin。

    Qt 的 QRect 右下角是闭区间（right = x + width - 1），故 +1；左上不越界。
    """
    x = available_rect.right() - margin - size.width() + 1
    y = available_rect.bottom() - margin - size.height() + 1
    x = max(x, available_rect.left())
    y = max(y, available_rect.top())
    return QPoint(x, y)


class ReminderPopup(QWidget):
    """必须由用户点「我知道了」才消失的提醒卡片。"""

    ack_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current: Reminder | None = None
        self._closable = False
        self._ack_sent = False
        self._build_ui()
        self._apply_window_flags()

    # ---------------- 构建 ----------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(_SHADOW_INSET, _SHADOW_INSET, _SHADOW_INSET, _SHADOW_INSET)
        outer.setSpacing(0)

        self.body = QFrame(self)
        self.body.setObjectName("popupBody")
        outer.addWidget(self.body)

        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.icon_label = QLabel(self.body)
        self.icon_label.setObjectName("popupIcon")
        self.icon_label.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.title_label = QLabel("", self.body)
        self.title_label.setObjectName("popupTitle")
        head.addWidget(self.icon_label, 0, Qt.AlignVCenter)
        head.addWidget(self.title_label, 1, Qt.AlignVCenter)
        layout.addLayout(head)

        self.message_label = QLabel("", self.body)
        self.message_label.setObjectName("popupMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(self.message_label, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.time_label = QLabel("", self.body)
        self.time_label.setObjectName("popupClock")
        self.time_label.setMinimumWidth(96)
        self.ack_button = QPushButton("我知道了", self.body)
        self.ack_button.setObjectName("ackButton")
        self.ack_button.setCursor(Qt.PointingHandCursor)
        self.ack_button.setFocusPolicy(Qt.NoFocus)
        foot.addWidget(self.time_label, 0, Qt.AlignVCenter)
        foot.addStretch(1)
        foot.addWidget(self.ack_button, 0, Qt.AlignVCenter)
        layout.addLayout(foot)

        self.ack_button.clicked.connect(self._on_ack_clicked)
        self.setStyleSheet(_POPUP_QSS)
        self.setFocusPolicy(Qt.NoFocus)

    def _apply_window_flags(self) -> None:
        """工具窗口 + 无边框 + 置顶 + 不抢焦点（PRD 3.3 / 5.5）。"""
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    # ---------------- 对外 API ----------------

    def show_reminder(self, reminder: Reminder, screen=None) -> None:
        """展示一条提醒；同一时刻只展示一条，重复调用即替换内容。"""
        self._current = reminder
        self._ack_sent = False
        self._closable = False
        self.ack_button.setEnabled(True)
        self.ack_button.show()

        self.title_label.setText(reminder.title)
        self.message_label.setText(reminder.message)
        icon = icon_pixmap(f"{reminder.icon}.svg", _ICON_SIZE)
        if icon.isNull():
            icon = icon_pixmap("app.ico", _ICON_SIZE)
        self.icon_label.setPixmap(icon)

        self._apply_size()
        target = screen or QApplication.primaryScreen()
        if target is not None:
            self.move(compute_position(target.availableGeometry(), self.size()))
        self.show()
        self.raise_()

    def update_clock(self, text: str) -> None:
        """外部每秒喂时间文本；只改文字，不触发布局抖动。"""
        if self.time_label.text() != text:
            self.time_label.setText(text)

    def hide_after_ack(self) -> None:
        """应用已把确认状态落盘后调用：从此允许窗口真正关闭。"""
        self._closable = True
        self.close()

    def allow_app_close(self) -> None:
        """仅「退出程序」时调用；之后的 close 不再被拦截。"""
        self._closable = True

    def set_always_on_top(self, on: bool) -> None:
        flags = self.windowFlags()
        if on:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        if was_visible:
            # setWindowFlags 会隐藏窗口，这里恢复显示并保持不激活
            self.show()
            self.raise_()

    def current_task_id(self) -> str | None:
        return self._current.task_id if self._current is not None else None

    # ---------------- 尺寸与绘制 ----------------

    def _apply_size(self) -> None:
        """固定 360 宽，按内容自适应高度并 clamp 到 150~190。"""
        self.setFixedWidth(POPUP_WIDTH)
        layout = self.layout()
        if layout is not None:
            layout.activate()
        self.adjustSize()
        # ponytail: 超长正文按 PRD 的 190 上限截断，PRD 正文均为短句，够用
        height = min(max(self.height(), POPUP_MIN_HEIGHT), POPUP_MAX_HEIGHT)
        self.resize(POPUP_WIDTH, height)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """自绘柔和阴影；圆角白卡片本身由 QSS 绘制。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        body = self.rect().adjusted(_SHADOW_INSET, _SHADOW_INSET, -_SHADOW_INSET, -_SHADOW_INSET)
        for offset, alpha in ((0, 16), (2, 10), (4, 6)):
            rect = QRectF(body.adjusted(-offset, -offset + 1, offset, offset + 2))
            path = QPainterPath()
            path.addRoundedRect(rect, _RADIUS + offset, _RADIUS + offset)
            painter.setBrush(QColor(15, 23, 42, alpha))
            painter.drawPath(path)
        painter.end()

    # ---------------- 关闭拦截 ----------------

    def closeEvent(self, event) -> None:  # noqa: N802
        """普通关闭（X / Alt+F4 / 系统 close）一律忽略，只有已放行才生效。"""
        if not self._closable:
            event.ignore()
            return
        self._closable = False
        self._ack_sent = False
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Esc 及其它按键都不算确认，也不关闭窗口。"""
        event.ignore()

    def changeEvent(self, event) -> None:  # noqa: N802
        # 失焦（WindowDeactivate）刻意不处理：PRD 3.3 要求切换程序时提醒仍在
        super().changeEvent(event)

    def _on_ack_clicked(self) -> None:
        if self._current is None or self._ack_sent:
            return
        self._ack_sent = True
        # 先禁用按钮，防止重复点击发第二次；窗口本身等应用落盘后再关
        self.ack_button.setEnabled(False)
        self.ack_requested.emit(self._current.task_id)
