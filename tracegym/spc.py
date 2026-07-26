"""Statistical process control over the run history: catch drift, never block.

The gate catches a step change against a pinned baseline. Drift is the other
failure mode: a slow slide across many runs that no single pairwise comparison
would flag. These are the textbook control charts for exactly that, computed with
numpy only and kept deterministic.

Everything here feeds the human-notify tier. A drift signal asks a person to look;
it never blocks a merge on its own. Two detectors run together: an EWMA chart for
"the process is out of control right now" and a tabular CUSUM (h=4 sigma, k=0.5
sigma, the standard tuning) for "a sustained shift has happened". CUSUM is
deliberately deaf to a short transient that recovers, which is what the gate
already handles pairwise, and it reports the most likely change point either way.
"""

from __future__ import annotations

import numpy as np


def ewma(values, lam: float = 0.3, *, start: float | None = None):
    """Exponentially weighted moving average: z_t = lam*x_t + (1-lam)*z_{t-1}."""
    v = np.asarray(values, dtype=float)
    z = np.empty(len(v))
    prev = float(v[0]) if start is None else float(start)
    for i in range(len(v)):
        prev = lam * float(v[i]) + (1 - lam) * prev
        z[i] = prev
    return z


def cusum(values, *, target: float, k: float):
    """Tabular CUSUM. Returns (hi, lo): cumulative upward and downward deviations."""
    v = np.asarray(values, dtype=float)
    hi = np.zeros(len(v))
    lo = np.zeros(len(v))
    sh = sl = 0.0
    for i in range(len(v)):
        sh = max(0.0, sh + (float(v[i]) - target) - k)
        sl = max(0.0, sl + (target - float(v[i])) - k)
        hi[i], lo[i] = sh, sl
    return hi, lo


def change_point(values) -> int | None:
    """Most likely single change point: argmax of |cumulative deviation from mean|."""
    v = np.asarray(values, dtype=float)
    if len(v) < 2:
        return None
    s = np.cumsum(v - v.mean())
    return int(np.argmax(np.abs(s)))


def _scale(values) -> float:
    """Short-term sigma via the moving range: mean|x_t - x_{t-1}| / 1.128.

    This is the individuals-chart (I-MR) estimator. It reads run-to-run noise, not
    the total spread, so a slow trend does not inflate it and hide itself. 1.128 is
    the d2 unbiasing constant for a moving range of two points.
    """
    v = np.asarray(values, dtype=float)
    if len(v) < 2:
        return 0.0
    return float(np.abs(np.diff(v)).mean()) / 1.128


def drift_check(
    values,
    direction: str = "up",
    *,
    min_samples: int = 8,
    lam: float = 0.3,
    ewma_L: float = 2.7,
    cusum_k: float = 0.5,
    cusum_h: float = 4.0,
) -> dict:
    """Human-notify drift verdict for a metric series. Never blocks.

    direction is "up" when higher is better (adverse drift is downward) or "down"
    otherwise. Needs at least min_samples runs. Returns a dict whose status is one
    of insufficient | flat | stable | recovered | drift:

      drift      the latest point is out of control now (ongoing, actionable),
      recovered  a sustained excursion happened but the process is back in control,
      stable     no signal.

    Only "drift" is meant to route to a person; "recovered" is context (with the
    change point) for the run that already tripped the gate pairwise.
    """
    v = np.asarray(values, dtype=float)
    n = len(v)
    if n < min_samples:
        return {"status": "insufficient", "drift": False, "n": n, "min_samples": min_samples}

    center = float(np.median(v))
    sigma = _scale(v)
    if sigma <= 1e-12:
        return {"status": "flat", "drift": False, "n": n, "center": round(center, 6)}

    adverse_down = direction == "up"  # an "up" metric drifts adversely when it falls

    # EWMA: is the latest point out of control in the adverse direction?
    z = ewma(v, lam, start=center)
    spread = ewma_L * sigma * float(np.sqrt(lam / (2 - lam)))
    latest_out = bool(z[-1] < center - spread) if adverse_down else bool(z[-1] > center + spread)

    # CUSUM: has a sustained shift accumulated past the decision interval anywhere?
    hi, lo = cusum(v, target=center, k=cusum_k * sigma)
    adverse_cusum = lo if adverse_down else hi
    signal_idx = np.nonzero(adverse_cusum > cusum_h * sigma)[0]
    excursion = bool(len(signal_idx) > 0)

    status = "drift" if latest_out else ("recovered" if excursion else "stable")
    return {
        "status": status,
        "drift": latest_out,
        "excursion": excursion,
        "latest_out": latest_out,
        "n": n,
        "center": round(center, 6),
        "sigma": round(sigma, 6),
        "latest": round(float(v[-1]), 6),
        "change_point": change_point(v),
        "first_signal": int(signal_idx[0]) if excursion else None,
        "direction": direction,
    }
