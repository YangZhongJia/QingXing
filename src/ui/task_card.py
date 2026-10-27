"""任务卡片（FR-01/02/03/11/15，PRD 4.1）。

约定：
- 卡片内**没有 QTimer**：倒计时由应用每秒调用 ``refresh()`` 驱动。
- 修改间隔/开关直接走 ``Scheduler.set_interval / set_enabled``（唯一业务真源），
  校验失败只显示红字错误，不改状态、不落盘。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..models import TaskStatus, ValidationError, validate_interval
from . import icon_pixmap, repolish

CARD_ICON_SIZE = 56
INTERVAL_CHIPS: tuple[int, ...] = (15, 20, 30, 40, 45, 60)
DISABLED_OPACITY = 0.55

_STATUS_BADGES: dict[TaskStatus, tuple[str, str]] = {
    TaskStatus.ACTIVE: ("计时中", "active"),
    TaskStatus.PENDING: ("待确认", "pending"),
    TaskStatus.PAUSED: ("已暂停", "paused"),
    TaskStatus.DISABLED: ("已关闭", "disabled"),
}


def format_remaining(seconds: int) -> str:
    """秒 → 倒计时文本：不足 1 小时用 MM:SS，否则 H:MM:SS。"""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class TaskCard(QFrame):
    """单个提醒任务的可编辑卡片。"""

    changed = Signal()
    delete_requested = Signal(str)

    def __init__(self, scheduler, task_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scheduler = scheduler
        self.task_id = task_id
        self.setObjectName("taskCard")
        self.setProperty("cardDisabled", "false")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._build_ui()
        self.refresh()

    # ---------------- 构建 ----------------

    def _build_ui(self) -> None:
        task = self.scheduler.task(self.task_id)
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self.opacity_effect)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(14)

        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("cardIcon")
        self.icon_label.setFixedSize(CARD_ICON_SIZE, CARD_ICON_SIZE)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setProperty("kind", task.icon)
        icon = icon_pixmap(f"{task.icon}.svg", 30)
        if icon.isNull():  # 自定义提醒没有专属图标，回退应用图标
            icon = icon_pixmap("app.ico", 30)
        if not icon.isNull():
            self.icon_label.setPixmap(icon)
        root.addWidget(self.icon_label, 0, Qt.AlignTop)

        center = QVBoxLayout()
        center.setSpacing(6)
        center.setContentsMargins(0, 0, 0, 0)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title_label = QLabel(task.title, self)
        self.title_label.setObjectName("cardTitle")
        self.badge_label = QLabel("", self)
        self.badge_label.setObjectName("badge")
        self.badge_label.setProperty("state", "active")
        head.addWidget(self.title_label, 0, Qt.AlignVCenter)
        head.addWidget(self.badge_label, 0, Qt.AlignVCenter)
        head.addStretch(1)
        center.addLayout(head)

        self.desc_label = QLabel(task.message, self)
        self.desc_label.setObjectName("cardDesc")
        self.desc_label.setWordWrap(True)
        center.addWidget(self.desc_label)

        self.countdown_label = QLabel("", self)
        self.countdown_label.setObjectName("cardCountdown")
        center.addWidget(self.countdown_label)

        editor = QHBoxLayout()
        editor.setSpacing(6)
        self.input = QLineEdit(self)
        self.input.setObjectName("intervalInput")
        self.input.setFixedWidth(56)
        self.input.setMaxLength(3)
        self.input.setAlignment(Qt.AlignCenter)
        self.input.setProperty("invalid", "false")
        self.input.setToolTip("间隔分钟数（1~720 的整数）")
        unit = QLabel("分钟", self)
        unit.setObjectName("cardUnit")
        self.chips: dict[int, QPushButton] = {}
        editor.addWidget(self.input, 0, Qt.AlignVCenter)
        editor.addWidget(unit, 0, Qt.AlignVCenter)
        editor.addSpacing(6)
        for minutes in INTERVAL_CHIPS:
            chip = QPushButton(str(minutes), self)
            chip.setObjectName("chip")
            chip.setCheckable(False)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setFocusPolicy(Qt.NoFocus)
            chip.clicked.connect(lambda _checked=False, value=minutes: self.apply_interval(value))
            self.chips[minutes] = chip
            editor.addWidget(chip, 0, Qt.AlignVCenter)
        editor.addStretch(1)
        self.save_button = QPushButton("保存", self)
        self.save_button.setObjectName("primarySmall")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setFocusPolicy(Qt.NoFocus)
        editor.addWidget(self.save_button, 0, Qt.AlignVCenter)
        center.addLayout(editor)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("cardError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        center.addWidget(self.error_label)

        root.addLayout(center, 1)

        actions = QVBoxLayout()
        actions.setSpacing(6)
        actions.setContentsMargins(0, 0, 0, 0)
        self.enabled_check = QCheckBox("开", self)
        self.enabled_check.setObjectName("enableSwitch")
        self.enabled_check.setCursor(Qt.PointingHandCursor)
        self.enabled_check.setFocusPolicy(Qt.NoFocus)
        actions.addWidget(self.enabled_check, 0, Qt.AlignRight)
        actions.addStretch(1)
        self.delete_button = QPushButton("删除", self)
        self.delete_button.setObjectName("linkButton")
        self.delete_button.setCursor(Qt.PointingHandCursor)
        self.delete_button.setFocusPolicy(Qt.NoFocus)
        self.delete_button.setVisible(self.task_id.startswith("custom_"))
        actions.addWidget(self.delete_button, 0, Qt.AlignRight)
        root.addLayout(actions, 0)

        self.save_button.clicked.connect(self.apply_interval_from_input)
        self.input.returnPressed.connect(self.apply_interval_from_input)
        self.enabled_check.toggled.connect(self._on_enabled_toggled)
        self.delete_button.clicked.connect(lambda: self.delete_requested.emit(self.task_id))

    # ---------------- 对外 API ----------------

    def refresh(self) -> None:
        """从 scheduler 重读并刷新状态/剩余时间/开关/间隔。"""
        task = self.scheduler.task(self.task_id)
        # 刷新期间屏蔽信号，避免程序化赋值把用户操作当成交互
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(task.enabled)
        self.enabled_check.blockSignals(False)
        self.enabled_check.setText("开" if task.enabled else "关")

        # 输入框正在编辑或是校验失败态时不要覆盖用户输入；清错后由下一次 refresh 同步
        if not self.input.hasFocus() and self.input.property("invalid") != "true":
            text = str(task.interval_minutes)
            if self.input.text() != text:
                self.input.setText(text)

        self._apply_badge(task.status)
        self.countdown_label.setText(self._countdown_text(task))
        self._apply_enabled_visual(task.enabled)

    def collect_values(self) -> tuple[str, bool]:
        """返回（间隔输入框原始文本, 启用开关值）；原始文本交给 Scheduler 校验。"""
        return self.input.text(), self.enabled_check.isChecked()

    def show_error(self, msg: str) -> None:
        self.error_label.setText(msg)
        self.error_label.setVisible(True)
        self.input.setProperty("invalid", "true")
        repolish(self.input)

    def clear_error(self) -> None:
        self.error_label.setText("")
        self.error_label.setVisible(False)
        self.input.setProperty("invalid", "false")
        repolish(self.input)

    # ---------------- 用户操作 ----------------

    def apply_interval(self, minutes: object) -> None:
        """chip 点击或外部直接指定间隔：填入输入框并立即保存。"""
        if isinstance(minutes, int):
            self.input.setText(str(minutes))
        try:
            saved = self.scheduler.set_interval(self.task_id, minutes)
        except ValidationError as exc:
            self.show_error(str(exc))
            return
        self.clear_error()
        self.input.setText(str(saved))
        self.refresh()
        self.changed.emit()

    def apply_interval_from_input(self) -> None:
        self.apply_interval(self.input.text())

    def has_unsaved_edit(self) -> bool:
        """输入框里的间隔是否与已保存值不同（PRD 4.2：未保存要有交代）。"""
        task = self.scheduler.config.get_task(self.task_id)
        if task is None:
            return False
        return self.input.text().strip() != str(task.interval_minutes)

    def commit_edit(self) -> str | None:
        """关闭/退出前把未保存的间隔自动保存；返回错误文案（None 表示成功或无改动）。"""
        if not self.has_unsaved_edit():
            self.clear_error()
            return None
        try:
            minutes = validate_interval(self.input.text())
        except ValidationError as exc:
            self.show_error(str(exc))
            return str(exc)
        self.apply_interval(minutes)
        return None

    def _on_enabled_toggled(self, checked: bool) -> None:
        self.scheduler.set_enabled(self.task_id, checked)
        self.refresh()
        self.changed.emit()

    # ---------------- 内部 ----------------

    def _apply_badge(self, status: TaskStatus) -> None:
        text, state = _STATUS_BADGES.get(status, ("未知", "disabled"))
        if self.badge_label.text() != text:
            self.badge_label.setText(text)
        if self.badge_label.property("state") != state:
            self.badge_label.setProperty("state", state)
            repolish(self.badge_label)

    def _countdown_text(self, task) -> str:
        if task.status is TaskStatus.PENDING:
            return "提醒待确认"
        if task.status is TaskStatus.DISABLED:
            return "已关闭，不再提醒"
        remaining = self.scheduler.remaining_seconds_for(self.task_id)
        if remaining is None:
            return "下次提醒 --:--"
        prefix = "已暂停，剩余" if task.status is TaskStatus.PAUSED else "下次提醒"
        return f"{prefix} {format_remaining(remaining)}"

    def _apply_enabled_visual(self, enabled: bool) -> None:
        self.opacity_effect.setOpacity(1.0 if enabled else DISABLED_OPACITY)
        value = "false" if enabled else "true"
        if self.property("cardDisabled") != value:
            self.setProperty("cardDisabled", value)
            repolish(self)
