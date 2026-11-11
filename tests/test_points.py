"""Data curves (``Plot.add_points``) and the program compilers."""

from __future__ import annotations

import math

import numpy as np
import pytest

from qmlmathplot import Plot
from qmlmathplot.calc import build_program
from qmlmathplot.curve import point_series, points_operations
from qmlmathplot.curveplotter import PlotView, stroke_vertices
from qmlmathplot.protocol import Program


def test_a_data_curve_holds_the_points() -> None:
    plot = Plot()
    curve = plot.add_points([0, 1, 2], [0, 1, 4], label="data")
    assert curve.is_data and not curve.implicit
    assert curve.expression == ""
    assert curve.points == ((0.0, 1.0, 2.0), (0.0, 1.0, 4.0))
    assert curve.label == "data"
    # The same curve can go back to an expression.
    curve.expression = "sin(x)"
    assert not curve.is_data and curve.points is None


def test_numpy_arrays_are_accepted_without_importing_numpy_here() -> None:
    xs = np.linspace(0.0, 1.0, 5)
    ys = xs * 2.0
    curve = Plot().add_points(xs, ys)
    assert curve.points is not None
    assert curve.points[0] == tuple(float(v) for v in xs)


def test_point_series_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="same non-zero length"):
        point_series(([0.0, 1.0], [0.0]))
    with pytest.raises(ValueError, match="pair of sequences"):
        point_series(1)
    with pytest.raises(ValueError, match="numeric"):
        point_series((["a"], [0.0]))


def test_a_non_finite_sample_breaks_the_polyline() -> None:
    operations = points_operations(
        ((0.0, 1.0, 2.0), (0.0, math.nan, 4.0))
    )
    assert [operation[0] for operation in operations] == ["MOVE_TO", "MOVE_TO"]


def test_data_points_become_stroke_vertices() -> None:
    view = PlotView(0.0, 2.0, 0.0, 4.0, 300, 200)
    vertices, count = stroke_vertices(
        points_operations(((0.0, 1.0, 2.0), (0.0, 1.0, 4.0))),
        view, (300.0, 200.0), 2.0, (0.1, 0.4, 0.9, 1.0),
    )
    assert count > 0 and len(vertices) == count * 32


def test_programs_compile_both_sources() -> None:
    program = build_program("sin(x)")
    math_function = program.compile()
    numpy_function = program.compile(numpy=True)
    assert math_function(0.3) == pytest.approx(math.sin(0.3))
    values = np.array([0.0, 0.5, 1.0])
    assert np.allclose(numpy_function(values), np.sin(values))


def test_a_program_without_a_source_cannot_compile() -> None:
    with pytest.raises(ValueError, match="no math source"):
        Program(text="points", kind="data").compile()


def test_the_data_kind_round_trips() -> None:
    program = Program(text="", kind="data")
    assert Program.from_json(program.to_json()) == program


# ------------------------------------------------------------------ rendering
@pytest.mark.gui
def test_a_data_curve_is_drawn(app) -> None:
    """The model role and the snapshot path: a data polyline reaches the RHI pass."""
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtQuick import QQuickView

    from qmlmathplot import qml_component_path

    pytest.importorskip("PySide6.QtQuick")
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(600, 400)
    view.setSource(QUrl.fromLocalFile(qml_component_path()))
    assert view.status() is QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    plot = Plot()
    plot.grid = False
    plot.ticks_visible = False
    xs = [index / 100.0 for index in range(101)]
    plot.add_points(xs, [value * value for value in xs])
    view.rootObject().setProperty("plot", plot)
    view.show()
    for _ in range(200):
        app.processEvents()
        if view.isExposed():
            break
    else:
        pytest.skip("no usable display/GPU scenegraph")
    plot.camera.set_viewport(600.0, 400.0)
    plot.camera.xlim = (0.0, 1.0)
    plot.camera.ylim = (0.0, 1.0)
    for _ in range(20):
        app.processEvents()
    result = view.rootObject().grabToImage()
    loop = QEventLoop()
    result.ready.connect(loop.quit)
    QTimer.singleShot(10_000, loop.quit)
    loop.exec()
    image = result.image()
    lit = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            color = image.pixelColor(x, y)
            if (255 - color.red()) + (255 - color.green()) + (255 - color.blue()) > 60:
                lit += 1
    view.close()
    assert lit > 200, f"the data polyline was not drawn ({lit} lit samples)"



def test_compiled_programs_have_no_builtins() -> None:
    program = build_program("sin(x)")
    assert program.compile().__globals__["__builtins__"] == {}
    assert program.compile(numpy=True).__globals__["__builtins__"] == {}


def test_a_program_cannot_reach_builtins_even_if_it_mentions_them() -> None:
    program = Program(text="x", kind="adaptive", math="def f(x):\n    return open('x')\n")
    function = program.compile()
    with pytest.raises(NameError):
        function(0.0)
