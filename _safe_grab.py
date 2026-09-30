"""把 QQuickView.grabWindow() 换成异步 grabToImage()，消除"同步握手 + Python 场景图
节点"的死锁（本机实测：窗口未响应）。

用法: python -c "import _safe_grab; import runpy; runpy.run_path('_probe_xxx.py', run_name='__main__')"
"""

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickView


def grab_async(view, timeout_ms: int = 8000) -> QImage:
    root = view.rootObject()
    if root is None:
        return QImage()
    result = root.grabToImage()
    loop = QEventLoop()
    result.ready.connect(loop.quit)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return result.image()


def _grab_window_replacement(self, *args, **kwargs) -> QImage:
    return grab_async(self)


QQuickView.grabWindow = _grab_window_replacement  # type: ignore[method-assign]
print("[_safe_grab] QQuickView.grabWindow -> 异步 grabToImage", flush=True)
