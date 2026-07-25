"""Database side of the gate: pull run vectors, promote baselines, gate a run.

The reference a run is gated against is just another run, so the same code powers
"vs the promoted baseline", "vs a previous version", and "vs a deterministic
oracle" (the benchmark-vs-reference story). Only the reference selection differs.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tracegym.config import GateConfig
from tracegym.gate.gate import GateResult, gate_verdict


def _now() -> str:
    return datetime.now(UTC).isoformat()


def run_vectors(conn, run_id: str) -> tuple[dict, dict, float]:
    """Return (scores, invariant_fails, total_cost) keyed by case id for a run."""
    rows = conn.execute(
        "SELECT case_id, score, l1_invariant_fail, cost_usd FROM results WHERE run_id = ?",
        (run_id,),
    ).fetchall()
    scores = {r["case_id"]: r["score"] for r in rows}
    invariant = {r["case_id"]: r["l1_invariant_fail"] for r in rows}
    cost = float(sum(r["cost_usd"] for r in rows))
    return scores, invariant, cost


def gate_runs(
    conn,
    candidate_run_id: str,
    reference_run_id: str,
    cfg: GateConfig | None = None,
    seed: int = 1729,
) -> GateResult:
    """Gate a candidate run against any reference run."""
    cs, ci, cc = run_vectors(conn, candidate_run_id)
    rs, ri, rc = run_vectors(conn, reference_run_id)
    return gate_verdict(
        cs,
        rs,
        cand_invariant_fails=ci,
        base_invariant_fails=ri,
        cand_cost=cc,
        base_cost=rc,
        cfg=cfg,
        seed=seed,
    )


def promote(conn, name: str, suite_id: str, run_id: str, note: str = "") -> None:
    """Mark a run as the named baseline the gate compares against."""
    conn.execute(
        "INSERT OR REPLACE INTO baselines (name, suite_id, run_id, promoted_at, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, suite_id, run_id, _now(), note),
    )
    conn.commit()


def baseline_run_id(conn, name: str = "baseline") -> str | None:
    row = conn.execute("SELECT run_id FROM baselines WHERE name = ?", (name,)).fetchone()
    return row["run_id"] if row else None


def gate_against_baseline(
    conn,
    candidate_run_id: str,
    name: str = "baseline",
    cfg: GateConfig | None = None,
    seed: int = 1729,
) -> GateResult:
    """Gate a candidate against the promoted baseline. Raises if none is promoted."""
    ref = baseline_run_id(conn, name)
    if ref is None:
        raise ValueError(f"no baseline named {name!r} has been promoted")
    return gate_runs(conn, candidate_run_id, ref, cfg, seed)
