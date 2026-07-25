"""The optional MLflow logger writes to a SQLite backend when MLflow is present."""

from __future__ import annotations

import pytest

pytest.importorskip("mlflow")

from tracegym.gate.gate import GateResult  # noqa: E402
from tracegym.gate.mlflow_log import log_run  # noqa: E402


def test_mlflow_log_run_writes_to_sqlite(tmp_path):
    db = tmp_path / "mlflow.db"
    ok = log_run(
        GateResult(verdict="PASS", mean_delta=0.05, ci_low=-0.01, ci_high=0.11),
        "run-x",
        tracking_uri=f"sqlite:///{db}",
        scorecard={"task_success_rate": 1.0, "invariant_failures": 0},
    )
    assert ok is True
    assert db.exists()
