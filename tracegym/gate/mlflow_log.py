"""Optional MLflow logging for eval runs.

Off by default and guarded: if MLflow is not installed or not requested, this is a
no-op and the keyless demo is untouched. When enabled it logs each run's gate
result and scorecard to a local SQLite tracking store, turning eval runs into
tracked experiments you can compare over time. SQLite is the backend recent MLflow
recommends; the file store is deprecated.
"""

from __future__ import annotations

from tracegym.gate.gate import GateResult


def log_run(
    result: GateResult,
    run_id: str,
    *,
    scorecard: dict | None = None,
    tracking_uri: str = "sqlite:///mlflow.db",
    experiment: str = "tracegym",
) -> bool:
    """Log one gated run to MLflow. Returns False (no-op) if MLflow is unavailable."""
    try:
        import mlflow
    except ImportError:
        return False

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_id):
        mlflow.log_param("verdict", result.verdict)
        mlflow.log_param("run_id", run_id)
        mlflow.log_metric("mean_delta", result.mean_delta)
        mlflow.log_metric("ci_low", result.ci_low)
        mlflow.log_metric("ci_high", result.ci_high)
        mlflow.log_metric("cost_delta_pct", result.cost_delta_pct)
        mlflow.log_metric("new_invariant_fails", result.new_invariant_fails)
        mlflow.log_metric("blocked", 1 if result.blocked else 0)
        for key, value in (scorecard or {}).items():
            if isinstance(value, (int, float)):
                mlflow.log_metric(f"scorecard.{key}", value)
    return True
