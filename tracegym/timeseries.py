"""Read the runs log as a per-suite metric time series.

The runs table already records every run with a git_sha, created_at, and a summary
snapshot, so a trend is a plain read: no schema change, no keys, deterministic.
For a real project each CI run appends one frozen run and the series grows over
commits and days. The bundled demo rebuilds its workspace each time, so it seeds a
short illustrative history (mode='history'); this reader prefers that when present
and otherwise reads real frozen runs.
"""

from __future__ import annotations

import json

# metric key -> (label, "up" if higher is better else "down")
METRICS: dict[str, tuple[str, str]] = {
    "mean_score": ("mean score", "up"),
    "task_success_rate": ("task success", "up"),
    "cost_usd": ("cost $", "down"),
    "p95_latency_ms": ("p95 latency ms", "down"),
    "invariant_failures": ("invariant fails", "down"),
}


def run_history(conn, suite_id: str, *, limit: int = 30) -> list[dict]:
    """Ordered per-run metric snapshots for a suite (oldest to newest)."""
    has_history = conn.execute(
        "SELECT 1 FROM runs WHERE suite_id = ? AND mode = 'history' LIMIT 1", (suite_id,)
    ).fetchone()
    mode = "history" if has_history else "frozen"
    rows = conn.execute(
        "SELECT id, git_sha, created_at, summary FROM runs "
        "WHERE suite_id = ? AND mode = ? ORDER BY created_at ASC, id ASC",
        (suite_id, mode),
    ).fetchall()
    rows = rows[-limit:]
    series = []
    for r in rows:
        s = json.loads(r["summary"] or "{}")
        series.append(
            {
                "run_id": r["id"],
                "git_sha": (r["git_sha"] or "")[:7],
                "created_at": (r["created_at"] or "")[:10],
                "synthetic": mode == "history",
                "mean_score": float(s.get("mean_score", 0.0)),
                "task_success_rate": float(s.get("task_success_rate", 0.0)),
                "cost_usd": float(s.get("cost_usd", 0.0)),
                "p95_latency_ms": float(s.get("p95_latency_ms", 0.0)),
                "invariant_failures": int(s.get("invariant_failures", 0)),
            }
        )
    return series


def metric_series(history: list[dict], metric: str) -> list[float]:
    return [float(h.get(metric, 0.0)) for h in history]


def delta(history: list[dict], metric: str) -> float:
    """Change in a metric from the previous run to the latest (0 if under 2 points)."""
    vals = metric_series(history, metric)
    return round(vals[-1] - vals[-2], 6) if len(vals) >= 2 else 0.0
