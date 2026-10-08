"""View layer glue: which QML types this package offers and where its component lives."""

from __future__ import annotations

from importlib.resources import files

from PySide6.QtCore import QUrl

from .camera import Camera
from .curve import Curve, CurveListModel
from .plot import Plot

__all__ = ["QML_MAJOR", "QML_MINOR", "QML_URI", "qml_component_path", "register_qml_types"]

QML_URI = "QmlMathPlot"
QML_MAJOR = 1
QML_MINOR = 0

_registered = False


def register_qml_types() -> None:
    """Register the QML types of this package (call before the engine loads).

    Registers the model side (``Plot``, ``Camera``, ``Curve``, ``CurveListModel``) and the
    ``PlotView`` component, so a Qt Quick app can simply write::

        import QmlMathPlot 1.0
        PlotView { anchors.fill: parent }

    Idempotent: repeated calls are a no-op (Qt complains about duplicate registrations).
    """
    global _registered
    if _registered:
        return

    from PySide6.QtQml import qmlRegisterType

    # Note: PySide6's signature annotation says bytes, but at runtime it only accepts str
    for qml_name, qml_type in (
        ("Plot", Plot),
        ("Camera", Camera),
        ("Curve", Curve),
        ("CurveListModel", CurveListModel),
    ):
        qmlRegisterType(qml_type, QML_URI, QML_MAJOR, QML_MINOR, qml_name)  # type: ignore[arg-type]
    qmlRegisterType(
        QUrl.fromLocalFile(qml_component_path()), QML_URI, QML_MAJOR, QML_MINOR, "PlotView"
    )
    _registered = True


def qml_component_path() -> str:
    """File path of the reusable QML component (setSource / Loader when embedding it)."""
    return str(files("qmlmathplot").joinpath("qml/PlotView.qml"))
