"""Matplotlib-flavoured themes.

A theme is a set of overrides for a :class:`~qmlmathplot.plot.Plot`'s style properties.
The bundled ones are Matplotlib's own style sheets, vendored verbatim into
``qmlmathplot/themes/*.mplstyle`` and redistributed under Matplotlib's BSD license (see
``themes/LICENSE.matplotlib``). They are converted at load time: Matplotlib's ``rcParams``
keys are mapped onto this library's property names, and keys that describe things this
library does not have (spines, figure size, dpi, legend, marker styles, …) are ignored.

    plot.theme = "ggplot"
    plot.theme = "dark_background"
    plot.theme = "seaborn-v0_8-darkgrid"
    plot.theme = "my-style"                  # themes/my-style.mplstyle
    plot.theme = "path/to/any.mplstyle"      # any file with the same format

The file format is Matplotlib's: ``key: value`` lines, ``#`` starts a comment. Only the
keys listed in ``_KEY_MAP`` are converted; the rest are dropped silently, so a style sheet
can be dropped in without editing.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["names", "raw", "resolve"]

THEME_DIR = Path(__file__).with_name("themes")

#: Matplotlib sizes are in points; this library's properties are logical pixels.
PT_TO_PX = 4.0 / 3.0

#: Matplotlib's named font sizes, in points.
_NAMED_SIZES = {
    "xx-small": 5.79, "x-small": 6.94, "small": 8.33, "medium": 10.0,
    "large": 12.0, "x-large": 14.4, "xx-large": 17.28,
}

_LINE_STYLES = {
    "solid": "-", "dashed": "--", "dotted": ":", "dashdot": "-.", "none": "", "": "",
    "-": "-", "--": "--", ":": ":", "-.": "-.",
}

#: matplotlib's single-letter colour names.
_SHORT_COLORS = {
    "b": "blue", "g": "green", "r": "red", "c": "cyan",
    "m": "magenta", "y": "yellow", "k": "black", "w": "white",
}

#: Font weights that mean "bold" (matplotlib's ``axes.titleweight``).
_BOLD_WEIGHTS = frozenset({"bold", "heavy", "semibold", "black"})

#: For every Plot property, the style-sheet keys that can feed it, most specific first.
#: A key that is absent is skipped; unknown keys are dropped entirely.
_SOURCES: dict[str, tuple[str, ...]] = {
    "background": ("axes.facecolor", "figure.facecolor"),
    "grid": ("axes.grid",),
    "grid_color": ("grid.color",),
    "grid_width": ("grid.linewidth",),
    "grid_alpha": ("grid.alpha",),
    "grid_style": ("grid.linestyle",),
    "color_cycle": ("axes.prop_cycle",),
    "line_width": ("lines.linewidth",),
    "text_color": ("text.color", "axes.labelcolor"),
    "tick_color": ("xtick.color", "ytick.color"),
    "tick_length": ("xtick.major.size", "ytick.major.size"),
    "font_size": ("font.size",),
    "tick_font_size": ("xtick.labelsize", "ytick.labelsize"),
    "title_color": ("axes.titlecolor",),
    "title_font_size": ("axes.titlesize",),
    "title_bold": ("axes.titleweight",),
}

#: Keys whose value is a boolean rather than text.
_BOOL_KEYS = frozenset({"axes.grid", "axes.titleweight"})
#: Keys whose value is a size in points (converted to logical pixels).
_PT_KEYS = frozenset({
    "grid.linewidth", "lines.linewidth", "xtick.major.size", "ytick.major.size",
    "font.size", "xtick.labelsize", "ytick.labelsize", "axes.titlesize",
})
#: Keys whose value is a colour.
_COLOR_KEYS = frozenset({
    "xtick.color", "ytick.color", "text.color", "axes.labelcolor", "axes.titlecolor",
    "grid.color", "axes.facecolor", "figure.facecolor",
})


def names() -> list[str]:
    """Names of the bundled themes (file stems), plus ``"default"`` (no overrides)."""
    stems = sorted(p.stem for p in THEME_DIR.glob("*.mplstyle")) if THEME_DIR.is_dir() else []
    return ["default", *stems]


def raw(name: str) -> dict[str, str]:
    """The theme's raw ``rcParams`` (key -> text value), before conversion."""
    if name == "default":
        return {}
    path = Path(name)
    if not path.is_file():
        path = THEME_DIR / f"{name}.mplstyle"
    if not path.is_file():
        raise KeyError(f"unknown theme {name!r}; available: {', '.join(names())}")
    return _parse(path.read_text(encoding="utf-8"))


