"""Inline-SVG chart geometry, computed in pure Python.

No dependencies, no external assets, no JavaScript: the report stays a single
self-contained file. This maps a list of per-run values to the coordinates a Jinja
template draws as an SVG polyline with gridlines, a last-point marker, and one
full-height hover column per run (the hit target) carrying a native <title>.
"""

from __future__ import annotations


def linechart(
    values: list[float],
    *,
    labels: list[str] | None = None,
    width: int = 280,
    height: int = 92,
    pad: int = 12,
) -> dict | None:
    """Return SVG geometry for a single-series line chart, or None if empty."""
    n = len(values)
    if n == 0:
        return None
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:  # flat series: give it a nominal band so the line centers
        lo -= 1.0
        hi += 1.0
    else:
        headroom = (hi - lo) * 0.08
        lo -= headroom
        hi += headroom

    def x(i: int) -> float:
        return pad + i * (width - 2 * pad) / max(n - 1, 1)

    def y(v: float) -> float:
        return height - pad - (v - lo) * (height - 2 * pad) / (hi - lo)

    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    grid = [{"y": round(pad + (height - 2 * pad) * k / 2, 1)} for k in range(3)]
    slot_w = (width - 2 * pad) / max(n, 1)
    slots = []
    for i, v in enumerate(values):
        label = labels[i] if labels else f"run {i + 1}"
        slots.append(
            {
                "x": round(x(i) - slot_w / 2, 1),
                "w": round(slot_w, 1),
                "cx": round(x(i), 1),
                "cy": round(y(v), 1),
                "title": f"{label}: {v:g}",
            }
        )
    return {
        "width": width,
        "height": height,
        "points": points,
        "grid": grid,
        "slots": slots,
        "last": {"x": round(x(n - 1), 1), "y": round(y(values[-1]), 1)},
    }
