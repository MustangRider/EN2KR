"""Marshal pipeline callbacks (worker threads) onto the Qt GUI thread."""
from PySide6 import QtCore

from ..pipeline import PipelineEvents


class PipelineBridge(QtCore.QObject):
    status = QtCore.Signal(str, str, str)
    level = QtCore.Signal(float)
    speech = QtCore.Signal(bool)
    stt = QtCore.Signal(object)
    translation = QtCore.Signal(object)
    notice = QtCore.Signal(str)
    download = QtCore.Signal(object, object)
    auto_ban = QtCore.Signal(str)

    def events(self):
        # Signal.emit from another thread is delivered via a queued connection.
        return PipelineEvents(
            status=self.status.emit, level=self.level.emit, speech=self.speech.emit,
            stt=self.stt.emit, translation=self.translation.emit, notice=self.notice.emit,
            download=self.download.emit, auto_ban=self.auto_ban.emit)


class Worker(QtCore.QObject):
    """Run a blocking call on a plain thread and deliver the result as a signal."""
    finished = QtCore.Signal(object, object)  # (result, error)

    def run(self, fn, *args):
        import threading

        def target():
            try:
                self.finished.emit(fn(*args), None)
            except Exception as e:  # reported to the UI
                self.finished.emit(None, e)

        threading.Thread(target=target, daemon=True).start()
