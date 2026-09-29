"""Emulate the vertex shader's float32 x-grid: where sin(1/x) aliases, and
which frames land exactly on x == 0.0 (the only y = NaN trigger)."""

import struct


def f32(v):
    return struct.unpack("f", struct.pack("f", v))[0]


N = 100_000
W = 900  # widget width px
H = 600
YMIN, YMAX = -2.0, 2.0


def grid(xmin, xmax, n=N):
    w = f32(xmax - xmin)
    x0 = f32(xmin)
    xs = []
    for i in range(n):
        t = f32(f32(i) / f32(n))
        xs.append(f32(x0 + f32(w * t)))
    return xs


def y_of(x):
    # sin(1/x) in float32; 1/0 -> inf, sin(inf) -> nan
    import math
    r = f32(1.0 / x) if x != 0.0 else float("inf")
    if math.isinf(r):
        return float("nan")
    return math.sin(r)


def stats(xs, label):
    w = xs[-1] - xs[0]
    dx = w / N
    zeros = sum(1 for v in xs if v == 0.0)
    # samples aliased: local period 2*pi*x^2 < 2*dx  ->  |x| < sqrt(dx/pi)
    lim = (dx / 3.141592653589793) ** 0.5
    aliased = sum(1 for v in xs if abs(v) < lim)
    ymin_px = H / (YMAX - YMIN)
    long_segments = 0
    px_total = 0
    prev = None
    for v in xs:
        y = y_of(v)
        if prev is not None and y == y and prev == prev:
            dy_px = abs(y - prev) * ymin_px
            if dy_px > 5:
                long_segments += 1
                px_total += min(dy_px, H)
            else:
                px_total += 1
        prev = y
    print(f"{label:34s} xMin={xs[0]:<12.6g} dx={dx:<10.3g} "
          f"exact_x0={zeros:<2d} aliased_vertices={aliased:<6d} "
          f"long_segments={long_segments:<6d} est_rasterized_px={px_total:,.0f}")


if __name__ == "__main__":
    print("sin(1/x) sampling geometry (float32, N=100000, 900x600 viewport)\n")
    stats(grid(-6.0, 6.0), "default view")
    for px in (1, 2, 3, 5):
        world = f32(f32(12.0 * px) / f32(W))
        stats(grid(f32(-6.0 - world), f32(6.0 - world)), f"panned {px}px right")
    stats(grid(-1e-3, 1e-3), "zoomed 1e-3")
    stats(grid(-1e-5, 1e-5), "zoomed 1e-5")
    stats(grid(-1e-7, 1e-7), "zoomed 1e-7")
    print()
    # does a *sequence* of pixel-pans hit x == 0.0 exactly?
    hits = 0
    xmin, xmax = -6.0, 6.0
    for k in range(400):
        world = f32(f32(12.0 * 3) / f32(W))  # 3px drag steps, like mouseMoveEvent
        xmin = f32(xmin - world)
        xmax = f32(xmax - world)
        if any(v == 0.0 for v in grid(xmin, xmax)):
            hits += 1
    print(f"frames with an exact x==0 vertex during a 400-frame 3px-pan drag: {hits}")
