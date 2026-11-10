# SPDX-FileCopyrightText: 2007-2026 GeoGebra GmbH
# SPDX-License-Identifier: GPL-3.0-only
# Portions derived from GeoGebra (EUPL-1.2); see THIRD_PARTY_NOTICES.md.
"""GGB's adaptive CurvePlotter, ported for 1-D functions.

This is the semantic core of ``org.geogebra.common.euclidian.plot.CurvePlotter`` /
``CurveSegmentPlotter`` for a ``GeoFunction``. It produces the same world-space path
operations (MOVE_TO / LINE_TO / END) as GGB; the caller maps them to screen pixels and
strokes them.

Coordinates are world coordinates, as in ``GeoFunction.evaluateCurve``. The view supplies
only the screen scales and bounds used by the bisection criteria.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotations only; the runtime takes program sources
    import sympy as sp

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .program import compile_function

__all__ = ["Gap", "PathRecorder", "FunctionCurve", "PlotView", "plot_curve"]

MAX_PIXEL_DISTANCE = 10.0
MAX_ANGLE = math.radians(10.0)
MAX_ANGLE_OFF_SCREEN = math.radians(45.0)
MAX_BEND = math.tan(MAX_ANGLE)
MAX_BEND_OFF_SCREEN = math.tan(MAX_ANGLE_OFF_SCREEN)
MAX_DEFINED_BISECTIONS = 16
MAX_PROBLEM_BISECTIONS = 8
MAX_CONTINUITY_BISECTIONS = 8
MAX_JUMP = 5.0
MAX_ZERO_COUNT = 1000
MIN_SAMPLE_POINTS = 80
CLIP_DISTANCE = 5.0
LENGTH = MAX_DEFINED_BISECTIONS + 1
ITER_MAX = 100


class Gap(Enum):
    MOVE_TO = "move_to"
    CORNER = "corner"


def _undefined(values: list[float] | tuple[float, ...] | None) -> bool:
    if values is None:
        return True
    return any(math.isnan(v) or math.isinf(v) for v in values)


def _value_undefined(value: float) -> bool:
    return math.isnan(value) or math.isinf(value)


class PathRecorder:
    """A ``PathPlotter`` recorder in world coordinates."""

    def __init__(self) -> None:
        self.operations: list[tuple[Any, ...]] = []

    def first_point(self, pos: list[float], gap: Gap) -> None:
        if gap is Gap.MOVE_TO:
            self.move_to(pos)
        else:
            self.line_to(pos)

    def move_to(self, pos: list[float]) -> None:
        self.operations.append(("MOVE_TO", pos[0], pos[1]))

    def line_to(self, pos: list[float]) -> None:
        self.operations.append(("LINE_TO", pos[0], pos[1]))

    def corner(self, pos: list[float] | None = None) -> None:
        self.operations.append(("CORNER",) if pos is None else ("CORNER", pos[0], pos[1]))

    def draw_to(self, pos: list[float], segment_type: Any) -> None:
        self.line_to(pos)

    def end_plot(self) -> None:
        self.operations.append(("END",))

    def new_double_array(self) -> list[float]:
        return [0.0, 0.0]

    def copy_coords(self, *args: Any) -> bool:
        return False

    def supports(self, *args: Any) -> bool:
        return True


@dataclass
class PlotView:
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    width: int
    height: int

    @property
    def xscale(self) -> float:
        return self.width / (self.xmax - self.xmin)

    @property
    def yscale(self) -> float:
        return self.height / (self.ymax - self.ymin)

    def is_on_view(self, coords: list[float]) -> bool:
        return (self.xmin <= coords[0] <= self.xmax
                and self.ymin <= coords[1] <= self.ymax)

    def on_screen_diff(self, p1: list[float], p2: list[float]) -> list[float]:
        return [(p2[0] - p1[0]) * self.xscale, (p2[1] - p1[1]) * self.yscale]

    def is_segment_off_view(self, p1: list[float], p2: list[float]) -> bool:
        tolerance = CLIP_DISTANCE / self.yscale
        if self.ymin > p1[1] + tolerance and self.ymin > p2[1] + tolerance:
            return True
        if p1[1] > self.ymax + tolerance and p2[1] > self.ymax + tolerance:
            return True
        tolerance = CLIP_DISTANCE / self.xscale
        if self.xmin > p1[0] + tolerance and self.xmin > p2[0] + tolerance:
            return True
        return p1[0] > self.xmax + tolerance and p2[0] > self.xmax + tolerance


def compile_source(source: str, name: str = "f") -> Callable[[float], Any]:
    """Compile a calculator ``Program.math`` definition into a scalar callable."""
    return compile_function(source, name)


class FunctionCurve:
    """The subset of ``CurveEvaluable`` used by ``CurveSegmentPlotter``."""

    def __init__(self, expression: str | Callable[[float], Any] | sp.Expr) -> None:
        """A calculator program source, a callable, or (legacy/tests) a sympy expression."""
        self.expression = expression
        if isinstance(expression, str):
            self._function = compile_source(expression)
        elif callable(expression):
            self._function = expression
        else:
            # Legacy path: the tests and the GGB oracle still hand in sympy expressions.
            import sympy as sp

            # math plus the few GGB names math lacks; avoids a runtime numpy dependency.
            local = {
                "sign": lambda value: 0 if value == 0 else (1 if value > 0 else -1),
                "Abs": abs,
                "sec": lambda value: 1 / math.cos(value),
                "csc": lambda value: 1 / math.sin(value),
                "cot": lambda value: 1 / math.tan(value),
            }
            self._function = sp.lambdify(
                sp.Symbol("x"), expression, modules=[math, local]
            )
        self.min_dist_x = 0.1
        self.is_function_in_x = True

    def evaluate_curve(self, t: float, out: list[float]) -> None:
        out[0] = t
        try:
            value = float(self._function(t))
        except (ArithmeticError, ValueError, TypeError, ZeroDivisionError, OverflowError):
            value = math.nan
        out[1] = value

    def new_double_array(self) -> list[float]:
        return [0.0, 0.0]

    def value(self, t: float) -> float:
        try:
            return float(self._function(t))
        except (ArithmeticError, ValueError, TypeError, ZeroDivisionError, OverflowError):
            return math.nan

    @staticmethod
    def distance_max(p1: list[float], p2: list[float]) -> float:
        return max(abs(p1[0] - p2[0]), abs(p1[1] - p2[1]))

    def defined_interval(self, a: float, b: float) -> list[float]:
        """Port of ``RealRootUtil.getDefinedInterval``."""
        a_defined = not _value_undefined(self.value(a))
        b_defined = not _value_undefined(self.value(b))
        if a_defined and b_defined:
            return [a, b]
        if not a_defined and not b_defined:
            middle = (a + b) * 0.5
            if _value_undefined(self.value(middle)):
                return [math.nan, math.nan]
            return [self._definition_border(a, middle), self._definition_border(middle, b)]
        if not a_defined:
            return [self._definition_border(a, b), b]
        return [a, self._definition_border(a, b)]

    def _definition_border(self, a: float, b: float) -> float:
        left, right = a, b
        left_defined = False
        for _ in range(ITER_MAX):
            if left == right:
                break
            left_value = self.value(left)
            right_value = self.value(right)
            left_defined = not _value_undefined(left_value)
            right_defined = not _value_undefined(right_value)
            if left_defined == right_defined:
                return math.nan
            middle = (left + right) * 0.5
            middle_defined = not _value_undefined(self.value(middle))
            if middle_defined == left_defined:
                left = middle
            else:
                right = middle
        return left if left_defined else right


class CurveSegmentInfo:
    def __init__(self, view: PlotView) -> None:
        self.view = view
        self.distance_ok = True
        self.angle_ok = True
        self.off_screen = False
        self.reached_min_step = False

    def update(self, left: list[float], right: list[float],
               diff: list[float], prev_diff: list[float], curve: FunctionCurve) -> None:
        self.off_screen = self.view.is_segment_off_view(left, right)
        self.reached_min_step = abs(diff[0]) < curve.min_dist_x
        self.distance_ok = self.off_screen or all(
            abs(value) <= MAX_PIXEL_DISTANCE for value in diff
        )
        bend = MAX_BEND_OFF_SCREEN if self.off_screen else MAX_BEND
        self.angle_ok = self._angle_ok(prev_diff, diff, bend)

    def is_off_screen(self) -> bool:
        return self.off_screen

    def is_distance_or_angle_invalid(self) -> bool:
        return not self.angle_ok or not self.distance_ok

    def has_not_reached_min_step(self) -> bool:
        return not self.reached_min_step

    @staticmethod
    def _angle_ok(v: list[float], w: list[float], bend: float) -> bool:
        inner = v[0] * w[0] + v[1] * w[1]
        if _value_undefined(inner):
            return True
        if inner <= 0:
            return False
        det = abs(v[0] * w[1] - v[1] * w[0])
        return det < bend * inner


class SegmentParams:
    def __init__(self, t_min: float, divisors: list[float], view: PlotView) -> None:
        self.t_min = t_min
        self.divisors = divisors
        self.view = view
        self.dyad = 1
        self.depth = 0
        self.t = t_min
        self.left = t_min
        self.count_diff_zeros = 0
        self.diff: list[float] = []
        self.prev_diff: list[float] = []

    def has_not_reached_max_depth(self) -> bool:
        return self.depth < MAX_DEFINED_BISECTIONS

    def is_diff_zeros_limit_not_reached(self) -> bool:
        return self.count_diff_zeros < MAX_ZERO_COUNT

    def is_step_too_big(self, max_param_step: float) -> bool:
        return self.current_divisor() > max_param_step

    def current_divisor(self) -> float:
        return self.divisors[self.depth]

    def progress(self) -> None:
        self.dyad = 2 * self.dyad - 1
        self.depth += 1
        self.update_t()

    def update_t(self) -> None:
        self.t = self.t_min + self.dyad * self.current_divisor()

    def update_diff(self, left: list[float], right: list[float]) -> None:
        self.diff = self.view.on_screen_diff(left, right)

    def update_previous_diff(self) -> None:
        self.prev_diff = list(self.diff)

    def restore_from_stack(self, item: "StackItem") -> None:
        self.depth = item.depth + 1
        self.dyad = item.dyadic * 2
        self.update_previous_diff()
        self.update_t()


@dataclass
class StackItem:
    dyadic: int = 1
    depth: int = 0
    on_screen: bool = False
    evaluation: list[float] | None = None


class CurvePlotterStack:
    def __init__(self, length: int, on_screen: bool, evaluation: list[float]) -> None:
        self.items = [StackItem() for _ in range(length)]
        self.items[0] = StackItem(1, 0, on_screen, list(evaluation))
        self.top = 1

    def push(self, dyadic: int, depth: int, on_screen: bool, evaluation: list[float]) -> None:
        self.items[self.top] = StackItem(dyadic, depth, on_screen, list(evaluation))
        self.top += 1

    def pop(self) -> StackItem:
        self.top -= 1
        return self.items[self.top] if self.top >= 0 else StackItem()

    def has_items(self) -> bool:
        return self.top != 0


class CurveSegmentPlotter:
    def __init__(self, curve: FunctionCurve, t_min: float, t_max: float,
                 interval_depth: int, max_param_step: float, view: PlotView,
                 gp: PathRecorder, calc_label: bool, move_to_allowed: Gap) -> None:
        self.curve = curve
        self.t_min = t_min
        self.t_max = t_max
        self.interval_depth = interval_depth
        self.max_param_step = max_param_step
        self.view = view
        self.gp = gp
        self.need_label = calc_label
        self.move_to_allowed = move_to_allowed
        self.label_point = None
        self.move: list[float] = []
        self.next_line_needs_move_to = False
        self.eval: list[float] = curve.new_double_array() if hasattr(curve, "new_double_array") else [0.0, 0.0]
        self.eval_left: list[float] = []
        self.eval_right: list[float] = []
        self.info = CurveSegmentInfo(view)
        self.params: SegmentParams | None = None
        self.stack: CurvePlotterStack | None = None
        self.on_screen = False
        self.divisors: list[float] = []
        if self._start():
            self._plot()

    def _start(self) -> bool:
        self.curve.evaluate_curve(self.t_min, self.eval)
        if _undefined(self.eval):
            self._plot_problem_interval(self.t_min)
            return False
        self.eval_left = list(self.eval)
        self.curve.evaluate_curve(self.t_max, self.eval)
        if _undefined(self.eval):
            self._plot_problem_interval(self.t_min)
            return False
        self.on_screen = self.view.is_on_view(self.eval)
        self.eval_right = list(self.eval)
        self.gp.first_point(self.eval_left, self.move_to_allowed)
        self._create_stack()
        self._create_divisors()
        self.params = SegmentParams(self.t_min, self.divisors, self.view)
        self.params.diff = self.view.on_screen_diff(self.eval_left, self.eval_right)
        self.curve.evaluate_curve(self.t_min + self.divisors[-1], self.eval)
        self.params.prev_diff = self.view.on_screen_diff(self.eval_left, self.eval)
        return True

    def _create_stack(self) -> None:
        self.stack = CurvePlotterStack(LENGTH, self.on_screen, self.eval_right)

    def _create_divisors(self) -> None:
        self.divisors = [0.0] * LENGTH
        self.divisors[0] = self.t_max - self.t_min
        for index in range(1, LENGTH):
            self.divisors[index] = self.divisors[index - 1] / 2.0

    def _plot(self) -> None:
        if self._plot_bisector_algo():
            self._plot_problem_interval(self.params.left if self.params else self.t_min)

    def _plot_bisector_algo(self) -> bool:
        params = self.params
        stack = self.stack
        assert params is not None and stack is not None
        while True:
            self.info.update(self.eval_left, self.eval_right, params.diff,
                             params.prev_diff, self.curve)
            while (params.has_not_reached_max_depth()
                   and self.info.has_not_reached_min_step()
                   and (self.info.is_distance_or_angle_invalid()
                        or params.is_step_too_big(self.max_param_step))
                   and params.is_diff_zeros_limit_not_reached()):
                stack.push(params.dyad, params.depth, self.on_screen, self.eval_right)
                params.progress()
                self.curve.evaluate_curve(params.t, self.eval)
                self.on_screen = self.view.is_on_view(self.eval)
                if _undefined(self.eval) and self._has_no_singularity(
                        params.t, self.divisors[LENGTH - 1]):
                    return True
                self.eval_right = list(self.eval)
                params.update_diff(self.eval_left, self.eval_right)
                params.count_diff_zeros = (params.count_diff_zeros + 1
                                           if self._is_diff_zero(params.diff) else 0)
                self.info.update(self.eval_left, self.eval_right, params.diff,
                                 params.prev_diff, self.curve)
            self._draw_segment(params.t, params.left, self.info)
            self.eval_left = list(self.eval_right)
            params.left = params.t
            item = stack.pop()
            self.on_screen = item.on_screen
            self.eval_right = list(item.evaluation) if item.evaluation else []
            params.restore_from_stack(item)
            params.update_diff(self.eval_left, self.eval_right)
            if not stack.has_items():
                break
        self.gp.end_plot()
        return False

    def _draw_segment(self, t: float, left: float, info: CurveSegmentInfo) -> None:
        if self._is_line_to(t, left, info):
            if self.next_line_needs_move_to:
                self.gp.move_to(self.move)
                self.next_line_needs_move_to = False
            self.gp.line_to(self.eval_right)
        else:
            self.move = list(self.eval_right)
            self.next_line_needs_move_to = True

    def _is_line_to(self, t: float, left: float, info: CurveSegmentInfo) -> bool:
        if self.move_to_allowed is Gap.MOVE_TO:
            if info.is_off_screen():
                return False
            if info.is_distance_or_angle_invalid():
                return self.is_continuous(self.curve, left, t, MAX_CONTINUITY_BISECTIONS)
        return True

    @staticmethod
    def _is_diff_zero(diff: list[float]) -> bool:
        return abs(diff[0]) < 1e-15 and abs(diff[1]) < 1e-15

    def _has_no_singularity(self, t: float, interval: float) -> bool:
        return not self.is_continuous_around(self.curve, t, interval, self.view, self.eval)

    @staticmethod
    def is_continuous_around(curve: FunctionCurve, t: float, eps: float,
                             view: PlotView, eval_t: list[float]) -> bool:
        evaluation = [0.0, 0.0]
        curve.evaluate_curve(t + eps, evaluation)
        old_y = evaluation[1]
        if not _undefined(evaluation):
            curve.evaluate_curve(t - eps, evaluation)
            if not _undefined(evaluation):
                if (curve.is_function_in_x
                        and abs(old_y - evaluation[1]) * view.yscale < MAX_JUMP):
                    eval_t[1] = (old_y + evaluation[1]) * 0.5
                    return True
                return not curve.is_function_in_x
        return False

    @staticmethod
    def is_continuous(curve: FunctionCurve, start: float, end: float,
                      max_iterations: int) -> bool:
        t_min, t_max = start, end
        if t_min == t_max:
            return True
        left = [0.0, 0.0]
        curve.evaluate_curve(t_min, left)
        if _undefined(left):
            return False
        right = [0.0, 0.0]
        curve.evaluate_curve(t_max, right)
        if _undefined(right):
            return False
        initial = max(abs(left[0] - right[0]), abs(left[1] - right[1]))
        eps = initial * 0.9
        dist = math.inf
        iterations = 0
        middle = [0.0, 0.0]
        while iterations < max_iterations and dist > eps:
            iterations += 1
            m = (t_min + t_max) * 0.5
            curve.evaluate_curve(m, middle)
            dist_left = curve.distance_max(left, middle)
            dist_right = curve.distance_max(right, middle)
            if dist_left > dist_right:
                dist = dist_left
                t_max = m
            else:
                dist = dist_right
                t_min = m
            if t_min == t_max:
                return True
        return dist <= eps

    @staticmethod
    def _get_defined_interval(curve: FunctionCurve, a: float, b: float) -> list[float]:
        a_defined = not _value_undefined(curve.value(a))
        b_defined = not _value_undefined(curve.value(b))
        if a_defined and b_defined:
            return [a, b]
        if a_defined or b_defined:
            interval = curve.defined_interval(a, b)
            return [a if _value_undefined(interval[0]) else interval[0],
                    b if _value_undefined(interval[1]) else interval[1]]
        return [a, b]

    def _plot_problem_interval(self, left: float) -> None:
        if self.interval_depth > MAX_PROBLEM_BISECTIONS or left == self.t_max:
            return
        split = (left + self.t_max) / 2.0
        calc = self.need_label
        if abs(left - split) > self.max_param_step:
            calc = calc and self.label_point is None
            minimum = CurveSegmentPlotter(self.curve, left, split, self.interval_depth + 1,
                                          self.max_param_step, self.view, self.gp, calc,
                                          self.move_to_allowed)
            label_min = minimum.label_point
            calc = calc and label_min is None
            maximum = CurveSegmentPlotter(self.curve, split, self.t_max, self.interval_depth + 1,
                                          self.max_param_step, self.view, self.gp, calc,
                                          self.move_to_allowed)
            label_max = maximum.label_point
        else:
            borders = self._get_defined_interval(self.curve, left, split)
            calc = calc and self.label_point is None
            minimum = CurveSegmentPlotter(self.curve, borders[0], borders[1],
                                          self.interval_depth + 1, self.max_param_step,
                                          self.view, self.gp, calc, self.move_to_allowed)
            label_min = minimum.label_point
            borders = self._get_defined_interval(self.curve, split, self.t_max)
            calc = calc and self.label_point is None
            maximum = CurveSegmentPlotter(self.curve, borders[0], borders[1],
                                          self.interval_depth + 1, self.max_param_step,
                                          self.view, self.gp, calc, self.move_to_allowed)
            label_max = maximum.label_point
        if self.label_point is None:
            self.label_point = label_min if label_min is not None else label_max


class CurvePlotter:
    def __init__(self, curve: FunctionCurve, t_min: float, t_max: float, view: PlotView,
                 gp: PathRecorder, calc_label: bool = False,
                 move_to_allowed: Gap = Gap.MOVE_TO) -> None:
        min_sample_points = max(MIN_SAMPLE_POINTS, view.width / 6.0)
        max_param_step = abs(t_max - t_min) / min_sample_points
        self.segment = CurveSegmentPlotter(curve, t_min, t_max, 0, max_param_step, view,
                                           gp, calc_label, move_to_allowed)
        if move_to_allowed is Gap.CORNER:
            gp.corner()


def plot_curve(expression: str | Callable[[float], Any] | sp.Expr, view: PlotView,
               t_min: float, t_max: float,
               gap: Gap = Gap.MOVE_TO) -> list[tuple[Any, ...]]:
    recorder = PathRecorder()
    CurvePlotter(FunctionCurve(expression), t_min, t_max, view, recorder, False, gap)
    return recorder.operations


def stroke_vertices(operations: list[tuple[Any, ...]], view: PlotView,
                    size: tuple[float, float], line_width: float,
                    color: tuple[float, float, float, float]) -> tuple[bytes, int]:
    """World path operations -> screen-space stroke quads for the RHI path pipeline.

    The path pipeline's vertex layout is (ndc.xy, rgba premultiplied, local.xy): segment
    quads carry local (0, 0); join/cap discs carry local (+-1, +-1) and are clipped by
    path.frag. This mirrors the compute path's ``slot``/``join`` stroke.
    """
    import struct

    width, height = size
    if width <= 0 or height <= 0 or view.xmax == view.xmin or view.ymax == view.ymin:
        return b"", 0
    r, g, b, a = color
    half = max(0.01, line_width * 0.5)
    data = bytearray()

    def screen(point: tuple[float, float]) -> tuple[float, float]:
        return ((point[0] - view.xmin) / (view.xmax - view.xmin) * width,
                (view.ymax - point[1]) / (view.ymax - view.ymin) * height)

    def vertex(point: tuple[float, float], local: tuple[float, float]) -> None:
        sx, sy = point
        data.extend(struct.pack("<2f4f2f",
                                sx / width * 2.0 - 1.0,
                                1.0 - sy / height * 2.0,
                                r * a, g * a, b * a, a,
                                local[0], local[1]))

    def segment(start: tuple[float, float], end: tuple[float, float]) -> None:
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return
        nx, ny = -dy / length * half, dx / length * half
        a1 = (start[0] + nx, start[1] + ny)
        a2 = (start[0] - nx, start[1] - ny)
        b2 = (end[0] - nx, end[1] - ny)
        b1 = (end[0] + nx, end[1] + ny)
        for point in (a1, a2, b2, a1, b2, b1):
            vertex(point, (0.0, 0.0))

    def disc(point: tuple[float, float]) -> None:
        x, y = point
        for local in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0),
                      (-1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)):
            vertex((x + local[0] * half, y + local[1] * half), local)

    current: tuple[float, float] | None = None
    for operation in operations:
        kind = operation[0]
        if kind == "MOVE_TO":
            current = screen((operation[1], operation[2]))
            disc(current)
        elif kind == "LINE_TO":
            point = screen((operation[1], operation[2]))
            if current is not None:
                segment(current, point)
            disc(point)
            current = point
        elif kind == "END":
            current = None
    return bytes(data), len(data) // 32
