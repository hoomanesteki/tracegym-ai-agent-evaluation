"""Regression gate: a pure verdict predicate plus database and MLflow adapters."""

from tracegym.gate.bootstrap import paired_bootstrap
from tracegym.gate.gate import GateResult, gate_verdict
from tracegym.gate.runs import (
    baseline_run_id,
    gate_against_baseline,
    gate_runs,
    promote,
    run_vectors,
)

__all__ = [
    "gate_verdict",
    "GateResult",
    "paired_bootstrap",
    "gate_runs",
    "gate_against_baseline",
    "promote",
    "baseline_run_id",
    "run_vectors",
]
