"""Apply the judge ensemble to a stored run and fold verdicts into the scores.

The final per-case score blends the deterministic L1 pass fraction with the judge
quality score, so the gate is sensitive to both a broken invariant and a quiet
quality drop. A failed invariant still forces the score to zero. Judging reads
cached verdicts first, so re-judging an unchanged run costs nothing.
"""

from __future__ import annotations

import json

from tracegym.judge.base import judge_case
from tracegym.store.blobs import get


def _l1_fraction(l1_results_json: str) -> float:
    results = json.loads(l1_results_json or "[]")
    if not results:
        return 1.0
    return sum(1 for r in results if r["passed"]) / len(results)


def judge_run(
    conn,
    blob_root,
    run_id: str,
    cases_by_id: dict,
    rubric: dict,
    roster: dict,
    *,
    threshold: float = 0.6,
    blend: float = 0.5,
    providers: dict | None = None,
    use_cache: bool = True,
) -> dict:
    """Judge every result in a run, update scores/state, and return a summary."""
    rows = conn.execute(
        "SELECT id, case_id, output_sha, l1_results, l1_invariant_fail FROM results WHERE run_id = ?",
        (run_id,),
    ).fetchall()

    needs_review = 0
    scores = []
    for r in rows:
        case = cases_by_id.get(r["case_id"], {"id": r["case_id"]})
        output = get(blob_root, r["output_sha"])
        verdict = judge_case(
            conn,
            case,
            output,
            r["output_sha"],
            rubric,
            roster,
            threshold=threshold,
            providers=providers,
            use_cache=use_cache,
        )
        l1_frac = _l1_fraction(r["l1_results"])
        if r["l1_invariant_fail"]:
            new_score = 0.0
        else:
            new_score = round(blend * l1_frac + (1 - blend) * verdict.score, 6)
        scores.append(new_score)
        if verdict.state == "NEEDS_REVIEW":
            needs_review += 1
        conn.execute(
            "UPDATE results SET judge_pass=?, judge_confidence=?, judge_state=?, score=? WHERE id=?",
            (1 if verdict.passed else 0, verdict.confidence, verdict.state, new_score, r["id"]),
        )
    conn.commit()

    return {
        "judged": len(rows),
        "needs_review": needs_review,
        "mean_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
    }