def resolve(name: str) -> dict[str, object]:
    """Convert a theme into ``{plot property: value}`` overrides.

    Only keys this library understands are returned; everything else in the style sheet
    (spines, figure size, dpi, legend, marker styles, …) is dropped, because there is no
    figure, no axes box and no legend here.
    """
    values = raw(name)
    cycle = _parse_cycle(values.get("axes.prop_cycle", ""))
    out: dict[str, object] = {}
    for prop, keys in _SOURCES.items():
        for key in keys:
            if key not in values:
                continue
            value = _convert(key, values[key], cycle)
            if value is not None:
                out[prop] = value
                break
    return out


def _convert(key: str, text: str, cycle: list[str]) -> object | None:
    """One style-sheet value -> the value a Plot property expects (``None`` = skip)."""
    if key == "axes.prop_cycle":
        return cycle or None
    if key == "axes.grid":
        return text.strip().lower() in ("true", "1", "yes", "on")
    if key == "axes.titleweight":
        return text.strip().lower() in _BOLD_WEIGHTS
    if key == "grid.linestyle":
        return _LINE_STYLES.get(text.strip().lower())
    if key in _PT_KEYS:
        return _size(text) * PT_TO_PX if key.endswith("size") else _number(text) * PT_TO_PX
    if key in _COLOR_KEYS:
        return _color(text, cycle)
    return None


# --------------------------------------------------------------------------- parsing


def _parse(text: str) -> dict[str, str]:
    """Parse ``key: value`` lines; later keys win (Matplotlib semantics)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip() if not line.lstrip().startswith("#") else ""
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def _number(text: str) -> float:
    try:
        return float(text.strip())
    except ValueError:
        return 0.0


def _size(text: str) -> float:
    """A font size: a number in points, a named size, or a relative one."""
    text = text.strip().lower()
    if text in _NAMED_SIZES:
        return _NAMED_SIZES[text]
    try:
        return float(text)
    except ValueError:
        return _NAMED_SIZES["medium"]


_CYCLE_LIST = re.compile(r"\[([^\]]*)\]")
_QUOTED = re.compile(r"['\"]([^'\"]+)['\"]")


def _parse_cycle(text: str) -> list[str]:
    """``cycler('color', ['#1f77b4', …])`` -> the colour list.

    Only the quoted strings *inside the list* count: the first argument of ``cycler`` is the
    property name (``'color'``) and must not be mistaken for a colour.
    """
    if "cycler" not in text:
        return []
    inside = _CYCLE_LIST.search(text)
    if not inside:
        return []
    out: list[str] = []
    for item in _QUOTED.findall(inside.group(1)):
        value = _color(item, [])
        if isinstance(value, str):
            out.append(value)
    return out


def _gray(value: float) -> str:
    """A 0..1 grayscale value as ``#rrggbb`` (matplotlib's float colour shorthand)."""
    level = int(round(max(0.0, min(1.0, value)) * 255))
    return f"#{level:02x}{level:02x}{level:02x}"


def _normalise_hex(text: str) -> str:
    """Matplotlib accepts hex colours without the leading ``#`` (``E24A33``)."""
    text = text.strip()
    if re.fullmatch(r"[0-9a-fA-F]{6}", text) or re.fullmatch(r"[0-9a-fA-F]{3}", text):
        return f"#{text}"
    return text


def _color(text: str, cycle: list[str]) -> str | float | None:
    """A colour value: ``#rrggbb``, a name, a grayscale float, or ``C0``… from the cycle.

    Returns ``None`` for values that mean "nothing" (``none``), which have no equivalent
    here because there is no frame or spine to hide.
    """
    text = text.strip()
    low = text.lower()
    if low in ("none", ""):
        return None
    match = re.fullmatch(r"c(\d+)", low)
    if match:
        index = int(match.group(1))
        return cycle[index % len(cycle)] if cycle else None
    if text in _SHORT_COLORS:       # matplotlib's single-letter names ("w", "k")
        return _SHORT_COLORS[text]
    if re.fullmatch(r"[0-9a-fA-F]{6}", text) or re.fullmatch(r"[0-9a-fA-F]{3}", text):
        return f"#{text}"           # matplotlib accepts hex without the leading '#'
    try:
        value = float(text)
    except ValueError:
        return text                 # a colour name ("white", "steelblue", …)
    return _gray(value) if 0.0 <= value <= 1.0 else None   # grayscale shorthand ("0.8")
