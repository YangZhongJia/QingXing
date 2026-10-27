"""主设置窗口（FR-01/02/03/10/11/15/16/22，PRD 4.1 / 4.2）。

主窗只是状态的展示层：所有业务改动都走注入的 Scheduler，
倒计时由应用每秒调用 ``refresh()`` 驱动，窗口自身不持有业务定时器。
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..models import DEFAULT_SETTINGS, ValidationError
from . import app_icon, icon_pixmap, load_stylesheet, repolish
from .task_card import TaskCard

WINDOW_SIZE = (780, 600)
WINDOW_MIN_SIZE = (700, 530)

SETTING_LABELS: tuple[tuple[str, str], ...] = (
    ("start_with_windows", "登录 Windows 时自动启动"),
    ("close_to_tray", "关闭主窗口时最小化到托盘"),
    ("popup_always_on_top", "提醒浮窗始终置顶"),
    ("sound_enabled", "启用柔和提示音"),
)


class _Toast(QLabel):
    """底部轻提示：无动画，点击可手动关闭。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.setObjectName("toast")
        self.setProperty("kind", "info")
        self.setWordWrap(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setVisible(False)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.setVisible(False)
        event.accept()


class SettingsPanel(QDialog):
    """通用设置面板：勾选后保存，由主窗写入 scheduler.config.settings。"""

    saved = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPanel")
        self.setWindowTitle("设置")
        self.setModal(False)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)

        title = QLabel("通用设置", self)
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        self.checks: dict[str, QCheckBox] = {}
        for key, label in SETTING_LABELS:
            check = QCheckBox(label, self)
            check.setObjectName("panelCheck")
            check.setCursor(Qt.PointingHandCursor)
            self.checks[key] = check
            layout.addWidget(check)

        hint = QLabel("设置保存在本地用户目录，不上传任何数据。", self)
        hint.setObjectName("panelHint")
        layout.addWidget(hint)
        layout.addSpacing(4)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton("保存", self)
        self.save_button.setObjectName("primaryButton")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._on_save)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

    # ---------------- 对外 API ----------------

    def load_from(self, settings: dict) -> None:
        for key, check in self.checks.items():
            check.setChecked(bool(settings.get(key, DEFAULT_SETTINGS.get(key, False))))

    def values(self) -> dict:
        return {key: check.isChecked() for key, check in self.checks.items()}

    def _on_save(self) -> None:
        self.saved.emit(self.values())
        self.accept()


class AddReminderDialog(QDialog):
    """新增提醒面板：标题必填、间隔 1-720 分钟，到点弹强提醒浮窗。

    校验不在对话框里做：提交给主窗（Scheduler.validate_interval 是唯一校验源），
    失败时通过 ``show_error`` 回填错误文案，保持「一条规则、一处实现」。
    """

    submitted = Signal(str, str, str)  # 标题 / 说明 / 间隔原文

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("addPanel")
        self.setWindowTitle("新增提醒")
        self.setModal(False)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)

        title = QLabel("新增提醒", self)
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        hint = QLabel("到点后会弹出最上层的提醒窗口，必须点「我知道了」才会关闭。", self)
        hint.setObjectName("panelHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(4)

        self.title_input = QLineEdit(self)
        self.title_input.setObjectName("formInput")
        self.title_input.setMaxLength(40)
        self.title_input.setPlaceholderText("例如：眼保健操")
        self.message_input = QLineEdit(self)
        self.message_input.setObjectName("formInput")
        self.message_input.setMaxLength(160)
        self.message_input.setPlaceholderText("例如：看看远处，放松眼睛（可不填）")
        self.interval_input = QLineEdit("20", self)
        self.interval_input.setObjectName("formInput")
        self.interval_input.setMaxLength(3)
        self.interval_input.setPlaceholderText("1-720")

        for label_text, field in (
            ("提醒标题", self.title_input),
            ("提醒内容", self.message_input),
            ("间隔（分钟）", self.interval_input),
        ):
            label = QLabel(label_text, self)
            label.setObjectName("formLabel")
            layout.addWidget(label)
            layout.addWidget(field)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("formError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)
        layout.addSpacing(2)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.clicked.connect(self.reject)
        self.create_button = QPushButton("创建提醒", self)
        self.create_button.setObjectName("primaryButton")
        self.create_button.setDefault(True)
        self.create_button.clicked.connect(self._on_submit)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.create_button)
        layout.addLayout(buttons)

        self.interval_input.returnPressed.connect(self._on_submit)

    def reset(self) -> None:
        self.title_input.clear()
        self.message_input.clear()
        self.interval_input.setText("20")
        self.show_error("")

    def show_error(self, text: str) -> None:
        text = (text or "").strip()
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))

    def _on_submit(self) -> None:
        title = self.title_input.text().strip()
        if not title:
            self.show_error("请填写提醒标题。")
            return
        self.show_error("")
        self.submitted.emit(title, self.message_input.text().strip(), self.interval_input.text().strip())


