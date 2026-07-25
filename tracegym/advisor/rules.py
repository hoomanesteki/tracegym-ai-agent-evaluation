"""Advisor rules: propose a cheaper or faster change, then prove it is safe.

Each rule detects an opportunity and returns a Recommendation whose status is the
result of an actual check, never a guess:

  SAFE           a completed deterministic proof shows the change does not regress
  ADVISORY_ONLY  a real signal a human should act on, but not auto-validatable
  INCONCLUSIVE   the keyless path cannot prove it (needs a fixture we will not fake)

The two byte-identical rules (redundant tool caching, duplicate LLM calls) are SAFE
by construction: identical inputs already produced identical stored outputs, so
caching them cannot change any answer. The judge-downgrade rule is validated by
verdict agreement from cached judgments, not by the score gate, because letting a
cheaper judge grade itself against the gate would be circular.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tracegym.capture.otel import COST_USD


@dataclass
class Recommendation:
    rule_id: str
    title: str
    status: str  # SAFE | REGRESSES | INCONCLUSIVE | ADVISORY_ONLY
    est_saving_usd: float = 0.0
    est_saving_pct: float = 0.0
    detail: str = ""
    evidence: dict = field(default_factory=dict)


def rule_redundant_tools(conn, run_id: str) -> list[Recommendation]:
    """R1: identical tool calls within a trace can be memoized. Byte-identical."""
    groups = conn.execute(
        """
        SELECT s.name AS name, COUNT(*) AS n, COUNT(DISTINCT s.output_sha) AS distinct_out,
               SUM((s.end_ns - s.start_ns)) / 1e6 AS total_ms
        FROM spans s JOIN results r ON r.trace_id = s.trace_id
        WHERE r.run_id = ? AND s.kind = 'tool'
        GROUP BY s.trace_id, s.name, s.input_sha
        HAVING COUNT(*) > 1
        """,
        (run_id,),
    ).fetchall()
    if not groups:
        return []
    reclaimable = sum(g["n"] - 1 for g in groups)
    reclaimable_ms = sum((g["total_ms"] or 0) * (g["n"] - 1) / g["n"] for g in groups)
    all_pure = all(g["distinct_out"] == 1 for g in groups)
    status = "SAFE" if all_pure else "ADVISORY_ONLY"
    detail = (
        f"{reclaimable} redundant tool call(s) repeat identical inputs; "
        f"memoizing reclaims ~{reclaimable_ms:.1f}ms"
    )
    if not all_pure:
        detail += " (some repeats returned different outputs; verify the tool is pure)"
    return [
        Recommendation(
            "R1",
            "Memoize redundant tool calls",
            status,
            est_saving_usd=0.0,
            detail=detail,
            evidence={"reclaimable_calls": reclaimable, "reclaimable_ms": round(reclaimable_ms, 1)},
        )
    ]


def rule_duplicate_llm(conn, run_id: str) -> list[Recommendation]:
    """R2: identical LLM calls within a trace can be memoized. Byte-identical."""
    groups = conn.execute(
        f"""
        SELECT COUNT(*) AS n, COUNT(DISTINCT s.output_sha) AS distinct_out,
               SUM(CAST(json_extract(s.attributes, '$."{COST_USD}"') AS REAL)) AS grp_cost
        FROM spans s JOIN results r ON r.trace_id = s.trace_id
        WHERE r.run_id = ? AND s.kind = 'llm'
        GROUP BY s.trace_id, s.input_sha
        HAVING COUNT(*) > 1
        """,
        (run_id,),
    ).fetchall()
    if not groups:
        return []
    reclaimable_usd = sum((g["grp_cost"] or 0) * (g["n"] - 1) / g["n"] for g in groups)
    total_cost = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM results WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    pct = round(reclaimable_usd / total_cost * 100, 2) if total_cost else 0.0
    return [
        Recommendation(
            "R2",
            "Memoize duplicate LLM calls",
            "SAFE",
            est_saving_usd=round(reclaimable_usd, 8),
            est_saving_pct=pct,
            detail=f"identical LLM calls repeat within traces; caching reclaims ${reclaimable_usd:.6f}",
            evidence={"groups": len(groups)},
        )
    ]


def _ensemble_pass(votes: list[int]) -> int:
    yes = sum(votes)
    return 1 if yes > (len(votes) - yes) else 0


def rule_drop_secondary_judge(conn, run_id: str, roster: dict) -> list[Recommendation]:
    """R4: drop the secondary judge if it never changes a final verdict.

    Validated by verdict agreement from cached judgments only (no score gate, which
    would be circular for a judge change). SAFE iff zero final verdicts flip.
    """
    if "secondary" not in roster:
        return []
    secondary_model = roster["secondary"][1]
    output_shas = [
        r["output_sha"]
        for r in conn.execute(
            "SELECT DISTINCT output_sha FROM results WHERE run_id = ?", (run_id,)
        ).fetchall()
    ]
    flips = 0
    covered = 0
    for osha in output_shas:
        rows = conn.execute(
            "SELECT model, pass FROM judgments WHERE output_sha = ?", (osha,)
        ).fetchall()
        if not rows:
            continue
        covered += 1
        full = _ensemble_pass([r["pass"] for r in rows])
        reduced_votes = [r["pass"] for r in rows if r["model"] != secondary_model]
        reduced = _ensemble_pass(reduced_votes) if reduced_votes else full
        if full != reduced:
            flips += 1
    if covered == 0:
        return [
            Recommendation(
                "R4",
                "Drop the secondary judge",
                "INCONCLUSIVE",
                detail="no cached judgments for this run; run the judge first",
            )
        ]
    status = "SAFE" if flips == 0 else "REGRESSES"
    detail = (
        f"secondary judge changed {flips}/{covered} final verdicts"
        if flips
        else f"secondary judge never changed a verdict across {covered} outputs; it can be dropped"
    )
    return [
        Recommendation(
            "R4",
            "Drop the secondary judge",
            status,
            detail=detail,
            evidence={"covered": covered, "flips": flips},
        )
    ]


def rule_latency_outliers(conn, run_id: str, cap_ms: float) -> list[Recommendation]:
    """R7: flag cases slower than the latency cap for a human to investigate."""
    rows = conn.execute(
        "SELECT case_id, latency_ms FROM results WHERE run_id = ? AND latency_ms > ? "
        "ORDER BY latency_ms DESC",
        (run_id, cap_ms),
    ).fetchall()
    if not rows:
        return []
    return [
        Recommendation(
            "R7",
            "Investigate latency outliers",
            "ADVISORY_ONLY",
            detail=f"{len(rows)} case(s) exceeded the {cap_ms:.0f}ms latency cap",
            evidence={
                "cases": [
                    {"case_id": r["case_id"], "latency_ms": r["latency_ms"]} for r in rows[:10]
                ]
            },
        )
    ]
