"""Global hotkeys.

The ``keyboard`` library fires callbacks on its own hook thread; v1 touched Qt
widgets directly from there (random crashes). Here callbacks only emit Qt
signals, which Qt delivers on the GUI thread.

Font +/- keys are hooked only while edit mode is on, so '=' and '-' stay
untouched while typing in game chat.
"""
import logging

from PySide6 import QtCore

log = logging.getLogger(__name__)


class Hotkeys(QtCore.QObject):
    edit_toggled = QtCore.Signal()
    font_up = QtCore.Signal()
    font_down = QtCore.Signal()

    def __init__(self, edit_key="f12"):
        super().__init__()
        self.edit_key = edit_key
        self._edit_handle = None
        self._font_handles = []
        self.error = None

    def start(self):
        try:
            import keyboard
            self._edit_handle = keyboard.add_hotkey(self.edit_key, self.edit_toggled.emit)
        except Exception as e:
            self.error = e
            log.warning("Global hotkey unavailable: %s", e)

    def set_edit_key(self, key):
        self.stop()
        self.edit_key = key
        self.start()

    def set_edit_mode(self, active):
        try:
            import keyboard
            for h in self._font_handles:
                keyboard.remove_hotkey(h)
            self._font_handles = []
            if active:
                self._font_handles = [keyboard.add_hotkey("=", self.font_up.emit),
                                      keyboard.add_hotkey("-", self.font_down.emit)]
        except Exception as e:
            log.warning("Edit-mode hotkeys unavailable: %s", e)

    def stop(self):
        try:
            import keyboard
            self.set_edit_mode(False)
            if self._edit_handle is not None:
                keyboard.remove_hotkey(self._edit_handle)
                self._edit_handle = None
        except Exception:
            pass
