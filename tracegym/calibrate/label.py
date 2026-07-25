"""Human labeling and calibration from the database.

Labels are optional and may arrive late; nothing here blocks on their absence. The
gate mode degrades gracefully: with too few labels or weak agreement the judge
becomes advisory and the gate falls back to L1. A stratified sampler picks a
balanced pass/fail set to label, because kappa is punished by class imbalance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from tracegym.calibrate.kappa import agreement_report

MIN_LABELS = 20


def _now() -> str:
    return datetime.now(UTC).isoformat()


def add_label(
    conn,
    case_id: str,
    output_sha: str,
    label_pass: bool,
    labeler: str,
    *,
    label_round: int = 1,
    notes: str = "",
) -> None:
    """Record one human label. Idempotent per (case, output, labeler, round)."""
    conn.execute(
        """
        INSERT OR REPLACE INTO labels
            (id, case_id, output_sha, label_pass, labeler, round, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "lb-" + uuid.uuid4().hex[:12],
            case_id,
            output_sha,
            1 if label_pass else 0,
            labeler,
            label_round,
            notes,
            _now(),
        ),
    )
    conn.commit()


def _ensemble_pass(conn, output_sha: str) -> int | None:
    """Majority pass across the most recently judged rubric for an output.

    Scoped to a single rubric_sha (the latest) so re-judging under an evolved
    rubric does not pool stale cross-rubric votes into the calibration label.
    """
    latest = conn.execute(
        "SELECT rubric_sha FROM judgments WHERE output_sha = ? ORDER BY created_at DESC LIMIT 1",
        (output_sha,),
    ).fetchone()
    if latest is None:
        return None
    rows = conn.execute(
        "SELECT pass FROM judgments WHERE output_sha = ? AND rubric_sha = ?",
        (output_sha, latest["rubric_sha"]),
    ).fetchall()
    yes = sum(r["pass"] for r in rows)
    return 1 if yes > (len(rows) - yes) else 0


def stratified_sample(conn, run_id: str, n: int) -> list[dict]:
    """Pick ~n items split evenly by predicted pass/fail, to label without imbalance."""
    rows = conn.execute(
        "SELECT case_id, output_sha, judge_pass, score FROM results WHERE run_id = ?", (run_id,)
    ).fetchall()
    predicted_pass = [
        r for r in rows if (r["judge_pass"] if r["judge_pass"] is not None else r["score"] >= 0.6)
    ]
    predicted_fail = [r for r in rows if r not in predicted_pass]
    half = max(1, n // 2)
    picked = predicted_pass[:half] + predicted_fail[:half]
    return [{"case_id": r["case_id"], "output_sha": r["output_sha"]} for r in picked]


def calibrate_from_db(
    conn, *, labeler: str | None = None, label_round: int = 1, min_labels: int = MIN_LABELS
) -> dict:
    """Compute the calibration report from stored labels and cached judgments."""
    query = "SELECT case_id, output_sha, label_pass FROM labels WHERE round = ?"
    params: list = [label_round]
    if labeler:
        query += " AND labeler = ?"
        params.append(labeler)
    rows = conn.execute(query, params).fetchall()

    human, judge = [], []
    for r in rows:
        jp = _ensemble_pass(conn, r["output_sha"])
        if jp is None:
            continue  # labeled but not judged yet; not counted, not fabricated
        human.append(r["label_pass"])
        judge.append(jp)

    total = len(rows)
    if not human:
        return {
            "n": 0,
            "labeled": total,
            "coverage": 0.0,
            "gate_mode": "l1_only",
            "message": "no labeled-and-judged pairs yet; judge is advisory until calibrated",
        }

    report = agreement_report(human, judge)
    report["labeled"] = total
    report["coverage"] = round(len(human) / total, 4) if total else 0.0

    # A judge cannot be trusted to gate if it was never tested against both a
    # passing and a failing example. Under the pass-heavy class balance an
    # all-pass label set yields kappa=1.0 by construction, so require both classes.
    n_pass = sum(human)
    n_fail = len(human) - n_pass
    report["labels_per_class"] = {"pass": n_pass, "fail": n_fail}
    both_classes = n_pass > 0 and n_fail > 0

    if len(human) < min_labels or not both_classes or report["cohen_kappa"] < 0.55:
        report["gate_mode"] = "l1_only"
    elif report["cohen_kappa"] < 0.70:
        report["gate_mode"] = "agreement_subset"
    else:
        report["gate_mode"] = "judge_gates"
    return report
