"""领域模型、输入校验与持久化结构。

本模块不依赖 Qt，可被调度核心与 UI 同时引用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

SCHEMA_VERSION = 1
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 720

_ISO_Z_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_INT_RE = re.compile(r"^-?\d+$")

DEFAULT_SETTINGS: dict[str, bool] = {
    "start_with_windows": False,
    "close_to_tray": True,
    "popup_always_on_top": True,
    "sound_enabled": False,
}

TASK_SPECS: tuple[tuple[str, str, str, str, int], ...] = (
    ("stand_up", "该起来活动一下啦", "已经坐了一会儿，起身走动或伸展一下吧。", "stand", 30),
    ("drink_water", "记得喝点水", "工作辛苦了，喝几口水吧。", "drink", 40),
)


class ValidationError(ValueError):
    """用户输入不合法（间隔、标题等）。"""


class TaskStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime(_ISO_Z_FORMAT)


def from_iso(raw: Any) -> datetime | None:
    """解析 ISO8601；无法解析返回 None（由 repair 兜底）。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_interval(raw: Any) -> int:
    """把用户输入校验为 1~720 的整数分钟，否则抛 ValidationError。

    拒绝：空值、None、bool、小数、字母、带单位字符串、越界值。
    """
    if raw is None or isinstance(raw, bool):
        raise ValidationError(f"请输入 {MIN_INTERVAL_MINUTES}~{MAX_INTERVAL_MINUTES} 的整数分钟")
    if isinstance(raw, int):
        minutes = raw
    elif isinstance(raw, float):
        raise ValidationError("间隔必须是整数分钟，不能带小数")
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValidationError("间隔不能为空")
        if not _INT_RE.match(text):
            raise ValidationError("间隔必须是整数分钟，不能包含字母、小数或单位")
        minutes = int(text)
    else:
        raise ValidationError(f"请输入 {MIN_INTERVAL_MINUTES}~{MAX_INTERVAL_MINUTES} 的整数分钟")

    if minutes < MIN_INTERVAL_MINUTES or minutes > MAX_INTERVAL_MINUTES:
        raise ValidationError(
            f"间隔必须在 {MIN_INTERVAL_MINUTES}~{MAX_INTERVAL_MINUTES} 分钟之间（当前 {minutes}）"
        )
    return minutes


@dataclass
class Task:
    id: str
    title: str
    message: str
    icon: str
    interval_minutes: int
    enabled: bool = True
    status: TaskStatus = TaskStatus.ACTIVE
    next_due_at: datetime | None = None
    remaining_seconds: int | None = None
    last_ack_at: datetime | None = None

    # ---------- 派生信息 ----------

    @property
    def interval_seconds(self) -> int:
        return self.interval_minutes * 60

    def due_in_seconds(self, now: datetime) -> int | None:
        """距离到期还有多少秒；不可计时的状态返回 None。"""
        if self.status is TaskStatus.ACTIVE and self.next_due_at is not None:
            return int((self.next_due_at - now).total_seconds())
        if self.status is TaskStatus.PAUSED and self.remaining_seconds is not None:
            return int(self.remaining_seconds)
        return None

    # ---------- 持久化 ----------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "message": self.message,
            "icon": self.icon,
            "interval_minutes": self.interval_minutes,
            "enabled": self.enabled,
            "status": self.status.value,
            "next_due_at_utc": to_iso(self.next_due_at),
            "remaining_seconds": self.remaining_seconds,
            "last_ack_at_utc": to_iso(self.last_ack_at),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], warnings: list[str] | None = None) -> Task:
        """宽松解析：缺失或非法字段回落默认值并记录警告，绝不抛出。"""
        warnings = warnings if warnings is not None else []
        spec = next((s for s in TASK_SPECS if s[0] == raw.get("id")), None)
        default_interval = spec[4] if spec else 30
        interval_raw = raw.get("interval_minutes", default_interval)
        try:
            interval = validate_interval(interval_raw)
        except ValidationError:
            interval = default_interval
            warnings.append(f"任务 {raw.get('id')} 的间隔非法（{interval_raw!r}），已回退为 {interval} 分钟")
        status_raw = raw.get("status", TaskStatus.ACTIVE.value)
        try:
            status = TaskStatus(str(status_raw))
        except ValueError:
            status = TaskStatus.ACTIVE
            warnings.append(f"任务 {raw.get('id')} 的状态非法（{status_raw!r}），已回退为 ACTIVE")
        task = cls(
            id=str(raw.get("id", "")),
            title=str(raw.get("title") or (spec[1] if spec else "提醒")),
            message=str(raw.get("message") or (spec[2] if spec else "")),
            icon=str(raw.get("icon") or (spec[3] if spec else "stand")),
            interval_minutes=interval,
            enabled=bool(raw.get("enabled", True)),
            status=status,
            next_due_at=from_iso(raw.get("next_due_at_utc")),
            remaining_seconds=(int(raw["remaining_seconds"]) if isinstance(raw.get("remaining_seconds"), (int, float)) else None),
            last_ack_at=from_iso(raw.get("last_ack_at_utc")),
        )
        return task


@dataclass
class PendingEntry:
    """待确认队列元素：按到期时刻排序，同刻按任务 ID 稳定排序。"""

    task_id: str
    due_at: datetime

    def sort_key(self) -> tuple[datetime, str]:
        return (self.due_at, self.task_id)

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "due_at_utc": to_iso(self.due_at)}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PendingEntry | None:
        task_id = raw.get("task_id")
        due_at = from_iso(raw.get("due_at_utc"))
        if not isinstance(task_id, str) or not task_id:
            return None
        return cls(task_id=task_id, due_at=due_at or utc_now())


