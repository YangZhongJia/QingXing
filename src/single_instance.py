"""单实例守卫（NFR-07 / PRD 5.6 / TC-026）：QLocalServer + QLocalSocket 真 IPC。

设计要点：
- 首个实例监听命名管道；再次启动的实例通过 QLocalSocket 连上它、送出「激活」消息，
  然后自行退出，绝不启动第二套调度器与托盘。
- 在 Windows 上 `QLocalServer.listen()` 对同名管道并不排斥（系统允许同名多实例），
  所以**不能**用 listen() 成败判断是否首实例：必须先做一次 socket 探测。
- 只有探测确认无人应答时，才 `removeServer()` 清理可能的残留管道名（例如上次崩溃残留），
  否则会破坏正在运行的实例。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)

INSTANCE_KEY = "DesktopHealthReminder.v1"

_CONNECT_TIMEOUT_MS = 300
_NOTIFY_ATTEMPTS = 3
_NOTIFY_RETRY_DELAY_MS = 80
_DRAIN_DELAY_MS = 60
_DISCONNECT_TIMEOUT_MS = 100


class SingleInstance(QObject):
    """谁抢到 INSTANCE_KEY，谁就是唯一的调度器实例。"""

    activate_requested = Signal()

    def __init__(self, key: str = INSTANCE_KEY, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._server: QLocalServer | None = None
        self._acquired = False

    def try_acquire(self) -> bool:
        """True=首个实例；False=已有实例存活（内部已尽力通知它显示设置）。"""
        if self._acquired:
            return True
        # 先 socket 探测：连得上说明已有实例在监听，顺带完成了激活通知。
        if self.notify_existing():
            logger.info("检测到已运行实例，本次启动退出：%s", self._key)
            return False
        # 探测确认无人应答，才清理可能残留的管道名，避免被旧名字挡住。
        QLocalServer.removeServer(self._key)
        server = QLocalServer(self)
        server.newConnection.connect(self._on_new_connection)
        if server.listen(self._key):
            self._server = server
            self._acquired = True
            logger.info("单实例服务已启动：%s", self._key)
            return True
        # 极端竞态：清理后仍被别的实例抢注，再通知一次并放弃。
        logger.warning("监听 %s 失败：%s", self._key, server.errorString())
        server.deleteLater()
        self.notify_existing()
        return False

    def notify_existing(self) -> bool:
        """用 QLocalSocket 通知首个实例「显示设置」；连不上则短重试，返回是否送达。"""
        for attempt in range(1, _NOTIFY_ATTEMPTS + 1):
            socket = QLocalSocket()
            socket.connectToServer(self._key)
            if socket.waitForConnected(_CONNECT_TIMEOUT_MS):
                socket.write(b"activate")
                socket.flush()
                socket.waitForBytesWritten(_CONNECT_TIMEOUT_MS)
                socket.disconnectFromServer()
                if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
                    socket.waitForDisconnected(_DISCONNECT_TIMEOUT_MS)
                return True
            socket.abort()
            if attempt < _NOTIFY_ATTEMPTS:
                QThread.msleep(_NOTIFY_RETRY_DELAY_MS)
        return False

    def release(self) -> None:
        """关闭监听并让出实例名；应用退出路径必须调用。"""
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        if self._acquired:
            logger.info("释放单实例：%s", self._key)
        self._acquired = False

    # ---------------- 内部 ----------------

    def _on_new_connection(self) -> None:
        # 收到连接即代表「第二个实例请求显示设置」，payload 不参与业务判断。
        self.activate_requested.emit()
        pending = self._server.nextPendingConnection() if self._server is not None else None
        if pending is None:
            return
        # 客户端数据可能尚未抵达管道；稍后再读空并释放，避免半关闭连接堆积。
        QTimer.singleShot(_DRAIN_DELAY_MS, lambda: self._drain(pending))

    @staticmethod
    def _drain(socket: QLocalSocket) -> None:
        try:
            socket.readAll()
        except RuntimeError:  # C++ 对象已被销毁
            return
        socket.deleteLater()
