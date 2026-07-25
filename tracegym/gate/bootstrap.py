"""Paired bootstrap for the significance of a per-case score change.

We resample the per-case deltas (candidate minus baseline) with replacement many
times and read the 2.5th and 97.5th percentiles of the resampled means. The seed
is fixed so the confidence interval is byte-identical across reruns, which is what
lets the advisor reuse the gate as a deterministic oracle.
"""

from __future__ import annotations

import numpy as np


def paired_bootstrap(
    deltas: list[float], samples: int = 10000, seed: int = 1729
) -> tuple[float, float, float]:
    """Return (mean, ci_low, ci_high) for the per-case deltas at 95%."""
    arr = np.asarray(deltas, dtype=float)
    n = arr.size
    if n == 0:
        return 0.0, 0.0, 0.0
    rng = np.random.default_rng(seed)
    resampled = arr[rng.integers(0, n, size=(samples, n))].mean(axis=1)
    lo, hi = np.percentile(resampled, [2.5, 97.5])
    return float(arr.mean()), float(lo), float(hi)
