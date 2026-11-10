"""The Program contract: JSON round trips, framing, and malformed input."""

from __future__ import annotations

import json

import pytest

from qmlmathplot.protocol import (
    SCHEMA,
    Program,
    decode_frames,
    encode_frame,
    parse_request,
    parse_response,
    request_payload,
    response_payload,
)


def _program() -> Program:
    return Program(
        text="sin(x)",
        kind="intervals",
        args=("x",),
        glsl={"compute": "// compute"},
        numpy="def f(x):\n    return numpy.sin(x)\n",
        math="def f(x):\n    return math.sin(x)\n",
    )


def test_round_trip_keeps_every_field() -> None:
    program = _program()
    restored = Program.from_json(program.to_json())
    assert restored == program


def test_optional_sources_are_omitted_and_read_back_as_none() -> None:
    program = Program(text="sin(x)", kind="intervals", args=("x",), glsl={})
    data = program.to_dict()
    assert "numpy" not in data and "math" not in data and data["glsl"] == {}
    restored = Program.from_dict(data)
    assert restored.numpy is None and restored.math is None and restored.glsl == {}


def test_the_schema_is_checked() -> None:
    data = _program().to_dict()
    data["schema"] = SCHEMA + 1
    with pytest.raises(ValueError, match="schema"):
        Program.from_dict(data)


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="kind"):
        Program(text="x", kind="nonsense")


def test_frames_split_arbitrarily() -> None:
    first = encode_frame({"schema": SCHEMA, "id": 1})
    second = encode_frame({"schema": SCHEMA, "id": 2})
    stream = first + second
    frames, rest = decode_frames(stream)
    assert [frame["id"] for frame in frames] == [1, 2]
    assert rest == b""

    frames, rest = decode_frames(first[:2])
    assert frames == [] and rest == first[:2]
    frames, rest = decode_frames(first[:6])
    assert frames == [] and rest == first[:6]


def test_a_partial_second_frame_stays_in_the_buffer() -> None:
    first = encode_frame({"id": 1})
    second = encode_frame({"id": 2})
    frames, rest = decode_frames(first + second[:-1])
    assert [frame["id"] for frame in frames] == [1]
    assert rest == second[:-1]


def test_an_oversized_frame_is_refused() -> None:
    with pytest.raises(ValueError, match="too large"):
        decode_frames(b"\xff\xff\xff\xff")


def test_request_and_response_payloads() -> None:
    request = request_payload(4, "sin(x)", implicit=True)
    assert request == {"schema": SCHEMA, "id": 4, "text": "sin(x)", "implicit": True}
    assert parse_request(request) == (4, "sin(x)", True)
    assert parse_request({"schema": SCHEMA, "id": 4, "text": "x"}) == (4, "x", None)

    response = response_payload(4, _program())
    request_id, program = parse_response(json.loads(json.dumps(response)))
    assert request_id == 4 and program == _program()


def test_malformed_messages_raise() -> None:
    with pytest.raises(ValueError, match="request"):
        parse_request({"schema": SCHEMA})
    with pytest.raises(ValueError, match="response"):
        parse_response({"schema": SCHEMA, "id": 1})
    with pytest.raises(ValueError, match="request schema"):
        parse_request({"schema": 99, "id": 1, "text": "x"})
