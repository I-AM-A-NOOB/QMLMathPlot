"""Worker process entry point: ``python -m qmlmathplot.calc``.

Reads length-prefixed JSON requests from stdin and writes one response per request to
stdout. Nothing but frames is ever written to stdout (import-time noise from sympy goes to
stderr, which the client drains), so the channel is safe to parse.
"""

from __future__ import annotations

import sys

from ..protocol import (
    SCHEMA,
    Program,
    decode_frames,
    encode_frame,
    parse_request,
    response_payload,
)
from .build import build_program

__all__ = ["main"]


def _error_program(payload: object) -> tuple[int, Program]:
    text = ""
    request_id = 0
    if isinstance(payload, dict):
        request_id = int(payload.get("id", 0) or 0)
        text = str(payload.get("text", ""))
    return request_id, Program(text=text, error="malformed request")


def main() -> int:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    buffer = b""
    while True:
        chunk = stdin.read1(65536) if hasattr(stdin, "read1") else stdin.read(65536)
        if not chunk:
            break
        buffer += chunk
        try:
            frames, buffer = decode_frames(buffer, limit=64)
        except ValueError as exc:
            stdout.write(encode_frame({"schema": SCHEMA, "id": 0, "error": str(exc)}))
            stdout.flush()
            return 1
        for payload in frames:
            try:
                request_id, text, implicit = parse_request(payload)
                program = build_program(text, implicit=implicit)
            except Exception:  # noqa: BLE001 - a bad frame must not kill the worker
                request_id, program = _error_program(payload)
            stdout.write(encode_frame(response_payload(request_id, program)))
            stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
