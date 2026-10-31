"""Shader resources: load the GLSL once, substitute ``@NAME@`` placeholders, validate.

The shaders live in ``qmlmathplot/shaders`` so they get syntax highlighting, diff cleanly
and can be baked/tested independently of the Python generators. ``render`` refuses to return
a source with an unresolved placeholder: a missing substitution used to surface only as a
cryptic qsb parse error.
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib.resources import files

__all__ = ["render", "shader_source"]

_INCLUDE = re.compile(r"@INCLUDE:([A-Za-z0-9_./-]+)@")

_ROOT = files("qmlmathplot.shaders")
_PLACEHOLDER = re.compile(r"@([A-Z][A-Z0-9_]*)@")


@lru_cache(maxsize=None)
def shader_source(name: str) -> str:
    """The raw shader text (cached; files are read once per process)."""
    return (_ROOT / name).read_text(encoding="utf-8")


def _expand_includes(text: str, stack: tuple[str, ...] = ()) -> str:
    """Expand ``@INCLUDE:path@`` recursively (small common GLSL blocks)."""

    def replace(match: re.Match[str]) -> str:
        included = match.group(1)
        if included in stack:  # pragma: no cover - defensive
            chain = " -> ".join((*stack, included))
            raise ValueError(f"shader include cycle: {chain}")
        return _expand_includes(shader_source(included).rstrip("\n"), (*stack, included))

    return _INCLUDE.sub(replace, text)


def render(name: str, /, **values: object) -> str:
    """Expand includes, substitute ``@NAME@`` placeholders, and fail if any remain."""
    text = _expand_includes(shader_source(name), (name,))
    if "@INCLUDE:" in text:
        raise ValueError(f"{name}: unresolved shader include")
    for key, value in values.items():
        text = text.replace(f"@{key}@", str(value))
    leftover = sorted(set(_PLACEHOLDER.findall(text)))
    if leftover:
        raise ValueError(f"{name}: unresolved shader placeholders {leftover}")
    return text