class MainWindow(QMainWindow):
    """唯一的设置主页面。"""

    settings_saved = Signal(dict)
    hidden_to_tray = Signal()
    quit_requested = Signal()

    def __init__(self, scheduler, version: str = "1.0.0", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scheduler = scheduler
        self.version = version
        self._panel: SettingsPanel | None = None
        self._add_dialog: AddReminderDialog | None = None
        self._task_signature: tuple[str, ...] = ()
        self.cards: dict[str, TaskCard] = {}

        self.setWindowTitle("桌面健康提醒")
        self.setWindowIcon(app_icon())
        self.resize(*WINDOW_SIZE)
        self.setMinimumSize(*WINDOW_MIN_SIZE)

        app = QApplication.instance()
        if app is not None and not app.styleSheet():
            app.setStyleSheet(load_stylesheet())

        self._build_ui()
        self.refresh()

    # ---------------- 构建 ----------------

    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("root")
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(14)

        layout.addLayout(self._build_header(root))

        self.cards_host = QWidget(root)
        self.cards_host.setObjectName("cardsHost")
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(16)
        self.cards_layout.addStretch(1)

        self.cards_area = QScrollArea(root)
        self.cards_area.setObjectName("cardsArea")
        self.cards_area.setWidgetResizable(True)
        self.cards_area.setFrameShape(QFrame.NoFrame)
        self.cards_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cards_area.setWidget(self.cards_host)
        layout.addWidget(self.cards_area, 1)

        self.toast = _Toast(root)
        layout.addWidget(self.toast)

        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.setInterval(3000)
        self._toast_timer.timeout.connect(self.toast.hide)

        layout.addLayout(self._build_footer(root))
        self._sync_tasks()

    def _build_header(self, parent: QWidget) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(12)

        icon_label = QLabel(parent)
        icon_label.setFixedSize(44, 44)
        icon_label.setAlignment(Qt.AlignCenter)
        app_pixmap = icon_pixmap("app.ico", 40)
        if not app_pixmap.isNull():
            icon_label.setPixmap(app_pixmap)
        header.addWidget(icon_label, 0, Qt.AlignVCenter)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = QLabel("健康提醒", parent)
        self.title_label.setObjectName("appTitle")
        titles.addWidget(self.title_label)
        header.addLayout(titles)
        header.addStretch(1)

        self.status_badge = QLabel("运行中", parent)
        self.status_badge.setObjectName("statusBadge")
        self.status_badge.setProperty("state", "running")
        header.addWidget(self.status_badge, 0, Qt.AlignVCenter)

        self.pause_button = QPushButton("暂停提醒", parent)
        self.pause_button.setObjectName("secondaryButton")
        self.pause_button.setCursor(Qt.PointingHandCursor)
        self.pause_button.clicked.connect(self._toggle_pause)
        header.addWidget(self.pause_button, 0, Qt.AlignVCenter)

        self.add_button = QPushButton("＋ 新增提醒", parent)
        self.add_button.setObjectName("primarySmall")
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.setToolTip("新增一条自定义提醒，到点会弹出强提醒")
        self.add_button.clicked.connect(self.open_add_dialog)
        header.addWidget(self.add_button, 0, Qt.AlignVCenter)

        self.settings_button = QPushButton("设置", parent)
        self.settings_button.setObjectName("secondaryButton")
        self.settings_button.setCursor(Qt.PointingHandCursor)
        self.settings_button.clicked.connect(self.open_settings)
        header.addWidget(self.settings_button, 0, Qt.AlignVCenter)
        return header
    def _build_footer(self, parent: QWidget) -> QHBoxLayout:
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.version_label = QLabel(f"版本 v{self.version}", parent)
        self.version_label.setObjectName("versionLabel")
        footer.addWidget(self.version_label, 0, Qt.AlignVCenter)
        footer.addStretch(1)
        hint = QLabel("提醒将在桌面右下角弹出", parent)
        hint.setObjectName("versionLabel")
        footer.addWidget(hint, 0, Qt.AlignVCenter)
        return footer

    # ---------------- 对外 API ----------------

    def refresh(self) -> None:
        """每秒由应用调用：刷新卡片与标题栏状态。"""
        self._sync_tasks()
        paused = self.scheduler.is_paused()
        badge_text = "已暂停" if paused else "运行中"
        if self.status_badge.text() != badge_text:
            self.status_badge.setText(badge_text)
        state = "paused" if paused else "running"
        if self.status_badge.property("state") != state:
            self.status_badge.setProperty("state", state)
            repolish(self.status_badge)
        pause_text = "恢复提醒" if paused else "暂停提醒"
        if self.pause_button.text() != pause_text:
            self.pause_button.setText(pause_text)
        for card in self.cards.values():
            card.refresh()

    def show_message(self, text: str, kind: str = "info") -> None:
        """底部轻提示：3 秒自动消失，也可点击手动关闭。"""
        if self.toast.property("kind") != kind:
            self.toast.setProperty("kind", kind)
            repolish(self.toast)
        self.toast.setText(text)
        self.toast.setVisible(True)
        self._toast_timer.start()

    def open_settings(self) -> SettingsPanel:
        """打开居中的设置面板（非模态，不阻塞事件循环）。"""
        if self._panel is None:
            self._panel = SettingsPanel(self)
            self._panel.saved.connect(self._apply_settings)
        self._panel.load_from(self.scheduler.config.settings)
        self._panel.adjustSize()
        center = self.frameGeometry().center()
        size = self._panel.sizeHint()
        self._panel.move(
            center.x() - size.width() // 2,
            center.y() - size.height() // 2,
        )
        self._panel.show()
        self._panel.raise_()
        return self._panel

    def open_add_dialog(self) -> AddReminderDialog:
        """打开「新增提醒」面板（非模态）。"""
        if self._add_dialog is None:
            dialog = AddReminderDialog(self)
            dialog.submitted.connect(self._create_task)
            self._add_dialog = dialog
        dialog = self._add_dialog
        dialog.reset()
        dialog.adjustSize()
        center = self.frameGeometry().center()
        size = dialog.sizeHint()
        dialog.move(center.x() - size.width() // 2, center.y() - size.height() // 2)
        dialog.show()
        dialog.raise_()
        dialog.title_input.setFocus()
        return dialog

    # ---------------- 内部 ----------------

    def _sync_tasks(self) -> None:
        """任务列表变化时增删对应卡片。"""
        signature = tuple(task.id for task in self.scheduler.tasks)
        if signature == self._task_signature:
            return
        self._task_signature = signature

        for task_id in list(self.cards):
            if task_id not in signature:
                card = self.cards.pop(task_id)
                self.cards_layout.removeWidget(card)
                card.deleteLater()

        for index, task in enumerate(self.scheduler.tasks):
            card = self.cards.get(task.id)
            if card is None:
                card = TaskCard(self.scheduler, task.id, self.cards_host)
                card.changed.connect(self.refresh)
                card.delete_requested.connect(self._delete_task)
                self.cards[task.id] = card
            if self.cards_layout.indexOf(card) != index:
                self.cards_layout.removeWidget(card)
                self.cards_layout.insertWidget(index, card)
            card.show()
        self.cards_area.verticalScrollBar().setValue(0)

    def _delete_task(self, task_id: str) -> None:
        task = self.scheduler.config.get_task(task_id)
        if task is None:
            return
        try:
            self.scheduler.remove_task(task_id)
        except RuntimeError as exc:  # SchedulerError
            self.show_message(str(exc), "error")
            return
        self.refresh()
        self.show_message(f"已删除「{task.title}」。")

    def _create_task(self, title: str, message: str, raw_interval: str) -> None:
        try:
            task = self.scheduler.add_task(title, message, raw_interval)
        except ValidationError as exc:
            if self._add_dialog is not None:
                self._add_dialog.show_error(str(exc))
            return
        if self._add_dialog is not None:
            self._add_dialog.accept()
        self.refresh()
        self.show_message(f"已新增「{task.title}」，每 {task.interval_minutes} 分钟提醒。", "success")

    def _toggle_pause(self) -> None:
        if self.scheduler.is_paused():
            self.scheduler.resume()
            self.show_message("已恢复提醒，倒计时继续。", "success")
        else:
            self.scheduler.pause()
            self.show_message("已暂停提醒，倒计时冻结。")
        self.refresh()

    def _apply_settings(self, values: dict) -> None:
        settings = self.scheduler.config.settings
        settings.update({key: bool(value) for key, value in values.items()})
        self.scheduler.commit()
        self.refresh()
        self.settings_saved.emit(dict(settings))
        self.show_message("设置已保存。", "success")

    def _flush_interval_edits(self) -> str | None:
        """保存未提交的间隔；返回首个错误，避免关闭时丢掉无效输入。"""
        for card in self.cards.values():
            error = card.commit_edit()
            if error:
                card.input.setFocus()
                self.show_message(f"间隔修改未保存：{error}", "error")
                return error
        return None

    def closeEvent(self, event) -> None:  # noqa: N802
        """关闭到托盘还是退出，由 settings["close_to_tray"] 决定。"""
        if self._flush_interval_edits():
            event.ignore()
            return
        if self.scheduler.config.settings.get("close_to_tray", True):
            event.ignore()
            self.hidden_to_tray.emit()
            return
        event.accept()
        self.quit_requested.emit()


__all__ = ["MainWindow", "SettingsPanel", "SETTING_LABELS", "WINDOW_SIZE", "WINDOW_MIN_SIZE"]
