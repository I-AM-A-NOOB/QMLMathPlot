"""Keyboard/pointer policy for a camera: focus-gated key bindings and their matching.

Kept out of :class:`~qmlmathplot.camera.Camera`, which stays geometry + ticks. The view reads
``plot.camera.input``; a host can rebind the commands without touching the camera model.

The properties are Qt-style camelCase (like ``Camera.zoomStep``); the method is snake_case,
matching the rest of the QML-facing Python API.
"""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Qt, Signal, Slot
from PySide6.QtGui import QKeySequence

__all__ = ["ViewInput"]


def _shortcut_property(storage: str, notify: Signal) -> Property:
    """A shortcut string property backed by ``storage`` (one notify for the whole group).

    The notify signal is passed as an *object*: PySide silently drops a notify signal given
    by name, and QML bindings then never refresh (AGENTS item 30).
    """
    def getter(self: ViewInput) -> str:
        return getattr(self, storage)

    def setter(self: ViewInput, value: object) -> None:
        text = str(value)
        if text == getattr(self, storage):
            return
        setattr(self, storage, text)
        notify.__get__(self, type(self)).emit()

    return Property(str, getter, setter, notify=notify)


class ViewInput(QObject):
    """Key bindings for the six view commands, plus the keyboard switch and pan step."""

    keyboardEnabledChanged = Signal()
    keyPanStepChanged = Signal()
    keyPanRepeatIntervalChanged = Signal()
    shortcutsChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._keyboard_enabled = True
        self._key_pan_step = 0.1
        self._key_pan_repeat_interval = 100
        self._pan_left = "Left"
        self._pan_right = "Right"
        self._pan_up = "Up"
        self._pan_down = "Down"
        self._zoom_in = "+|="
        self._zoom_out = "-|_"

    # ------------------------------------------------------------- switches
    def _get_keyboard_enabled(self) -> bool:
        return self._keyboard_enabled

    def _set_keyboard_enabled(self, value: bool) -> None:
        value = bool(value)
        if value == self._keyboard_enabled:
            return
        self._keyboard_enabled = value
        self.keyboardEnabledChanged.emit()

    #: Whether the view reacts to the keyboard while it has focus.
    keyboardEnabled: bool = Property(
        bool, _get_keyboard_enabled, _set_keyboard_enabled, notify=keyboardEnabledChanged
    )

    def _get_key_pan_step(self) -> float:
        return self._key_pan_step

    def _set_key_pan_step(self, value: float) -> None:
        value = float(value)
        if value <= 0.0:
            raise ValueError(f"keyPanStep must be positive, got {value!r}")
        if value == self._key_pan_step:
            return
        self._key_pan_step = value
        self.keyPanStepChanged.emit()

    #: Fraction of the viewport moved by one arrow-key press (0.1 = 10%).
    keyPanStep: float = Property(
        float, _get_key_pan_step, _set_key_pan_step, notify=keyPanStepChanged
    )

    def _get_key_pan_repeat_interval(self) -> int:
        return self._key_pan_repeat_interval

    def _set_key_pan_repeat_interval(self, value: int) -> None:
        value = int(value)
        if value <= 0:
            raise ValueError(f"keyPanRepeatInterval must be positive, got {value!r}")
        if value == self._key_pan_repeat_interval:
            return
        self._key_pan_repeat_interval = value
        self.keyPanRepeatIntervalChanged.emit()

    #: Milliseconds per held-key step (the frame timer integrates at dt / this value).
    keyPanRepeatInterval: int = Property(
        int,
        _get_key_pan_repeat_interval,
        _set_key_pan_repeat_interval,
        notify=keyPanRepeatIntervalChanged,
    )

    #: Key sequences (QKeySequence text) for the six view commands. ``|`` separates
    #: alternatives, so the default zoom-in is ``"+|="`` (Shift+= and the unshifted key).
    panLeftShortcut: str = _shortcut_property("_pan_left", shortcutsChanged)
    panRightShortcut: str = _shortcut_property("_pan_right", shortcutsChanged)
    panUpShortcut: str = _shortcut_property("_pan_up", shortcutsChanged)
    panDownShortcut: str = _shortcut_property("_pan_down", shortcutsChanged)
    zoomInShortcut: str = _shortcut_property("_zoom_in", shortcutsChanged)
    zoomOutShortcut: str = _shortcut_property("_zoom_out", shortcutsChanged)

    # ------------------------------------------------------------- matching
    @Slot(int, int, str, result=bool)
    def matches(self, key: int, modifiers: int, shortcut: str) -> bool:
        """Whether a key event matches one of the ``|``-separated sequences in ``shortcut``
        (QKeySequence text). Empty or unparsable sequences never match."""
        if not shortcut or key <= 0:
            return False
        event_mods = int(modifiers) & ~Qt.KeyboardModifier.KeypadModifier.value
        for part in shortcut.split("|"):
            sequence = QKeySequence(part.strip())
            if sequence.count() == 0:
                continue
            combination = sequence[0]
            if combination.key().value != int(key):
                continue
            target_mods = (
                combination.keyboardModifiers().value
                & ~Qt.KeyboardModifier.KeypadModifier.value
            )
            if target_mods == event_mods:
                return True
            # QKeySequence("+") is Key_Plus with no modifier, while a real keyboard reports
            # Shift+Plus; letters are not forgiving (Shift+A must stay Shift+A).
            if (target_mods == 0
                    and event_mods == Qt.KeyboardModifier.ShiftModifier.value
                    and not (Qt.Key.Key_A.value <= int(key) <= Qt.Key.Key_Z.value)):
                return True
        return False
