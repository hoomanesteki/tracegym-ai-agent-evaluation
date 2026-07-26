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


def _config_sha(conn, run_id: str) -> str | None:
    row = conn.execute("SELECT config_sha FROM runs WHERE id = ?", (run_id,)).fetchone()
    return row["config_sha"] if row else None


def _output_hashes(conn, run_id: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT case_id, output_sha FROM results WHERE run_id = ?", (run_id,)
    ).fetchall()
    return {r["case_id"]: r["output_sha"] for r in rows}


def _churn_cases(conn, candidate_run_id: str, reference_run_id: str) -> list[str]:
    """Cases whose output changed between two runs that declared the same config.

    Only meaningful when both runs pin a non-null, equal config_sha: that is a
    promise the replays are identical, so any differing output hash is
    nondeterministic churn, not a real change. Returns [] unless that holds.
    """
    cand_cfg = _config_sha(conn, candidate_run_id)
    ref_cfg = _config_sha(conn, reference_run_id)
    if not cand_cfg or cand_cfg != ref_cfg:
        return []
    ch = _output_hashes(conn, candidate_run_id)
    rh = _output_hashes(conn, reference_run_id)
    return sorted(c for c in ch.keys() & rh.keys() if ch[c] != rh[c])


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
    result = gate_verdict(
        cs,
        rs,
        cand_invariant_fails=ci,
        base_invariant_fails=ri,
        cand_cost=cc,
        base_cost=rc,
        cfg=cfg,
        seed=seed,
    )
    churn = _churn_cases(conn, candidate_run_id, reference_run_id)
    if churn:
        result.churn_cases = churn
        result.reasons.append(
            f"{len(churn)} case(s) changed output under an identical config_sha "
            f"(nondeterministic churn): {', '.join(churn[:5])}"
        )
        result.verdict = "BLOCK"
    return result


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
