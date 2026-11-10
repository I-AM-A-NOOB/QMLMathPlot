"""The calculator ↔ model contract: one expression -> one :class:`Program`, as plain data.

The library never parses or converts expressions itself. A calculator (in-process or in
another process) turns the user's text into a :class:`Program` — shader sources, a numpy
program, a `math` program — and the model only consumes that. Nothing here imports Qt or
sympy, so both sides of a process boundary can use it.

Wire format
-----------
Length-prefixed UTF-8 JSON: a 4-byte big-endian payload length, then one JSON object.
A request is ``{"schema": 2, "id": <int>, "text": "...", "implicit": <bool|null>}``; a
response is ``{"schema": 2, "id": <int>, "program": {…}}``. The protocol is deliberately
small and stable: hosts may implement the worker in any language.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Final

__all__ = [
    "SCHEMA",
    "Program",
    "decode_frames",
    "encode_frame",
    "parse_request",
    "parse_response",
    "request_payload",
    "response_payload",
]

#: Bumped when a field changes meaning. Fields are only ever added.
SCHEMA: Final = 2

#: ``"intervals"`` = GeoGebra interval path (compute shader), ``"adaptive"`` = CPU path,
#: ``"implicit"`` = level set (fragment shader).
KINDS: Final = ("intervals", "adaptive", "implicit", "data")

#: Graph kinds the model knows how to draw.
FUNCTION_NAMES: Final = {"intervals": "f", "adaptive": "f", "implicit": "F", "data": "f"}

_MAX_FRAME: Final = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Program:
    """Everything the model needs to draw one expression; all fields are plain data.

    ``glsl`` holds only the stages the renderer will bake ("fragment" for level sets,
    "compute" for the interval path). ``numpy`` / ``math`` are complete Python function
    definitions over ``args`` (``"x"`` for a function, ``"x", "y"`` for a level set); the
    model compiles them itself, so a calculator never hands out callables.
    """

    schema: int = SCHEMA
    text: str = ""
    kind: str = "adaptive"
    args: tuple[str, ...] = ("x",)
    glsl: dict[str, str] = field(default_factory=dict)
    numpy: str | None = None
    math: str | None = None
    error: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown program kind {self.kind!r}; expected one of {KINDS}")

    @property
    def name(self) -> str:
        """The function name the ``numpy``/``math`` sources define."""
        return FUNCTION_NAMES[self.kind]

    @property
    def implicit(self) -> bool:
        return self.kind == "implicit"

    def compile(self, *, numpy: bool = False, name: str | None = None) -> object:
        """Compile the ``math`` (or ``numpy``) source and return its function."""
        from .program import compile_function

        source = self.numpy if numpy else self.math
        if not source:
            raise ValueError(f"the program has no {'numpy' if numpy else 'math'} source")
        return compile_function(source, name or self.name, numpy=numpy)

    def with_error(self, message: str) -> Program:
        return replace(self, error=str(message))

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema": self.schema,
            "text": self.text,
            "kind": self.kind,
            "args": list(self.args),
            "glsl": dict(self.glsl),
            "error": self.error,
        }
        if self.numpy is not None:
            data["numpy"] = self.numpy
        if self.math is not None:
            data["math"] = self.math
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Program:
        schema = int(data.get("schema", 0))
        if schema != SCHEMA:
            raise ValueError(f"unsupported program schema {schema}; this build speaks {SCHEMA}")
        kind = str(data.get("kind", "adaptive"))
        args = data.get("args", ("x",))
        if not isinstance(args, (list, tuple)) or not all(isinstance(a, str) for a in args):
            raise ValueError(f"program args must be a list of names, got {args!r}")
        glsl = data.get("glsl") or {}
        if not isinstance(glsl, Mapping):
            raise ValueError("program glsl must be an object")
        program = cls(
            schema=schema,
            text=str(data.get("text", "")),
            kind=kind,
            args=tuple(args),
            glsl={str(key): str(value) for key, value in glsl.items()},
            numpy=_optional_str(data.get("numpy")),
            math=_optional_str(data.get("math")),
            error=str(data.get("error", "")),
        )
        return program

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str | bytes) -> Program:
        return cls.from_dict(json.loads(text))


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


# ------------------------------------------------------------------------ framing
def encode_frame(payload: Mapping[str, Any] | bytes) -> bytes:
    """One length-prefixed frame: 4-byte big-endian length + UTF-8 JSON (or raw bytes)."""
    body = payload if isinstance(payload, bytes) else json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(body) > _MAX_FRAME:
        raise ValueError(f"frame too large: {len(body)} bytes")
    return struct.pack(">I", len(body)) + body


def decode_frames(data: bytes, *, limit: int = 64) -> tuple[list[dict[str, Any]], bytes]:
    """Decode as many complete frames as ``data`` holds; return them and the remainder."""
    frames: list[dict[str, Any]] = []
    rest = data
    while rest and len(frames) < limit:
        if len(rest) < 4:
            break
        (length,) = struct.unpack(">I", rest[:4])
        if length > _MAX_FRAME:
            raise ValueError(f"frame too large: {length} bytes")
        if len(rest) < 4 + length:
            break
        body = rest[4:4 + length]
        rest = rest[4 + length:]
        frames.append(json.loads(body.decode("utf-8")))
    return frames, rest


# ---------------------------------------------------------------------- messages
def request_payload(request_id: int, text: str, implicit: bool | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"schema": SCHEMA, "id": int(request_id), "text": str(text)}
    if implicit is not None:
        payload["implicit"] = bool(implicit)
    return payload


def response_payload(request_id: int, program: Program) -> dict[str, Any]:
    return {"schema": SCHEMA, "id": int(request_id), "program": program.to_dict()}


def parse_request(payload: Mapping[str, Any]) -> tuple[int, str, bool | None]:
    """``(request_id, text, implicit)``; raises ``ValueError`` on a malformed request."""
    if int(payload.get("schema", 0)) != SCHEMA:
        raise ValueError(f"unsupported request schema {payload.get('schema')!r}")
    try:
        request_id = int(payload["id"])
        text = str(payload.get("text", ""))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed request: {exc}") from None
    implicit = payload.get("implicit")
    return request_id, text, None if implicit is None else bool(implicit)


def parse_response(payload: Mapping[str, Any]) -> tuple[int, Program]:
    if int(payload.get("schema", 0)) != SCHEMA:
        raise ValueError(f"unsupported response schema {payload.get('schema')!r}")
    try:
        request_id = int(payload["id"])
        program = Program.from_dict(payload["program"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed response: {exc}") from None
    return request_id, program


def iter_frames(stream: Iterable[bytes]) -> Iterable[dict[str, Any]]:
    """Yield frames from an iterable of byte chunks (a blocking read loop)."""
    buffer = b""
    for chunk in stream:
        if not chunk:
            break
        buffer += chunk
        frames, buffer = decode_frames(buffer)
        yield from frames
