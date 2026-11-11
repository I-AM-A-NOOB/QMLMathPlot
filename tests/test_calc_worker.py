"""The calc worker process: framed JSON in, programs out (no Qt in the worker)."""

from __future__ import annotations

import subprocess
import sys

from qmlmathplot.protocol import (
    SCHEMA,
    decode_frames,
    encode_frame,
    parse_response,
    request_payload,
)


def _round_trip(text: str) -> tuple[int, object]:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [sys.executable, "-m", "qmlmathplot.calc"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=flags,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(encode_frame(request_payload(3, text)))
        process.stdin.flush()
        buffer = b""
        while True:
            chunk = process.stdout.read1(65536)
            assert chunk, "the worker exited without an answer"
            buffer += chunk
            frames, buffer = decode_frames(buffer)
            if frames:
                return parse_response(frames[0])
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_the_worker_returns_a_program() -> None:
    request_id, program = _round_trip("sin(x)")
    assert request_id == 3
    assert program.kind == "intervals" and program.glsl.get("compute")
    assert program.math and program.numpy


def test_the_worker_reports_parse_errors_inside_the_program() -> None:
    _request_id, program = _round_trip("sin(")
    assert program.error
    assert SCHEMA == 2
