"""Run-history series reader and the inline-SVG line-chart geometry."""

from __future__ import annotations

import json

from tracegym.report.charts import linechart
from tracegym.store import connect
from tracegym.timeseries import delta, metric_series, run_history


def _seed_history(conn, suite, scores):
    for i, s in enumerate(scores):
        conn.execute(
            "INSERT INTO runs (id, suite_id, mode, model, git_sha, created_at, summary) "
            "VALUES (?, ?, 'history', 'demo', ?, ?, ?)",
            (f"h{i}", suite, f"c{i}", f"2026-07-{10 + i:02d}", json.dumps({"mean_score": s})),
        )
    conn.commit()


def test_run_history_prefers_seeded_history():
    conn = connect()
    _seed_history(conn, "s", [1.0, 0.8, 1.0])
    h = run_history(conn, "s")
    assert len(h) == 3
    assert h[0]["synthetic"] is True
    assert metric_series(h, "mean_score") == [1.0, 0.8, 1.0]
    assert delta(h, "mean_score") == 0.2  # 1.0 - 0.8


def test_run_history_falls_back_to_real_frozen_runs():
    conn = connect()
    conn.execute(
        "INSERT INTO runs (id, suite_id, mode, created_at, summary) "
        "VALUES ('f1', 's', 'frozen', '2026-07-10', '{\"mean_score\": 0.9}')"
    )
    conn.commit()
    h = run_history(conn, "s")
    assert len(h) == 1
    assert h[0]["synthetic"] is False
    assert h[0]["mean_score"] == 0.9


def test_linechart_geometry_and_edge_cases():
    c = linechart([1.0, 2.0, 3.0], labels=["a", "b", "c"])
    assert len(c["points"].split()) == 3
    assert len(c["slots"]) == 3
    assert c["last"]["x"] > 0
    assert linechart([5.0, 5.0, 5.0]) is not None  # flat series does not divide by zero
    assert linechart([]) is None
