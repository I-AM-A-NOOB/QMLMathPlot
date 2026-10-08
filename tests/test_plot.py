"""Plot layer unit tests: the model the view binds to — camera, curves and styling.

No GUI. Qt is only used for the value types (QColor, QVector2D) and the property machinery.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QVector2D

from qmlmathplot import Camera, CurveListModel, Plot

#: Style properties of the contract, with a value QML or a theme could hand over.
STYLE: dict[str, object] = {
    "background": "#101010",
    "grid": 0,
    "grid_color": "#202020",
    "grid_width": 2,
    "grid_alpha": 0.25,
    "grid_style": "--",
    "color_cycle": ["#ff0000"],
    "line_width": 3,
    "text_color": "#303030",
    "tick_color": "#404040",
    "tick_length": 8,
    "font_size": 13,
    "tick_font_size": 9,
    "title": "sin(1/x)",
    "title_color": "#505050",
    "title_font_size": 18,
    "title_bold": 0,
    "ticks_visible": 0,
}


def test_plot_owns_its_camera_and_curve_model() -> None:
    plot = Plot()
    assert isinstance(plot.camera, Camera)
    assert isinstance(plot.curves, CurveListModel)
    assert plot.camera.parent() is plot
    assert plot.curves.parent() is plot


def test_add_and_remove_curve_are_forwarded() -> None:
    plot = Plot()
    plot.color_cycle = ["#ff0000", "#00ff00"]
    plot.line_width = 4.0
    first = plot.add_curve("sin(x)")
    second = plot.add_curve("cos(x)")
    assert [first.color.name(), second.color.name()] == ["#ff0000", "#00ff00"]
    assert first.lineWidth == 4.0                       # the plot's default width
    assert plot.curves.at(0) is first and len(plot.curves) == 2

    plot.remove_curve(first)
    assert len(plot.curves) == 1 and plot.curves.at(0) is second


def test_xlim_and_ylim_are_forwarded_to_the_camera() -> None:
    plot = Plot()
    plot.camera.setViewport(900, 600)
    seen = []
    plot.viewChanged.connect(lambda: seen.append(1))
    plot.xlim = (-1.0, 1.0)
    assert (plot.xlim.x(), plot.xlim.y()) == pytest.approx((-1.0, 1.0))
    assert (plot.camera.xlim.x(), plot.camera.xlim.y()) == pytest.approx((-1.0, 1.0))
    assert seen, "the forwarded property must notify"
    plot.ylim = QVector2D(-2.0, 2.0)
    assert (plot.ylim.x(), plot.ylim.y()) == pytest.approx((-2.0, 2.0))


def test_every_style_property_has_a_wired_notify_signal() -> None:
    """A PySide property with a notify name that does not resolve silently has *no* notify
    signal, and QML bindings then never refresh — so check the wiring, not just the value."""
    plot = Plot()
    meta = plot.metaObject()
    names = {str(meta.property(i).name()) for i in range(meta.propertyCount())}
    missing = set(STYLE) - names
    assert not missing, f"style properties missing from the meta-object: {sorted(missing)}"

    for name in STYLE:
        prop = meta.property(meta.indexOfProperty(name))
        assert prop.notifySignal().isValid(), f"{name} has no notify signal"
        assert prop.isReadable() and prop.isWritable(), f"{name} must be readable and writable"


def _notify_signal(plot: Plot, name: str):
    """The notify signal of a style property (Qt spells signals camelCase)."""
    head, *rest = name.split("_")
    return getattr(plot, head + "".join(part.title() for part in rest) + "Changed")


def test_style_values_convert_and_notify_once() -> None:
    plot = Plot()
    for name, value in STYLE.items():
        hits = []
        _notify_signal(plot, name).connect(lambda: hits.append(1))
        setattr(plot, name, value)
        assert hits == [1], f"{name} did not notify exactly once"
        setattr(plot, name, value)                       # idempotent
        assert hits == [1], f"{name} notified without a change"

    assert plot.background == QColor("#101010")
    assert plot.grid is False and plot.title_bold is False and plot.ticks_visible is False
    assert plot.grid_width == 2.0 and plot.tick_font_size == 9.0
    assert plot.grid_style == "--"
    assert plot.color_cycle == ["#ff0000"]


def test_color_cycle_is_handed_out_as_a_copy() -> None:
    plot = Plot()
    cycle = plot.color_cycle
    cycle.append("#ffffff")
    assert plot.color_cycle == list(plot.color_cycle) and "#ffffff" not in plot.color_cycle
    plot.color_cycle = ["#abcdef"]
    assert plot.add_curve("sin(x)").color == QColor("#abcdef")


def test_theme_applies_resolved_values_and_rejects_unknown_names() -> None:
    plot = Plot()
    plot.theme = "ggplot"
    assert plot.theme == "ggplot"
    assert plot.background == QColor("#e5e5e5")           # the theme really was applied
    assert plot.color_cycle[0] == "#E24A33"

    with pytest.raises(KeyError):
        plot.theme = "no-such-theme"
    assert plot.theme == "ggplot", "a failed lookup must leave the current theme alone"


def test_theme_is_idempotent() -> None:
    plot = Plot()
    plot.theme = "classic"
    plot.background = "#123456"                           # a manual change after the theme
    plot.theme = "classic"                                # setting it again is a no-op
    assert plot.background == QColor("#123456")
