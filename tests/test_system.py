"""系统集成测试：单实例 IPC（NFR-07）、开机自启（FR-23）、托盘菜单（FR-19/20）。

真实副作用边界：
- 单实例只用本进程创建的命名管道，测试结束一律 release()；
- 开机自启把 RUN_KEY / VALUE_NAME 换到 `Software\\DesktopHealthReminderTest\\Run`，
  不碰用户真实启动项，并在 finally（fixture 收尾）里清理自己建的键；
- 托盘在 offscreen 平台一般不可用，因此菜单结构通过 TrayManager.context_menu()
  直接断言，不依赖系统托盘是否真的可见。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")

from PySide6.QtWidgets import QSystemTrayIcon  # noqa: E402

from src import startup  # noqa: E402
from src.single_instance import INSTANCE_KEY, SingleInstance  # noqa: E402
from src.tray_manager import TrayManager  # noqa: E402

_TEST_KEY_ROOT = r"Software\DesktopHealthReminderTest"
_TEST_RUN_KEY = _TEST_KEY_ROOT + r"\Run"


# ---------------------------------------------------------------- 单实例 ----

def test_single_instance_blocks_second_and_activates_first(qtbot):
    # 用测试专用管道名而不是 INSTANCE_KEY：E2E 联调时真实应用可能正占着默认键，
    # 那会让本用例因环境里已有一个实例而假失败（不是逻辑失败）。
    key = f"{INSTANCE_KEY}.pytest"
    first = SingleInstance(key=key)
    second = SingleInstance(key=key)
    try:
        assert first.try_acquire() is True
        # 先连信号再触发：waitSignal 上下文内第二个实例才发起 IPC 连接。
        with qtbot.waitSignal(first.activate_requested, timeout=3000):
            assert second.try_acquire() is False
    finally:
        second.release()
        first.release()


def test_single_instance_release_allows_reacquire(qtbot):
    key = f"{INSTANCE_KEY}.release"
    first = SingleInstance(key=key)
    try:
        assert first.try_acquire() is True
        first.release()
        again = SingleInstance(key=key)
        try:
            assert again.try_acquire() is True
        finally:
            again.release()
    finally:
        first.release()


# ---------------------------------------------------------------- 开机自启 ----

@pytest.fixture
def isolated_run_key(monkeypatch):
    """把自启读写隔离到测试专用注册表键，退出时清理，不污染用户启动项。"""
    if not startup.available():
        pytest.skip("当前平台没有 winreg，开机自启仅支持 Windows")
    import winreg

    value_name = f"{startup.VALUE_NAME}.pytest"
    monkeypatch.setattr(startup, "RUN_KEY", _TEST_RUN_KEY)
    monkeypatch.setattr(startup, "VALUE_NAME", value_name)

    def cleanup() -> None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _TEST_RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, value_name)
        except OSError:
            pass
        for path in (_TEST_RUN_KEY, _TEST_KEY_ROOT):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except OSError:
                pass

    cleanup()
    yield value_name
    cleanup()


def test_current_command_is_quoted_and_points_to_entry(isolated_run_key):
    command = startup.current_command()
    assert command
    assert command.startswith('"')
    assert "DesktopHealthReminder" in command or "app.py" in command


def test_startup_toggle_roundtrip_writes_hkcu_run(isolated_run_key):
    import winreg

    assert startup.is_enabled() is False

    ok, message = startup.set_enabled(True)
    assert ok is True, message
    assert "已设置" in message
    assert startup.is_enabled() is True

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, startup.RUN_KEY, 0, winreg.KEY_READ
    ) as key:
        stored, kind = winreg.QueryValueEx(key, isolated_run_key)
    assert kind == winreg.REG_SZ
    assert stored == startup.current_command()

    ok, message = startup.set_enabled(False)
    assert ok is True, message
    assert "已取消" in message
    assert startup.is_enabled() is False


def test_startup_apply_only_writes_on_change(isolated_run_key):
    assert startup.apply(False) == (True, "")
    assert startup.is_enabled() is False

    assert startup.apply(True) == (True, "已设置开机自启")
    assert startup.is_enabled() is True
    assert startup.apply(True) == (True, "")

    assert startup.apply(False)[0] is True
    assert startup.is_enabled() is False


# ---------------------------------------------------------------- 托盘 ----

def test_tray_available_and_start_return_bool(qtbot, make_ctx):
    tray = TrayManager(make_ctx().scheduler)
    try:
        assert isinstance(tray.available(), bool)
        started = tray.start()
        assert isinstance(started, bool)
        if started:  # 真实桌面环境下才可能为 True
            assert "健康提醒" in tray.tooltip_text()
        assert tray.context_menu().actions()
    finally:
        tray.stop()


def test_tray_menu_structure_and_pause_toggle(qtbot, make_ctx):
    tray = TrayManager(make_ctx().scheduler)
    try:
        texts = [
            action.text()
            for action in tray.context_menu().actions()
            if not action.isSeparator()
        ]
        assert texts == [
            "打开设置",
            "暂停提醒",
            "测试久坐提醒",
            "测试喝水提醒",
            "退出程序",
        ]

        tray.set_paused(True)
        paused_texts = [
            action.text()
            for action in tray.context_menu().actions()
            if not action.isSeparator()
        ]
        assert paused_texts[1] == "恢复提醒"

        tray.set_paused(False)
        resumed_texts = [
            action.text()
            for action in tray.context_menu().actions()
            if not action.isSeparator()
        ]
        assert resumed_texts[1] == "暂停提醒"
    finally:
        tray.stop()


def test_tray_menu_actions_emit_signals(qtbot, make_ctx):
    tray = TrayManager(make_ctx().scheduler)
    seen: list[str] = []
    tray.open_settings_requested.connect(lambda: seen.append("open"))
    tray.pause_toggle_requested.connect(lambda: seen.append("pause"))
    tray.test_requested.connect(seen.append)
    tray.quit_requested.connect(lambda: seen.append("quit"))
    try:
        for action in tray.context_menu().actions():
            if not action.isSeparator():
                action.trigger()
    finally:
        tray.stop()
    assert seen == ["open", "pause", "stand_up", "drink_water", "quit"]


def test_tray_refresh_tooltip_running_and_paused(qtbot, make_ctx):
    ctx = make_ctx()
    tray = TrayManager(ctx.scheduler)
    try:
        tray.refresh()
        running = tray.tooltip_text()
        assert running.startswith("健康提醒")
        assert "久坐" in running and "喝水" in running
        assert "已暂停" not in running

        ctx.scheduler.pause()
        tray.set_paused(True)
        tray.refresh()
        assert "已暂停" in tray.tooltip_text()

        ctx.scheduler.resume()
        tray.set_paused(False)
        tray.refresh()
        assert "已暂停" not in tray.tooltip_text()

        tray.apply_settings("测试后缀")
        assert tray.tooltip_text().endswith("测试后缀")
    finally:
        tray.stop()


def test_tray_start_false_when_system_tray_unavailable(qtbot, make_ctx, monkeypatch):
    monkeypatch.setattr(
        QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False)
    )
    tray = TrayManager(make_ctx().scheduler)
    try:
        assert tray.available() is False
        assert tray.start() is False
        # 托盘不可用时菜单与一次性提示接口仍然安全
        assert len(tray.context_menu().actions()) >= 5
        assert tray.show_message("仍在后台运行", "程序继续在托盘运行") is None
    finally:
        tray.stop()
