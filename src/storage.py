"""JSON 配置存储：原子写入、损坏自恢复、结构修复。"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from .models import SCHEMA_VERSION, AppConfig, default_config_now, utc_now

APP_DIR_NAME = "DesktopHealthReminder"
CONFIG_FILE_NAME = "config.json"


def default_config_dir() -> Path:
    base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    return Path(base) / APP_DIR_NAME


def default_config_path() -> Path:
    return default_config_dir() / CONFIG_FILE_NAME


def default_log_dir() -> Path:
    return default_config_dir() / "logs"


def load(path: Path | str | None = None, now: datetime | None = None) -> tuple[AppConfig, list[str]]:
    """读取配置；任何异常都回落为默认配置并给出可展示的警告。"""
    path = Path(path) if path is not None else default_config_path()
    now = now or utc_now()
    warnings: list[str] = []
    if not path.exists():
        return default_config_now(now), warnings
    try:
        raw_text = path.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
        if not isinstance(raw, dict):
            raise ValueError("配置根节点不是对象")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        backup = _backup_broken(path)
        warnings.append(f"配置文件损坏（{exc.__class__.__name__}），已备份为 {backup.name} 并恢复默认设置")
        return default_config_now(now), warnings

    version = raw.get("schema_version")
    if isinstance(version, int) and version > SCHEMA_VERSION:
        backup = _backup_broken(path)
        warnings.append(f"配置版本 {version} 高于本程序支持的 {SCHEMA_VERSION}，已备份为 {backup.name} 并恢复默认设置")
        return default_config_now(now), warnings

    config = AppConfig.from_dict(raw, warnings)
    warnings.extend(config.repair(now))
    return config, warnings


def save(path: Path | str | None, config: AppConfig) -> None:
    """原子写入：同目录临时文件 + fsync + os.replace。"""
    path = Path(path) if path is not None else default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def _backup_broken(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"config.corrupt-{stamp}.json")
    try:
        shutil.copy2(path, backup)
    except OSError:
        pass
    return backup
