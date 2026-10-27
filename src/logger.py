"""日志：轮转文件 + 全局异常捕获。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .storage import default_log_dir

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_configured = False


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> Path:
    """初始化文件日志，返回日志文件路径；重复调用无副作用。"""
    global _configured
    directory = Path(log_dir) if log_dir is not None else default_log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    log_file = directory / "app.log"
    root = logging.getLogger()
    if not _configured:
        root.setLevel(level)
        handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(handler)
        if sys.stderr is not None:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(logging.Formatter(LOG_FORMAT))
            root.addHandler(stream)
        _configured = True
    return log_file


def install_excepthook() -> None:
    """把未捕获异常写进日志，避免打包后无控制台时静默崩溃。"""
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        logging.getLogger("uncaught").critical(
            "未捕获异常", exc_info=(exc_type, exc_value, exc_tb)
        )
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
