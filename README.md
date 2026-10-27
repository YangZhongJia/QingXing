# 轻醒

**轻醒**是一款运行于 Windows 桌面的健康提醒工具。它会按设定间隔提醒你起身活动、补充饮水，也允许你创建自己的提醒。提醒数据只保存在本机，应用不联网、不登录、不上传数据。

## 界面预览

![轻醒主界面：久坐、喝水和自定义提醒卡片](docs/images/main-window.png)

![提醒到时显示的确认弹窗](docs/images/reminder-popup.png)

## 功能

- **久坐与喝水提醒**：默认分别每 30 分钟和 40 分钟提醒，可单独启用、关闭或调整间隔。
- **自定义提醒**：添加标题、内容与提醒间隔；支持 1～720 分钟。自定义提醒也会进入托盘菜单。
- **按顺序弹出**：多条提醒同时到期时逐条显示，确认当前提醒后再显示下一条。
- **明确确认**：提醒以置顶弹窗显示，点击「我知道了」后关闭。
- **暂停与恢复**：全局暂停会冻结倒计时，恢复后继续。
- **托盘运行**：关闭主窗口时可最小化到托盘；可从托盘打开窗口、暂停提醒、测试提醒或退出。
- **Windows 启动选项**：可设置登录 Windows 后自动启动。
- **本地配置保护**：配置以原子方式保存；发现损坏时会备份并恢复默认配置。

## 运行

适用于 Windows 10/11。使用 Python 运行源码：

```powershell
python -m pip install -r requirements.txt
python app.py
```

也可以构建不显示控制台窗口的单文件程序：

```powershell
python -m pip install -r requirements-dev.txt
scripts\build_windows.bat
```

构建结果为 `dist\DesktopHealthReminder.exe`。该程序使用 PySide6 Deploy 与 Nuitka 打包，包含 Qt 运行库和界面资源。

## 使用说明

主界面中可开启或关闭每项提醒、修改间隔、添加自定义提醒，以及暂停或恢复全部提醒。新增的提醒可在其卡片上删除。托盘菜单提供打开主窗口、暂停/恢复、测试各条提醒和退出等操作。

关闭主窗口的行为由「关闭主窗口时最小化到托盘」设置决定。启用后，窗口关闭时应用继续在后台运行；需要完全结束时，请从托盘菜单选择「退出」。

### 调试参数

| 参数 | 说明 |
| --- | --- |
| `--config <路径>` | 使用指定的 JSON 配置文件；默认位于 `%APPDATA%\DesktopHealthReminder\config.json`。 |
| `--shift-minutes N` | 将下次提醒时间整体平移 N 分钟，用于演示。 |
| `--exit-after <秒>` | 指定秒数后自动退出，用于启动检查和自动化。 |

## 开发

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

测试默认使用 Qt offscreen 模式，不会显示真实窗口。应用由 Python 入口 `app.py` 装配，主要模块如下：

| 路径 | 职责 |
| --- | --- |
| `src/models.py` | 提醒、任务与配置模型，输入校验和序列化。 |
| `src/scheduler.py` | 提醒计时、暂停/恢复、到期队列和确认后的重排。 |
| `src/storage.py` | 本地 JSON 配置的原子保存、损坏备份与恢复。 |
| `src/reminder_manager.py` | 提醒弹窗队列、测试提醒和弹窗生命周期。 |
| `src/tray_manager.py` | 系统托盘图标与菜单。 |
| `src/ui/` | 主窗口、提醒卡片、弹窗和 QSS 样式。 |
| `tests/` | 调度、存储、托盘、弹窗及界面测试。 |
| `scripts/` | Windows 打包、资源和界面截图脚本。 |

## 数据与隐私

| 内容 | 默认位置 |
| --- | --- |
| 配置 | `%APPDATA%\DesktopHealthReminder\config.json` |
| 损坏配置备份 | `%APPDATA%\DesktopHealthReminder\config.corrupt-*.json` |
| 日志 | `%APPDATA%\DesktopHealthReminder\logs\app.log` |

应用不访问网络、不使用账号或遥测。开启 Windows 启动后，只会在当前用户的注册表 `Run` 项中写入本应用的启动配置。删除 `%APPDATA%\DesktopHealthReminder` 可清除本地配置和日志。
