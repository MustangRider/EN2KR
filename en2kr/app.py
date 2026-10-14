"""Application entry point."""
import ctypes
import logging
import os
import sys

from . import APP_NAME, __version__, gpu, log, paths, stt
from .settings import Settings

logger = logging.getLogger(__name__)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if any(a.startswith("--selftest") for a in argv):
        from . import selftest
        return selftest.main(argv)

    log.setup()
    logger.info("Starting %s %s (frozen=%s)", APP_NAME, __version__, paths.FROZEN)
    gpu.setup_cuda_dll_paths()
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
    except (AttributeError, OSError):
        pass

    from PySide6 import QtCore, QtGui, QtWidgets
    from .ui.main_window import MainWindow
    from .ui.theme import STYLE

    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QtWidgets.QApplication([sys.argv[0]])
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(STYLE)
    translator = QtCore.QTranslator(app)
    if translator.load("qtbase_ko", QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    if os.path.exists(paths.ICON_PATH):
        app.setWindowIcon(QtGui.QIcon(paths.ICON_PATH))

    lock = QtCore.QLockFile(os.path.join(paths.app_dir(), "instance.lock"))
    if not lock.tryLock(200):
        QtWidgets.QMessageBox.information(None, APP_NAME, "이미 실행 중입니다.")
        return 1

    settings = Settings().load()
    stt.load_stats()
    win = MainWindow(settings)
    win.show()
    win.start()
    rc = app.exec()
    lock.unlock()
    logger.info("Exit %s", rc)
    return rc
