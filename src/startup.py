"""开机自启（FR-23 / PRD 5.6）：只写当前用户 HKCU 的 Run 键，不需要管理员权限。

- 冻结态（PyInstaller onedir）：写 `"<sys.executable>"`，由打包后的
  DesktopHealthReminder.exe 自己启动；不绑定开发机的 Python 路径。
- 源码态（开发/测试）：写 `"<pythonw.exe>" "<项目根>/app.py"`，用 pythonw 避免黑框。
- 路径一律加引号，含空格的安装目录也能被 Windows 正确解析。
- 绝不写 HKLM；非 Windows 平台导入本模块不报错，由 available() 报不可用。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DesktopHealthReminder"

try:  # winreg 仅 Windows 提供；非 Windows 下模块仍可导入
    import winreg
except ImportError:  # pragma: no cover - 非 Windows
    winreg = None  # type: ignore[assignment]


def available() -> bool:
    """当前平台是否支持注册表开机自启。"""
    return winreg is not None


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def current_command() -> str:
    """自启命令行：可执行文件加引号，源码态带 app.py 绝对路径。"""
    if getattr(sys, "frozen", False):  # PyInstaller 打包态
        return f'"{sys.executable}"'
    interpreter = Path(sys.executable)
    pythonw = interpreter.with_name("pythonw.exe")
    if pythonw.exists():
        interpreter = pythonw
    return f'"{interpreter}" "{_project_root() / "app.py"}"'


def _read_command() -> str | None:
    """读取当前注册表登记的命令；不存在或不可读返回 None。"""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return None
    text = str(value).strip()
    return text or None


def is_enabled() -> bool:
    return _read_command() is not None


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """写入/删除 HKCU Run 值；返回 (是否成功, 中文消息)。"""
    if winreg is None:
        return False, "仅支持 Windows 开机自启"
    if enabled:
        command = current_command()
        try:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
        except OSError as exc:
            logger.warning("写入开机自启失败：%s", exc)
            return False, f"写入注册表失败：{exc}"
        logger.info("已设置开机自启：%s", command)
        return True, "已设置开机自启"
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return True, "已取消开机自启"
    except OSError as exc:
        logger.warning("取消开机自启失败：%s", exc)
        return False, f"取消开机自启失败：{exc}"
    logger.info("已取消开机自启")
    return True, "已取消开机自启"


def apply(desired: bool) -> tuple[bool, str]:
    """按设置同步注册表：状态一致时不写盘，返回 (True, "")。

    已启用但登记的命令指向旧路径（例如从源码态切到打包态）时按不一致处理，重写。
    """
    if winreg is None:
        return False, "仅支持 Windows 开机自启"
    command = _read_command()
    if not desired:
        if command is None:
            return True, ""
    elif command == current_command():
        return True, ""
    return set_enabled(desired)
