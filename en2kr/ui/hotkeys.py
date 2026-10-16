"""Global hotkeys.

The ``keyboard`` library fires callbacks on its own hook thread; v1 touched Qt
widgets directly from there (random crashes). Here callbacks only emit Qt
signals, which Qt delivers on the GUI thread.

Font +/- keys are hooked only while edit mode is on, so '=' and '-' stay
untouched while typing in game chat.

A hotkey made only of modifiers (the translation toggle's default Ctrl+Alt)
can't be an ordinary hotkey: it is the start of Ctrl+Alt+O and of whatever the
game binds to Ctrl or Alt. It fires only on a quick tap instead (see ChordTap).
"""
import logging
import time

from PySide6 import QtCore

log = logging.getLogger(__name__)


def key_label(key):
    """'ctrl+alt+o' -> 'Ctrl+Alt+O', 'f12' -> 'F12'."""
    return "+".join(part.strip().title() if len(part.strip()) > 1 else part.strip().upper()
                    for part in (key or "").split("+"))


class ChordTap:
    """Fires when all keys of a chord go down together and come up again with nothing else pressed.

    groups: one set of scan codes per chord key (left/right variants).
    Holding Ctrl to crouch and then pressing Alt is not a tap (the keys did not
    go down together), nor is Ctrl+Alt+O (another key joined) or a long hold.
    """

    PRESS_WINDOW = 0.4   # every chord key down within this of the first
    HOLD_MAX = 1.0       # and all released within this once the chord was complete
    STALE = 30.0         # forget a key whose release we never saw (e.g. lost to a UAC prompt)

    def __init__(self, groups, on_fire, clock=time.monotonic):
        self.groups = [set(g) for g in groups]
        self.on_fire, self.clock = on_fire, clock
        self.down = {}         # scan code -> time it went down
        self.first = None
        self.armed = None      # time the chord became complete
        self.spoiled = False

    def feed(self, scan, is_down):
        now = self.clock()
        for code, t in list(self.down.items()):
            if now - t > self.STALE:
                del self.down[code]
        if is_down:
            if scan in self.down:
                return  # auto-repeat while held
            if not self.down:
                self.first, self.armed, self.spoiled = now, None, False
            self.down[scan] = now
            if not any(scan in g for g in self.groups):
                self.spoiled = True
            elif self.armed is None and all(self.down.keys() & g for g in self.groups):
                if now - self.first <= self.PRESS_WINDOW:
                    self.armed = now
                else:
                    self.spoiled = True
            return
        self.down.pop(scan, None)
        if self.down:
            return
        fire = self.armed is not None and not self.spoiled and now - self.armed <= self.HOLD_MAX
        self.first = self.armed = None
        self.spoiled = False
        if fire:
            self.on_fire()


class Hotkeys(QtCore.QObject):
    edit_toggled = QtCore.Signal()
    translate_toggled = QtCore.Signal()
    font_up = QtCore.Signal()
    font_down = QtCore.Signal()

    def __init__(self, edit_key="ctrl+alt+o", toggle_key="ctrl+alt"):
        super().__init__()
        self.edit_key, self.toggle_key = edit_key, toggle_key
        self._edit_handle = None
        self._toggle_handle = None   # ("hook" | "hotkey", handle)
        self._font_handles = []
        self.error = None            # edit-mode hotkey
        self.toggle_error = None     # translation toggle

    def start(self):
        self.error = self.toggle_error = None
        try:
            import keyboard
            self._edit_handle = keyboard.add_hotkey(self.edit_key, self.edit_toggled.emit)
        except Exception as e:
            self.error = e
            log.warning("Global hotkey unavailable: %s", e)
        if not self.toggle_key:
            return
        try:
            import keyboard
            parts = [p.strip() for p in self.toggle_key.split("+") if p.strip()]
            if parts and all(keyboard.is_modifier(p) for p in parts):
                tap = ChordTap(keyboard.parse_hotkey(self.toggle_key)[0], self.translate_toggled.emit)
                handle = keyboard.hook(lambda e: tap.feed(e.scan_code, e.event_type == keyboard.KEY_DOWN))
                self._toggle_handle = ("hook", handle)
            else:
                self._toggle_handle = ("hotkey", keyboard.add_hotkey(self.toggle_key, self.translate_toggled.emit))
        except Exception as e:
            self.toggle_error = e
            log.warning("Translation toggle hotkey unavailable: %s", e)

    def set_keys(self, edit_key, toggle_key):
        self.stop()
        self.edit_key, self.toggle_key = edit_key, toggle_key
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
        except Exception:
            return
        self.set_edit_mode(False)
        handles = [("hotkey", self._edit_handle)] if self._edit_handle is not None else []
        if self._toggle_handle:
            handles.append(self._toggle_handle)
        for kind, handle in handles:
            try:
                if kind == "hook":
                    keyboard.unhook(handle)
                else:
                    keyboard.remove_hotkey(handle)
            except Exception:
                pass
        self._edit_handle = self._toggle_handle = None
