"""Annotation, guide, legend, custom tick and spine/tick control tests."""

from __future__ import annotations

import pytest

from qmlmathplot import Plot


def test_annotations_and_text_are_model_objects() -> None:
    plot = Plot()
    annotation = plot.annotate("peak", (0.0, 0.0), (20.0, -18.0),
                               arrowprops={"color": "#ff0000"})
    text = plot.add_text(1.0, 2.0, "label")
    assert plot.annotations.rowCount() == 2
    assert annotation.text == "peak" and annotation.arrow is True
    assert (annotation.xy.x(), annotation.xy.y()) == pytest.approx((0.0, 0.0))
    assert (annotation.xytext.x(), annotation.xytext.y()) == pytest.approx((20.0, -18.0))
    assert annotation.color.name() == "#ff0000"
    assert text.arrow is False
    assert plot.annotations.at(1) is text

    plot.remove_annotation(text)
    assert plot.annotations.rowCount() == 1
    plot.clear_annotations()
    assert plot.annotations.rowCount() == 0


def test_guides_are_model_objects() -> None:
    plot = Plot()
    horizontal = plot.add_hline(1.5, color="#00ff00", width=2.0, style=":")
    vertical = plot.add_vline(-1.0)
    assert plot.guides.rowCount() == 2
    assert (horizontal.kind, horizontal.value, horizontal.style) == ("h", 1.5, ":")
    assert vertical.kind == "v"
    plot.remove_guide(horizontal)
    assert plot.guides.rowCount() == 1
    plot.clear_guides()
    assert plot.guides.rowCount() == 0


def test_legend_and_tick_properties() -> None:
    plot = Plot()
    assert plot.legend is False
    plot.legend = True
    plot.legend_position = "lower left"
    assert plot.legend is True and plot.legend_position == "lower left"
    with pytest.raises(ValueError):
        plot.legend_position = "middle"

    plot.set_xticks([-1, 0, 1], ["-1", "zero", "1"])
    plot.set_yticks([-2, 2])
    assert plot.xticks == [[-1.0, "-1"], [0.0, "zero"], [1.0, "1"]]
    assert plot.yticks == [[-2.0, "-2"], [2.0, "2"]]
    plot.clear_xticks()
    assert plot.xticks == []

    plot.grid_x = False
    plot.grid_y = True
    plot.tick_width = 2.0
    plot.tick_pad = 5.0
    plot.ticks_top = True
    plot.ticks_right = True
    plot.minor_ticks = True
    plot.spine_top = False
    assert (plot.grid_x, plot.grid_y, plot.tick_width, plot.tick_pad) == \
        (False, True, 2.0, 5.0)
    assert plot.ticks_top and plot.ticks_right and plot.minor_ticks
    assert plot.spine_top is False


def test_handoff_maps_labels_legend_annotations_guides_and_ticks() -> None:
    pytest.importorskip("matplotlib")
    plot = Plot()
    plot.xlabel = "time"
    plot.ylabel = "value"
    plot.legend = True
    plot.legend_position = "lower right"
    plot.add_curve("sin(x)", label="sine")
    plot.annotate("peak", (0.0, 0.0), (10.0, -10.0), arrowprops={"color": "#ff0000"})
    plot.add_hline(0.5, color="#00ff00")
    plot.add_vline(1.0, color="#0000ff")
    plot.set_xticks([-1, 0, 1], ["a", "b", "c"])

    figure = plot.to_matplotlib()
    axes = figure.axes[0]
    assert axes.get_xlabel() == "time"
    assert axes.get_ylabel() == "value"
    assert axes.get_legend() is not None
    assert [text.get_text() for text in axes.texts] == ["peak"]
    assert any(len(line.get_ydata()) == 2 and line.get_ydata()[0] == pytest.approx(0.5)
               for line in axes.lines)
    assert list(axes.get_xticks()) == pytest.approx([-1.0, 0.0, 1.0])