def default_tasks(now: datetime) -> list[Task]:
    """FR-01：首次启动创建两个开启的任务，从当前时间起计完整间隔。"""
    return [
        Task(
            id=task_id,
            title=title,
            message=message,
            icon=icon,
            interval_minutes=interval,
            enabled=True,
            status=TaskStatus.ACTIVE,
            next_due_at=now + timedelta(minutes=interval),
        )
        for task_id, title, message, icon, interval in TASK_SPECS
    ]


@dataclass
class AppConfig:
    schema_version: int = SCHEMA_VERSION
    global_paused: bool = False
    settings: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    tasks: list[Task] = field(default_factory=list)
    pending_queue: list[PendingEntry] = field(default_factory=list)

    # ---------- 查询 ----------

    def task(self, task_id: str) -> Task:
        for item in self.tasks:
            if item.id == task_id:
                return item
        raise KeyError(task_id)

    def get_task(self, task_id: str) -> Task | None:
        return next((item for item in self.tasks if item.id == task_id), None)

    def queue_sorted(self) -> list[PendingEntry]:
        return sorted(self.pending_queue, key=PendingEntry.sort_key)

    def queue_ids(self) -> list[str]:
        return [entry.task_id for entry in self.queue_sorted()]

    def head_id(self) -> str | None:
        ordered = self.queue_sorted()
        return ordered[0].task_id if ordered else None

    def find_entry(self, task_id: str) -> PendingEntry | None:
        return next((entry for entry in self.pending_queue if entry.task_id == task_id), None)

    # ---------- 修复 ----------

    def repair(self, now: datetime) -> list[str]:
        """把不一致的持久化状态拉回自洽；返回人类可读的修复说明。"""
        warnings: list[str] = []
        if not self.tasks:
            self.tasks = default_tasks(now)
            warnings.append("任务列表为空，已恢复两个默认任务")
        for task in self.tasks:
            if not task.enabled and task.status is not TaskStatus.DISABLED:
                task.status = TaskStatus.DISABLED
                task.next_due_at = None
                task.remaining_seconds = None
            if task.status is TaskStatus.PENDING and self.find_entry(task.id) is None:
                self.pending_queue.append(PendingEntry(task.id, task.next_due_at or now))
                warnings.append(f"任务 {task.id} 处于待确认状态但缺失队列记录，已补回队列")
            if task.status is TaskStatus.ACTIVE and task.next_due_at is None:
                task.next_due_at = now + timedelta(minutes=task.interval_minutes)
                warnings.append(f"任务 {task.id} 缺少到期时间，已按当前时间重算")
            if task.status is TaskStatus.PAUSED and not task.remaining_seconds:
                task.remaining_seconds = task.interval_seconds
            if task.status is TaskStatus.PENDING:
                task.next_due_at = None
            if task.status is TaskStatus.DISABLED:
                task.next_due_at = None
                task.remaining_seconds = None
        known = {task.id for task in self.tasks}
        dropped = [entry for entry in self.pending_queue if entry.task_id not in known]
        if dropped:
            self.pending_queue = [entry for entry in self.pending_queue if entry.task_id in known]
            warnings.append("队列中存在未知任务，已清理：" + ", ".join(e.task_id for e in dropped))
        for entry in self.pending_queue:
            task = self.get_task(entry.task_id)
            if task is not None and task.status is not TaskStatus.PENDING and task.enabled:
                task.status = TaskStatus.PENDING
                task.next_due_at = None
        self.pending_queue.sort(key=PendingEntry.sort_key)
        return warnings

    def copy_settings_defaults(self) -> None:
        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: bool(v) for k, v in self.settings.items() if k in DEFAULT_SETTINGS})
        self.settings = merged

    # ---------- 持久化 ----------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "global_paused": self.global_paused,
            "settings": dict(self.settings),
            "tasks": [task.to_dict() for task in self.tasks],
            "pending_queue": [entry.to_dict() for entry in self.queue_sorted()],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], warnings: list[str] | None = None) -> AppConfig:
        warnings = warnings if warnings is not None else []
        tasks_raw = raw.get("tasks")
        tasks = [Task.from_dict(item, warnings) for item in tasks_raw if isinstance(item, dict)] if isinstance(tasks_raw, list) else []
        queue_raw = raw.get("pending_queue")
        queue: list[PendingEntry] = []
        if isinstance(queue_raw, list):
            for item in queue_raw:
                if isinstance(item, dict):
                    entry = PendingEntry.from_dict(item)
                    if entry is not None:
                        queue.append(entry)
        settings_raw = raw.get("settings")
        config = cls(
            schema_version=int(raw.get("schema_version", SCHEMA_VERSION)),
            global_paused=bool(raw.get("global_paused", False)),
            settings={k: bool(v) for k, v in settings_raw.items() if k in DEFAULT_SETTINGS} if isinstance(settings_raw, dict) else dict(DEFAULT_SETTINGS),
            tasks=tasks,
            pending_queue=queue,
        )
        config.copy_settings_defaults()
        return config


def default_config_now(now: datetime | None = None) -> AppConfig:
    """全新配置：两个任务开启、从 now 起计完整间隔。"""
    moment = now or utc_now()
    return AppConfig(tasks=default_tasks(moment))


@dataclass(frozen=True)
class Reminder:
    """交给浮窗展示的一条提醒（测试提醒也用它，但不入队、不落盘）。"""

    task_id: str
    title: str
    message: str
    icon: str
    is_test: bool = False
