"""存储与输入校验测试：覆盖 TC-002/003/004/005/022/023/FR-13。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src import storage
from src.models import (
    AppConfig,
    PendingEntry,
    TaskStatus,
    ValidationError,
    default_config_now,
    validate_interval,
)
START = datetime(2026, 10, 26, 1, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("raw,expected", [(1, 1), (15, 15), (720, 720), ("30", 30), (" 45 ", 45)])
def test_validate_interval_accepts_integers(raw, expected):
    """TC-002/TC-003 边界 1 与 720 允许保存。"""
    assert validate_interval(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [0, -1, 721, "", "  ", "abc", "30.5", 30.5, None, True, False, "60分钟", "1e2", [], "+60"],
)
def test_validate_interval_rejects_invalid(raw):
    """TC-004/TC-005 0、负数、721、空白、小数、字母、单位、布尔均被拒绝。"""
    with pytest.raises(ValidationError):
        validate_interval(raw)


def test_first_run_defaults_two_tasks():
    """FR-01 首次启动自动创建两个开启任务，从当前时间起计完整间隔。"""
    config, warnings = storage.load(path="Z:/definitely/missing/config.json", now=START)
    assert warnings == []
    assert [t.id for t in config.tasks] == ["stand_up", "drink_water"]
    for task, interval in zip(config.tasks, (30, 40)):
        assert task.enabled and task.status is TaskStatus.ACTIVE
        assert task.interval_minutes == interval
        assert task.next_due_at == START + timedelta(minutes=interval)


def test_save_then_load_roundtrip(tmp_path):
    """TC-022 重启后配置与待确认状态完整保留。"""
    path = tmp_path / "config.json"
    config = default_config_now(START)
    config.task("stand_up").status = TaskStatus.PENDING
    config.task("stand_up").next_due_at = None
    config.pending_queue.append(PendingEntry("stand_up", START))
    config.settings["start_with_windows"] = True
    config.task("drink_water").last_ack_at = START

    storage.save(path, config)
    loaded, warnings = storage.load(path, now=START + timedelta(minutes=1))

    assert warnings == []
    assert loaded.task("stand_up").status is TaskStatus.PENDING
    assert loaded.pending_queue[0].task_id == "stand_up"
    assert loaded.settings["start_with_windows"] is True
    assert loaded.task("drink_water").last_ack_at == START
    assert loaded.task("drink_water").interval_minutes == 40


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "config.json"
    storage.save(path, default_config_now(START))
    assert path.exists()
    assert not (tmp_path / "config.json.tmp").exists()
    json.loads(path.read_text(encoding="utf-8"))


def test_corrupt_json_is_backed_up_and_recovered(tmp_path):
    """TC-023 配置文件被破坏：备份损坏文件并回落默认配置，不崩溃。"""
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")

    config, warnings = storage.load(path, now=START)

    assert [t.id for t in config.tasks] == ["stand_up", "drink_water"]
    assert any("损坏" in w for w in warnings)
    backups = list(tmp_path.glob("config.corrupt-*.json"))
    assert len(backups) == 1
    assert "not json" in backups[0].read_text(encoding="utf-8")


def test_future_schema_version_is_backed_up(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"schema_version": 99, "tasks": []}), encoding="utf-8")
    config, warnings = storage.load(path, now=START)
    assert any("版本" in w for w in warnings)
    assert len(config.tasks) == 2
    assert list(tmp_path.glob("config.corrupt-*.json"))


def test_repair_adds_missing_due_time(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "globals": {},
                "tasks": [
                    {"id": "stand_up", "interval_minutes": 30, "enabled": True, "status": "ACTIVE"},
                    {"id": "drink_water", "interval_minutes": 40, "enabled": True, "status": "ACTIVE"},
                ],
            }
        ),
        encoding="utf-8",
    )
    config, warnings = storage.load(path, now=START)
    assert all(task.next_due_at is not None for task in config.tasks)
    assert any("到期时间" in w for w in warnings)


def test_repair_readds_pending_task_missing_from_queue(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tasks": [
                    {"id": "stand_up", "interval_minutes": 30, "enabled": True, "status": "PENDING"},
                    {"id": "drink_water", "interval_minutes": 40, "enabled": True, "status": "ACTIVE",
                     "next_due_at_utc": START.strftime("%Y-%m-%dT%H:%M:%SZ")},
                ],
                "pending_queue": [],
            }
        ),
        encoding="utf-8",
    )
    config, warnings = storage.load(path, now=START)
    assert config.head_id() == "stand_up"
    assert any("队列" in w for w in warnings)


def test_invalid_interval_in_file_falls_back(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"schema_version": 1, "tasks": [{"id": "stand_up", "interval_minutes": 0, "status": "ACTIVE"}]}),
        encoding="utf-8",
    )
    config, warnings = storage.load(path, now=START)
    assert config.task("stand_up").interval_minutes == 30
    assert any("间隔" in w for w in warnings)


def test_disabled_task_status_is_repaired(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"schema_version": 1, "tasks": [{"id": "stand_up", "enabled": False, "status": "ACTIVE"}]}),
        encoding="utf-8",
    )
    config, _ = storage.load(path, now=START)
    assert config.task("stand_up").status is TaskStatus.DISABLED


def test_settings_unknown_keys_are_dropped():
    config = AppConfig.from_dict({"settings": {"start_with_windows": True, "bogus": True}})
    assert config.settings["start_with_windows"] is True
    assert "bogus" not in config.settings
    assert set(config.settings) == {"start_with_windows", "close_to_tray", "popup_always_on_top", "sound_enabled"}
