"""Model-driven annotation elements: text/annotate labels and horizontal/vertical guides.

They live in the model (not in a host overlay) so the same QML scene renders them in the live
view and in the offscreen export. Positions are world coordinates for the anchor and logical
pixels for the label offset.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QModelIndex,
    QObject,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QVector2D

__all__ = ["Annotation", "AnnotationListModel", "Guide", "GuideListModel"]


def _pair(value: object) -> tuple[float, float]:
    if isinstance(value, QVector2D):
        return (value.x(), value.y())
    try:
        x, y = value  # type: ignore[misc]
    except (TypeError, ValueError):
        raise ValueError(f"expected a (x, y) pair, got {value!r}") from None
    return (float(x), float(y))


def _colour(value: object, fallback: str = "#303030") -> QColor:
    return QColor(fallback if value is None else value)


class Annotation(QObject):
    """One text label, optionally with an arrow from the label to a world-coordinate anchor."""

    changed = Signal()

    def __init__(
        self,
        text: str,
        xy: object = (0.0, 0.0),
        xytext: object = (0.0, 0.0),
        *,
        arrow: bool = False,
        color: object = None,
        font_size: float = 12.0,
        visible: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._text = str(text)
        self._xy = QVector2D(*_pair(xy))
        self._xytext = QVector2D(*_pair(xytext))
        self._arrow = bool(arrow)
        self._color = _colour(color)
        self._font_size = float(font_size)
        self._visible = bool(visible)

    def _emit(self) -> None:
        self.changed.emit()

    def _get_text(self) -> str:
        return self._text

    def _set_text(self, value: object) -> None:
        text = str(value)
        if text != self._text:
            self._text = text
            self._emit()

    text: str = Property(str, _get_text, _set_text, notify=changed)

    def _get_xy(self) -> QVector2D:
        return QVector2D(self._xy)

    def _set_xy(self, value: object) -> None:
        point = QVector2D(*_pair(value))
        if point != self._xy:
            self._xy = point
            self._emit()

    xy: QVector2D = Property("QVariant", _get_xy, _set_xy, notify=changed)

    def _get_xytext(self) -> QVector2D:
        return QVector2D(self._xytext)

    def _set_xytext(self, value: object) -> None:
        point = QVector2D(*_pair(value))
        if point != self._xytext:
            self._xytext = point
            self._emit()

    xytext: QVector2D = Property("QVariant", _get_xytext, _set_xytext, notify=changed)

    def _get_arrow(self) -> bool:
        return self._arrow

    def _set_arrow(self, value: bool) -> None:
        value = bool(value)
        if value != self._arrow:
            self._arrow = value
            self._emit()

    arrow: bool = Property(bool, _get_arrow, _set_arrow, notify=changed)

    def _get_color(self) -> QColor:
        return QColor(self._color)

    def _set_color(self, value: object) -> None:
        color = _colour(value)
        if color != self._color:
            self._color = color
            self._emit()

    color: QColor = Property(QColor, _get_color, _set_color, notify=changed)

    def _get_font_size(self) -> float:
        return self._font_size

    def _set_font_size(self, value: float) -> None:
        value = float(value)
        if value != self._font_size:
            self._font_size = value
            self._emit()

    fontSize: float = Property(float, _get_font_size, _set_font_size, notify=changed)

    def _get_visible(self) -> bool:
        return self._visible

    def _set_visible(self, value: bool) -> None:
        value = bool(value)
        if value != self._visible:
            self._visible = value
            self._emit()

    visible: bool = Property(bool, _get_visible, _set_visible, notify=changed)


class Guide(QObject):
    """A horizontal (``kind="h"``) or vertical (``kind="v"``) reference line."""

    changed = Signal()

    def __init__(
        self,
        kind: str,
        value: float,
        *,
        color: object = None,
        width: float = 1.0,
        style: str = "--",
        visible: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if kind not in ("h", "v"):
            raise ValueError(f'guide kind must be "h" or "v", got {kind!r}')
        self._kind = kind
        self._value = float(value)
        self._color = _colour(color, "#888888")
        self._width = float(width)
        self._style = str(style)
        self._visible = bool(visible)

    def _emit(self) -> None:
        self.changed.emit()

    def _get_kind(self) -> str:
        return self._kind

    kind: str = Property(str, _get_kind, notify=changed)

    def _get_value(self) -> float:
        return self._value

    def _set_value(self, value: float) -> None:
        value = float(value)
        if value != self._value:
            self._value = value
            self._emit()

    value: float = Property(float, _get_value, _set_value, notify=changed)

    def _get_color(self) -> QColor:
        return QColor(self._color)

    def _set_color(self, value: object) -> None:
        color = _colour(value, "#888888")
        if color != self._color:
            self._color = color
            self._emit()

    color: QColor = Property(QColor, _get_color, _set_color, notify=changed)

    def _get_width(self) -> float:
        return self._width

    def _set_width(self, value: float) -> None:
        value = float(value)
        if value != self._width:
            self._width = value
            self._emit()

    width: float = Property(float, _get_width, _set_width, notify=changed)

    def _get_style(self) -> str:
        return self._style

    def _set_style(self, value: str) -> None:
        value = str(value)
        if value != self._style:
            self._style = value
            self._emit()

    style: str = Property(str, _get_style, _set_style, notify=changed)

    def _get_visible(self) -> bool:
        return self._visible

    def _set_visible(self, value: bool) -> None:
        value = bool(value)
        if value != self._visible:
            self._visible = value
            self._emit()

    visible: bool = Property(bool, _get_visible, _set_visible, notify=changed)


class AnnotationListModel(QAbstractListModel):
    """The annotations of one plot, as a QML list model."""

    AnnotationRole = int(Qt.ItemDataRole.UserRole) + 1
    TextRole = AnnotationRole + 1
    XRole = AnnotationRole + 2
    YRole = AnnotationRole + 3
    DXRole = AnnotationRole + 4
    DYRole = AnnotationRole + 5
    ArrowRole = AnnotationRole + 6
    ColorRole = AnnotationRole + 7
    FontSizeRole = AnnotationRole + 8
    VisibleRole = AnnotationRole + 9

    _ROLES = {
        AnnotationRole: b"annotation",
        TextRole: b"text",
        XRole: b"x",
        YRole: b"y",
        DXRole: b"dx",
        DYRole: b"dy",
        ArrowRole: b"arrow",
        ColorRole: b"color",
        FontSizeRole: b"fontSize",
        VisibleRole: b"visible",
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[Annotation] = []

    def roleNames(self) -> dict[int, bytes]:  # noqa: N802
        return dict(self._ROLES)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if role == self.AnnotationRole:
            return item
        if role == self.TextRole:
            return item.text
        if role == self.XRole:
            return item.xy.x()
        if role == self.YRole:
            return item.xy.y()
        if role == self.DXRole:
            return item.xytext.x()
        if role == self.DYRole:
            return item.xytext.y()
        if role == self.ArrowRole:
            return item.arrow
        if role == self.ColorRole:
            return item.color
        if role == self.FontSizeRole:
            return item.fontSize
        if role == self.VisibleRole:
            return item.visible
        return None

    def _notify(self, item: Annotation) -> None:
        for row, candidate in enumerate(self._items):
            if candidate is item:
                top = self.index(row, 0)
                self.dataChanged.emit(top, top)
                return

    def add(self, item: Annotation) -> Annotation:
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.append(item)
        item.setParent(self)
        self.endInsertRows()
        item.changed.connect(lambda item=item: self._notify(item))
        return item

    @Slot(QObject)
    def remove(self, item: Annotation) -> None:
        for row, candidate in enumerate(self._items):
            if candidate is item:
                self.beginRemoveRows(QModelIndex(), row, row)
                del self._items[row]
                self.endRemoveRows()
                return

    def clear(self) -> None:
        if not self._items:
            return
        self.beginRemoveRows(QModelIndex(), 0, len(self._items) - 1)
        self._items.clear()
        self.endRemoveRows()

    @Slot(int, result=QObject)
    def at(self, row: int) -> Annotation:
        return self._items[row]

    def __len__(self) -> int:
        return len(self._items)


class GuideListModel(QAbstractListModel):
    """The horizontal/vertical guides of one plot."""

    GuideRole = int(Qt.ItemDataRole.UserRole) + 1
    KindRole = GuideRole + 1
    ValueRole = GuideRole + 2
    ColorRole = GuideRole + 3
    WidthRole = GuideRole + 4
    StyleRole = GuideRole + 5
    VisibleRole = GuideRole + 6

    _ROLES = {
        GuideRole: b"guide",
        KindRole: b"kind",
        ValueRole: b"value",
        ColorRole: b"color",
        WidthRole: b"width",
        StyleRole: b"style",
        VisibleRole: b"visible",
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[Guide] = []

    def roleNames(self) -> dict[int, bytes]:  # noqa: N802
        return dict(self._ROLES)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if role == self.GuideRole:
            return item
        if role == self.KindRole:
            return item.kind
        if role == self.ValueRole:
            return item.value
        if role == self.ColorRole:
            return item.color
        if role == self.WidthRole:
            return item.width
        if role == self.StyleRole:
            return item.style
        if role == self.VisibleRole:
            return item.visible
        return None

    def _notify(self, item: Guide) -> None:
        for row, candidate in enumerate(self._items):
            if candidate is item:
                top = self.index(row, 0)
                self.dataChanged.emit(top, top)
                return

    def add(self, item: Guide) -> Guide:
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.append(item)
        item.setParent(self)
        self.endInsertRows()
        item.changed.connect(lambda item=item: self._notify(item))
        return item

    @Slot(QObject)
    def remove(self, item: Guide) -> None:
        for row, candidate in enumerate(self._items):
            if candidate is item:
                self.beginRemoveRows(QModelIndex(), row, row)
                del self._items[row]
                self.endRemoveRows()
                return

    def clear(self) -> None:
        if not self._items:
            return
        self.beginRemoveRows(QModelIndex(), 0, len(self._items) - 1)
        self._items.clear()
        self.endRemoveRows()

    @Slot(int, result=QObject)
    def at(self, row: int) -> Guide:
        return self._items[row]

    def __len__(self) -> int:
        return len(self._items)
