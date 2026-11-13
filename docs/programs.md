# Programs and calculators

`Program` is the boundary between the model and whatever turns text into drawing
instructions. This page documents the data, the source protocol, and how to run a calculator
in another process (or another language).

## Why a boundary

The model (`Plot`, `Curve`, `PlotView`) deliberately knows nothing about parsers. That keeps
SymPy (or any other CAS) out of the UI process, lets a host compute expressions on a worker,
and makes the renderer independent of where a program came from. A `Program` is small,
plain data; a process boundary costs one JSON message per expression.

## The `Program` record

```python
@dataclass(frozen=True)
class Program:
    schema: int = 2
    text: str = ""
    kind: str = "adaptive"        # intervals | adaptive | implicit | data
    args: tuple[str, ...] = ("x",)
    glsl: dict[str, str] = {}     # "compute" or "fragment" -> source
    numpy: str | None = None
    math: str | None = None
    error: str = ""
```

- `kind` selects the renderer ([Rendering modes](rendering.md)).
- `glsl` is only filled for the stages that renderer bakes: `compute` for `intervals`,
  `fragment` for `implicit`. `adaptive` and `data` need no shader.
- `math` and `numpy` are **complete function definitions**, not expressions:
  `"def f(x):\n    return math.sin(x)\n"` for a function, `def F(x, y): …` for a level set.
  The model compiles them itself (`Program.compile`), so a calculator never hands out
  callables.
- `error` is the only failure channel for a well-formed request: a parse error, an
  unsupported operation, or a domain in which the shader cannot be generated. The model
  keeps the previous drawing and shows the message.

JSON example (abridged):

```json
{
  "schema": 2,
  "text": "sin(1/x)",
  "kind": "intervals",
  "args": ["x"],
  "glsl": {"compute": "#version 440\n..."},
  "math": "def f(x):\n    return math.sin(1/x)\n",
  "numpy": "def f(x):\n    return numpy.sin(x**(-1.0))\n",
  "error": ""
}
```

Round trips: `Program.to_json()` / `Program.from_json()`; pickling works too. The
`schema` is checked: a reader refuses a version it does not know. Fields are only added,
never re-purposed.

## `ProgramSource`

```python
class ProgramSource(Protocol):
    def program(self, text: str, *, implicit: bool | None = None) -> Program: ...
```

The contract is **synchronous**: the returned `Program` is on the curve when `add_curve`
returns. Only the shader bake is asynchronous. This is why the model stays simple and why a
process-backed calculator belongs in a worker: the worker produces programs, the UI applies
them.

`implicit=True` forces the level-set kind (`add_implicit`); `None` lets the source decide
(an equation or a free `y` is a level set).

Bundled source:

```python
from qmlmathplot import Plot
from qmlmathplot.calc import SymPyProgramSource

plot = Plot(program_source=SymPyProgramSource())
curve = plot.add_curve("x**2 + y**2 - 1")   # kind == "implicit", program ready now
```

Without a source:

```python
plot = Plot()
plot.add_curve("sin(x)")        # raises ProgramSourceMissingError
plot.add_program(program)       # works
plot.add_points(x, y)           # works
```

## The decoupled entry points

```python
plot.add_program(program, color=None, line_width=None, label="") -> Curve
curve.set_program(program) -> None
plot.curves.add_program(program) -> Curve
```

They do not parse, do not import SymPy, and do not care where the program came from. A
failed bake keeps the previous shaders; the new program is still the one the curve
describes.

## Worker process (host-owned)

The library never starts a process. `examples/process_calculator.py` is a complete minimal
host; the shape is:

```python
from qmlmathplot import Plot
from qmlmathplot.protocol import decode_frames, encode_frame, parse_response, request_payload

def request_program(text: str) -> Program:
    process = subprocess.Popen([sys.executable, "-m", "qmlmathplot.calc"],
                               stdin=PIPE, stdout=PIPE, stderr=PIPE, ...)
    process.stdin.write(encode_frame(request_payload(1, text)))
    process.stdin.flush()
    # read stdout, decode_frames(), parse_response() -> Program
    ...

plot = Plot(program_source=None)
plot.add_program(request_program("sin(1/x)"))
```

Wire format: a 4-byte big-endian length followed by one UTF-8 JSON object.

```json
{"schema": 2, "id": 7, "text": "sin(x)", "implicit": null}
{"schema": 2, "id": 7, "program": { ... }}
```

- `id` is chosen by the client; the response echoes it.
- The worker must write nothing but frames to stdout; diagnostics go to stderr (the client
  should drain stderr, as the example does).
- The bundled worker is `python -m qmlmathplot.calc` (`[calc]` extra). It is Qt-free and
  imports only SymPy and the calculator modules.

For a long-lived worker, the host owns start/stop/restart and stale-request handling. The
model's generation counter only guards its own shader bakes; a client should also ignore a
response whose `id` no longer matches the latest request.

## Trust and execution

- `Program.compile` executes the source with a namespace containing only `math`/`numpy` and
  `abs`, and an **explicit empty `__builtins__`**. A program cannot reach `open`,
  `__import__`, or any module the calculator did not emit.
- Treat the calculator as the trust boundary: it is the component that emits source text.
  Do not apply a `Program` from an untrusted party without reviewing what it can call.
- SymPy itself is imported only by `qmlmathplot.calc`. The core import chain never loads it;
  `tests/test_no_sympy_blocked.py` makes `import sympy` fail and still exercises the core,
  `add_program`, and the matplotlib hand-off.

## Writing a calculator in another language

Emit the same JSON. Minimum for a 1-D function:

1. `kind`: `"intervals"` if you implement the interval path shader, otherwise `"adaptive"`.
2. `math`: `def f(x): return …` using the host language's `math`-like functions
   (`sin`, `cos`, `tan`, `log`, `exp`, `sqrt`, `abs`); this is what the CPU fallback and the
   scalar sampler use.
3. `numpy`: optional; used by `to_matplotlib` and available to hosts.
4. `glsl`: for `intervals`, the compute shader generated by the bundled interval emitter;
   for `implicit`, the fragment shader. If you cannot generate GLSL, use `"adaptive"`:
   the renderer strokes the CPU path through the shared GPU pipeline.

The bundled emitters (`calc/interval.py`, `calc/ggb_compute.py`, `calc/model.py`) are the
reference for the shader contract; `calc/build.py` shows the routing decisions
(equation → implicit, interval-eligible → intervals, otherwise adaptive).

## Testing a source

- `tests/test_program_source.py` covers the synchronous contract, missing sources, and
  `set_program`.
- `tests/test_protocol.py` covers framing, schema checks, JSON and pickle round trips.
- `tests/test_calc_worker.py` drives the bundled worker over pipes.
- `tests/test_no_sympy_ui.py` and `tests/test_no_sympy_blocked.py` prove the isolation.
