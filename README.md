<div align="center">

# 轻醒 · QingXing

### 把照顾自己的提醒，轻轻放进工作日常。

**Windows 10/11**　·　**本地运行**　·　**无需账号**

简体中文　|　[English](README.en.md)

<br>

<img src="docs/images/main-window.png" alt="轻醒主界面" width="760">

<br>

<img src="docs/images/reminder-popup.png" alt="轻醒提醒弹窗" width="360">

</div>

## 关于轻醒

轻醒是一款轻量的 Windows 桌面健康提醒工具。它帮助你在长时间工作时记得起身活动、补充饮水，也支持创建适合自己的提醒。所有提醒和设置都保存在本机。

## 功能一览

| 提醒 | 使用方式 |
| --- | --- |
| 久坐活动 | 默认每 30 分钟提醒，可独立开关和调整间隔。 |
| 补充饮水 | 默认每 40 分钟提醒，可独立开关和调整间隔。 |
| 自定义提醒 | 设置标题、内容和 1～720 分钟间隔；可随时删除。 |
| 暂停与恢复 | 暂停时冻结剩余时间，恢复后继续倒计时。 |
| 到期队列 | 同时到期的提醒按顺序逐条显示，确认后继续下一条。 |
| 系统托盘 | 打开窗口、测试提醒、暂停或退出都可以从托盘完成。 |

提醒到时会显示置顶弹窗。点击「我知道了」后，弹窗关闭并安排下一次提醒。关闭主窗口时可选择最小化到托盘，让提醒继续在后台运行。

## 快速开始

### 从源码运行

需要 Windows 10/11 和 Python 环境：

```powershell
python -m pip install -r requirements.txt
python app.py
```

### 构建单文件程序

```powershell
python -m pip install -r requirements-dev.txt
scripts\build_windows.bat
```

程序生成在 `dist\DesktopHealthReminder.exe`。构建使用 PySide6 Deploy 与 Nuitka，生成的客户端不显示控制台窗口，并包含 Qt 运行库和界面资源。

## 开发与测试

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

测试使用 Qt offscreen 模式，不会打开真实窗口。入口和主要代码位置：

| 路径 | 内容 |
| --- | --- |
| `app.py` | 应用入口与组件装配。 |
| `src/scheduler.py` | 倒计时、暂停、到期队列和提醒重排。 |
| `src/storage.py` | 配置原子保存、损坏备份与恢复。 |
| `src/reminder_manager.py` | 提醒队列、弹窗展示和确认流程。 |
| `src/tray_manager.py` | 系统托盘和菜单。 |
| `src/ui/` | 主窗口、提醒卡片、弹窗与样式。 |
| `tests/` | 调度、存储、托盘、弹窗和界面测试。 |

## 本地数据与隐私

轻醒不联网、不使用账号、不收集遥测数据。配置和日志保存在当前 Windows 用户目录：

| 内容 | 路径 |
| --- | --- |
| 设置与提醒 | `%APPDATA%\DesktopHealthReminder\config.json` |
| 损坏配置备份 | `%APPDATA%\DesktopHealthReminder\config.corrupt-*.json` |
| 应用日志 | `%APPDATA%\DesktopHealthReminder\logs\app.log` |

删除 `%APPDATA%\DesktopHealthReminder` 可清除本地设置和日志。启用开机启动时，应用只会在当前用户的注册表 `Run` 项中保存自身启动项。

## 调试参数

| 参数 | 说明 |
| --- | --- |
| `--config <路径>` | 指定 JSON 配置文件；默认使用上述 `%APPDATA%` 路径。 |
| `--shift-minutes N` | 将下一次提醒整体平移 N 分钟，用于演示。 |
| `--exit-after <秒>` | 指定秒数后自动退出，用于启动检查和自动化。 |
