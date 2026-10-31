# SPDX-FileCopyrightText: 2007-2026 GeoGebra GmbH
# SPDX-License-Identifier: GPL-3.0-only
# Portions derived from GeoGebra (EUPL-1.2); see THIRD_PARTY_NOTICES.md.
"""GLSL generation for the sequential GPU port of GGB's interval path.

One compute invocation walks the screen columns in order, evaluates ``FI`` (the interval of
the function on the column), and runs the same state machine as
:mod:`qmlmathplot.ggb_path`: ``IntervalPath`` (break/join), ``DrawInterval``
(joined/independent/whole), ``DrawInvertedInterval`` (gap rays) and ``JoinLines``
(neighbour-aware completion). The emitted segments are written into a storage buffer that a
tiny vertex/fragment pair draws; no CPU sampling is involved.

The neighbour state is taken from the previously evaluated column and a one-column prefetch,
so the whole pass pays one ``FI`` per column plus one. No scalar ``F`` evaluations are used:
the path is the interval's own endpoints, which is what GGB draws.
"""

from __future__ import annotations

import sympy as sp

from .shaderbuild import render, shader_source
from .interval import has_sign_pole, interval_glsl, interval_lib_for
from .model import _domain_is_pole_only, _non_negative, _pole_glsl, _pole_spacing

__all__ = ["PATH_VERTEX", "PATH_FRAGMENT", "path_compute_source", "path_subsamples"]

PATH_VERTEX = shader_source("path.vert")

PATH_FRAGMENT = shader_source("path.frag")


def path_subsamples(expression: str | sp.Basic) -> int:
    # GGB FunctionSampler: one interval per screen column.
    return 1




def path_compute_source(expression: str | sp.Basic) -> str:
    expr = expression if isinstance(expression, sp.Basic) else sp.sympify(expression)
    pole = has_sign_pole(expr)
    intervals = interval_glsl(expr)
    return render(
        "interval_path.comp",
        SUBSAMPLES=path_subsamples(expression),
        POLE=_pole_glsl(expr, "u") if pole else "1.0",
        HAS_POLE="1" if pole else "0",
        POLE_SPACING=f"{_pole_spacing(expr):.17g}",
        UNIFORM_FILL="1" if (
            pole and _non_negative(expr) and _domain_is_pole_only(expr)
        ) else "0",
        INTERVAL_LIB=interval_lib_for(intervals),
        INTERVALS=intervals,
    )
