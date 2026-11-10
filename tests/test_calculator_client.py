"""The worker process: framed JSON round trip, failure reporting, and close()."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from qmlmathplot.client import InlineCalculator, ProcessCalculator
from qmlmathplot.protocol import Program


def _wait_for(signal, timeout_ms: int = 20_000):
    """Run the event loop until ``signal`` fires; returns its arguments."""
    loop = QEventLoop()
    received: list[tuple[object, ...]] = []

    def done(*args: object) -> None:
        received.append(args)
        loop.quit()

    signal.connect(done)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return received[0] if received else None


def test_the_inline_calculator_delivers_synchronously() -> None:
    calculator = InlineCalculator()
    received: list[tuple[object, Program]] = []
    calculator.ready.connect(lambda token, program: received.append((token, program)))
    calculator.request("sin(x)", token="curve-1")
    assert received and received[0][0] == "curve-1"
    assert received[0][1].kind == "intervals"


def test_the_worker_process_builds_a_program(app: QApplication) -> None:
    calculator = ProcessCalculator()
    try:
        calculator.request("sin(x)", token="curve-1")
        result = _wait_for(calculator.ready)
        assert result is not None, "the worker never answered"
        token, program = result
        assert token == "curve-1"
        assert isinstance(program, Program)
        assert program.kind == "intervals" and program.glsl.get("compute")
        assert program.numpy and "numpy.sin(x)" in program.numpy
    finally:
        calculator.close()


def test_the_worker_reports_parse_errors_as_programs(app: QApplication) -> None:
    calculator = ProcessCalculator()
    try:
        calculator.request("sin(", token="curve-2")
        result = _wait_for(calculator.ready)
        assert result is not None
        token, program = result
        assert token == "curve-2" and program.error
    finally:
        calculator.close()


def test_a_dead_worker_is_reported_through_failed(app: QApplication) -> None:
    calculator = ProcessCalculator(
        [sys.executable, "-c", "import sys; sys.exit(3)"], timeout=5.0
    )
    try:
        calculator.request("sin(x)", token="curve-3")
        result = _wait_for(calculator.failed)
        assert result is not None, "a dead worker must report a failure"
        token, message = result
        assert token == "curve-3" and "exited" in message
    finally:
        calculator.close()


def test_a_closed_calculator_refuses_new_requests(app: QApplication) -> None:
    calculator = ProcessCalculator()
    calculator.close()
    with pytest.raises(RuntimeError, match="closed"):
        calculator.request("sin(x)", token="curve-4")
