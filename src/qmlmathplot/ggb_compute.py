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

from .interval import interval_glsl, interval_lib_for
from .shaderbuild import render, shader_source

__all__ = ["PATH_VERTEX", "PATH_FRAGMENT", "path_compute_source", "path_subsamples"]

PATH_VERTEX = shader_source("path.vert")

PATH_FRAGMENT = shader_source("path.frag")


def path_subsamples(expression: str | sp.Basic) -> int:
    # GGB FunctionSampler: one interval per screen column.
    return 1




def path_compute_source(expression: str | sp.Basic) -> str:
    expr = expression if isinstance(expression, sp.Basic) else sp.sympify(expression)
    intervals = interval_glsl(expr)
    return render(
        "interval_path.comp",
        SUBSAMPLES=path_subsamples(expression),
        INTERVAL_LIB=interval_lib_for(intervals),
        INTERVALS=intervals,
    )
