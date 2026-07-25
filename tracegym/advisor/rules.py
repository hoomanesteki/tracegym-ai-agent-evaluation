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
    # Only SAFE when every repeated call produced an identical output. A group whose
    # identical inputs yielded different outputs (nondeterministic sampling, a retry)
    # cannot be memoized without changing an answer, so it is advisory, not proven.
    all_pure = all(g["distinct_out"] == 1 for g in groups)
    detail = f"identical LLM calls repeat within traces; caching reclaims ${reclaimable_usd:.6f}"
    if not all_pure:
        detail += " (some repeats returned different outputs; verify determinism first)"
    return [
        Recommendation(
            "R2",
            "Memoize duplicate LLM calls",
            "SAFE" if all_pure else "ADVISORY_ONLY",
            est_saving_usd=round(reclaimable_usd, 8),
            est_saving_pct=pct,
            detail=detail,
            evidence={"groups": len(groups), "all_identical": all_pure},
        )
    ]


def _majority(votes: list[int]) -> int | None:
    """1/0 for a decided majority, or None for a tie (ambiguous under any rule)."""
    yes = sum(votes)
    no = len(votes) - yes
    if yes == no:
        return None
    return 1 if yes > no else 0


def rule_drop_secondary_judge(conn, run_id: str, roster: dict) -> list[Recommendation]:
    """R4: drop the secondary judge only if it provably never decides a verdict.

    Validated by verdict agreement from cached judgments (no score gate, which would
    be circular for a judge change). A recommendation is SAFE only if, for every
    output, removing the secondary leaves the same decided majority. A tie (which
    the production ensemble breaks by score, a signal we do not have here) or a flip
    is treated as not-provable, so it is advisory rather than a fake SAFE. Votes are
    scoped to the output's most recent rubric so stale cross-rubric votes never leak.
    """
    if "secondary" not in roster:
        return []
    secondary_provider, secondary_model = roster["secondary"]
    output_shas = [
        r["output_sha"]
        for r in conn.execute(
            "SELECT DISTINCT output_sha FROM results WHERE run_id = ?", (run_id,)
        ).fetchall()
    ]
    covered = 0
    unsafe = 0  # outputs where dropping the secondary is not provably harmless
    for osha in output_shas:
        latest = conn.execute(
            "SELECT rubric_sha FROM judgments WHERE output_sha = ? ORDER BY created_at DESC LIMIT 1",
            (osha,),
        ).fetchone()
        if latest is None:
            continue
        rows = conn.execute(
            "SELECT provider, model, pass FROM judgments WHERE output_sha = ? AND rubric_sha = ?",
            (osha, latest["rubric_sha"]),
        ).fetchall()
        if not rows:
            continue
        covered += 1
        full = _majority([r["pass"] for r in rows])
        reduced_votes = [
            r["pass"]
            for r in rows
            if not (r["provider"] == secondary_provider and r["model"] == secondary_model)
        ]
        reduced = _majority(reduced_votes) if reduced_votes else full
        if full is None or reduced is None or full != reduced:
            unsafe += 1

    if covered == 0:
        return [
            Recommendation(
                "R4",
                "Drop the secondary judge",
                "INCONCLUSIVE",
                detail="no cached judgments for this run; run the judge first",
            )
        ]
    status = "SAFE" if unsafe == 0 else "ADVISORY_ONLY"
    detail = (
        f"secondary judge was decisive or ambiguous on {unsafe}/{covered} outputs; "
        "re-judge to validate before dropping"
        if unsafe
        else f"secondary judge never decided a verdict across {covered} outputs; safe to drop"
    )
    return [
        Recommendation(
            "R4",
            "Drop the secondary judge",
            status,
            detail=detail,
            evidence={"covered": covered, "not_provable": unsafe},
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
