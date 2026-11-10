"""Calculators: the injection point between the model and the expression source.

The model never parses text. It asks a :class:`Calculator` for a
:class:`~qmlmathplot.protocol.Program` and consumes that. Two implementations ship here:

* :class:`InlineCalculator` — builds the program in the calling thread. Needed by scripts,
  headless use and the test suite; it imports sympy lazily, on the first request.
* :class:`ProcessCalculator` — asks a worker process (``python -m qmlmathplot.calc``). The
  host owns the process: when to start it, how to ship it, when to stop it.

Both deliver through the same ``ready`` / ``failed`` signals, so the model has exactly one
state machine. A host may implement its own calculator (any language, any transport); it
only has to produce programs.

Results carry an opaque **token** chosen by the caller (the model uses its generation
counter). The calculator never interprets it, so several curves may share one calculator
without coordinating ids, and a stale answer is discarded by the caller.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
from collections import deque

from PySide6.QtCore import QObject, Signal

from .protocol import (
    Program,
    decode_frames,
    encode_frame,
    parse_response,
    request_payload,
)

__all__ = ["Calculator", "InlineCalculator", "ProcessCalculator", "calculator_command"]

#: Environment override for the worker command line (any executable/shell words).
COMMAND_ENV = "QMLMATHPLOT_CALC"


def calculator_command() -> list[str]:
    """The worker command: ``$QMLMATHPLOT_CALC`` if set, else this interpreter's module."""
    override = os.environ.get(COMMAND_ENV)
    if override:
        return shlex.split(override, posix=False)
    return [sys.executable, "-m", "qmlmathplot.calc"]


class Calculator(QObject):
    """Asynchronous expression source: ``request`` now, ``ready``/``failed`` later."""

    #: ``(token, Program)``. Always emitted for a well-formed request, including parse errors
    #: (which arrive as ``Program.error``).
    ready = Signal(object, object)
    #: ``(token, message)`` — transport failures only (worker died, timeout, bad frame).
    failed = Signal(object, str)

    def request(self, text: str, implicit: bool | None = None, *, token: object = None) -> None:
        """Ask for a program. The result signals carry ``token`` back."""
        raise NotImplementedError

    def close(self) -> None:
        """Release resources (the inline calculator has none)."""


class InlineCalculator(Calculator):
    """Build programs in-process — the default, and what the tests use."""

    def request(self, text: str, implicit: bool | None = None, *, token: object = None) -> None:
        from .calc import build_program

        self.ready.emit(token, build_program(text, implicit=implicit))


class ProcessCalculator(Calculator):
    """Ask a worker process; framed JSON over its stdin/stdout.

    The worker is started on the first request and restarted after a crash. One request is in
    flight per token; the reader and stderr-drain threads spend their time blocked in C
    reads, so they do not hold the GIL.
    """

    def __init__(
        self,
        command: list[str] | None = None,
        *,
        timeout: float = 10.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._command = list(command) if command is not None else calculator_command()
        self._timeout = float(timeout)
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._pending: dict[int, tuple[object, threading.Timer]] = {}
        self._stderr: deque[str] = deque(maxlen=20)
        self._next_id = 0
        self._closed = False

    # ------------------------------------------------------------------ public
    def request(self, text: str, implicit: bool | None = None, *, token: object = None) -> None:
        if self._closed:
            raise RuntimeError("this calculator has been closed")
        self._ensure_started()
        self._next_id += 1
        wire_id = self._next_id
        payload = request_payload(wire_id, text, implicit)
        timer = threading.Timer(self._timeout, self._on_timeout, args=(wire_id,))
        timer.daemon = True
        with self._lock:
            self._pending[wire_id] = (token, timer)
        timer.start()
        try:
            assert self._process is not None and self._process.stdin is not None
            self._process.stdin.write(encode_frame(payload))
            self._process.stdin.flush()
        except (OSError, ValueError) as exc:
            self._finish(wire_id, error=f"calculator write failed: {exc}")

    def close(self) -> None:
        self._closed = True
        process = self._process
        self._process = None
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        with self._lock:
            timers = [timer for _token, timer in self._pending.values()]
            self._pending.clear()
        for timer in timers:
            timer.cancel()

    # --------------------------------------------------------------- internals
    def _ensure_started(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        flags = 0
        if sys.platform == "win32":           # no console window from a GUI host (AGENTS 57)
            flags = subprocess.CREATE_NO_WINDOW
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=flags,
        )
        threading.Thread(target=self._read_stdout, name="qmlmathplot-calc-out",
                         daemon=True).start()
        threading.Thread(target=self._read_stderr, name="qmlmathplot-calc-err",
                         daemon=True).start()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        buffer = b""
        while True:
            try:
                read = getattr(process.stdout, "read1", process.stdout.read)
                chunk = read(65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buffer += chunk
            try:
                frames, buffer = decode_frames(buffer, limit=64)
            except ValueError as exc:
                self._fail_all(f"calculator sent a bad frame: {exc}")
                break
            for payload in frames:
                try:
                    wire_id, program = parse_response(payload)
                except ValueError as exc:
                    self._fail_all(f"calculator sent a bad response: {exc}")
                    continue
                self._deliver(wire_id, program)
        self._fail_all(self._death_message())

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while True:
            line = process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                with self._lock:
                    self._stderr.append(text)

    def _death_message(self) -> str:
        with self._lock:
            tail = list(self._stderr)[-3:]
        return "calculator process exited" + (": " + " | ".join(tail) if tail else "")

    def _deliver(self, wire_id: int, program: Program) -> None:
        entry = self._take(wire_id)
        if entry is None:
            return                           # already timed out / closed
        token, timer = entry
        timer.cancel()
        self.ready.emit(token, program)

    def _on_timeout(self, wire_id: int) -> None:
        entry = self._take(wire_id)
        if entry is not None:
            token, _timer = entry
            self.failed.emit(token, f"calculator timed out after {self._timeout:.1f} s")

    def _finish(self, wire_id: int, *, error: str) -> None:
        entry = self._take(wire_id)
        if entry is not None:
            token, timer = entry
            timer.cancel()
            self.failed.emit(token, error)

    def _take(self, wire_id: int) -> tuple[object, threading.Timer] | None:
        with self._lock:
            return self._pending.pop(wire_id, None)

    def _fail_all(self, message: str) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for token, timer in pending:
            timer.cancel()
            self.failed.emit(token, message)
