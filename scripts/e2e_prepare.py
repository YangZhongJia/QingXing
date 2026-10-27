"""E2E 辅助：准备隔离配置 / 检查持久化结果。

    python scripts/e2e_prepare.py --dir .e2e --mode due-now
    python scripts/e2e_prepare.py --dir .e2e --mode clean
    python scripts/e2e_prepare.py --dir .e2e --mode inspect
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import models, storage  # noqa: E402
from src.clock import SystemClock  # noqa: E402


def prepare(data_dir: Path, due_now: bool) -> int:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "config.json"
    now = SystemClock().now_utc()
    config = models.default_config_now(now)
    config.settings["start_with_windows"] = False
    config.settings["close_to_tray"] = True
    for task in config.tasks:
        task.interval_minutes = 1
        if due_now:
            task.next_due_at = now - timedelta(seconds=30)
            task.status = models.TaskStatus.ACTIVE
        else:
            task.next_due_at = now + timedelta(minutes=1)
    if due_now:
        # 第三条是自定义提醒，用来验证「新增的提醒也能触发强提醒浮窗」
        config.tasks.append(
            models.Task(
                id="custom_1",
                title="眼保健操",
                message="看看远处，放松眼睛。",
                icon="custom",
                interval_minutes=1,
                next_due_at=now - timedelta(seconds=30),
            )
        )
    config.pending_queue.clear()
    config.global_paused = False
    storage.save(path, config)
    print(f"config written: {path}")
    print(json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
    return 0


def inspect(data_dir: Path) -> int:
    path = data_dir / "config.json"
    if not path.exists():
        print(f"missing: {path}", file=sys.stderr)
        return 2
    config, warnings = storage.load(path, SystemClock().now_utc())
    print(json.dumps(config.to_dict(), ensure_ascii=False, indent=2))
    for warning in warnings:
        print(f"WARN {warning}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--mode", choices=["due-now", "clean", "inspect"], required=True)
    args = parser.parse_args()
    data_dir = Path(args.dir)
    if not data_dir.is_absolute():
        data_dir = (ROOT / data_dir).resolve()
    if args.mode == "inspect":
        return inspect(data_dir)
    return prepare(data_dir, due_now=args.mode == "due-now")


if __name__ == "__main__":
    sys.exit(main())
