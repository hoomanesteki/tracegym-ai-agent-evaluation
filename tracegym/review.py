"""The review queue: the human-notify tier of the monitor-control-optimize loop.

Signals that should not auto-block but a person should see (a judge NEEDS_REVIEW
case, a gate WARN, a drift alert) land here. Resolving a needs_review item records
a human label, so the human's decision feeds calibration and closes the loop back
into control. Keyless and offline: this is a table plus `tg review`, not a service.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from tracegym.calibrate import add_label

_SEV_ORDER = {"high": 0, "med": 1, "low": 2}


@dataclass
class ResolveResult:
    """Outcome of resolve(): whether the item was resolved and whether resolving it
    actually wrote a calibration label (only needs_review items with a case output
    can). Truthy iff resolved, so callers can still `if resolve(...)`."""

    resolved: bool
    labeled: bool = False

    def __bool__(self) -> bool:
        return self.resolved


def _now() -> str:
    return datetime.now(UTC).isoformat()


def enqueue(
    conn,
    *,
    run_id: str | None,
    kind: str,
    reason: str = "",
    severity: str = "med",
    ref_id: str | None = None,
    case_id: str | None = None,
    output_sha: str | None = None,
    tier: str = "human-notify",
) -> str:
    """Add an open review item, de-duplicating on (kind, run_id, ref_id)."""
    existing = conn.execute(
        "SELECT id FROM review_queue WHERE kind = ? AND IFNULL(run_id,'') = IFNULL(?,'') "
        "AND IFNULL(ref_id,'') = IFNULL(?,'') AND status = 'open'",
        (kind, run_id, ref_id),
    ).fetchone()
    if existing:
        return existing["id"]
    item_id = "rv-" + uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO review_queue (id, run_id, kind, ref_id, case_id, output_sha, tier, severity, "
        "reason, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
        (item_id, run_id, kind, ref_id, case_id, output_sha, tier, severity, reason, _now()),
    )
    conn.commit()
    return item_id


def list_open(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM review_queue WHERE status = 'open'").fetchall()
    items = [dict(r) for r in rows]
    items.sort(key=lambda r: (_SEV_ORDER.get(r.get("severity"), 3), r.get("created_at") or ""))
    return items


def ack(conn, item_id: str) -> bool:
    cur = conn.execute("UPDATE review_queue SET status = 'ack' WHERE id = ?", (item_id,))
    conn.commit()
    return cur.rowcount > 0


def resolve(
    conn, item_id: str, *, label_pass: bool | None = None, labeler: str = "reviewer"
) -> ResolveResult:
    """Resolve an item. For a needs_review item that carries a case output, a label
    records the human decision into the calibration set. Returns a ResolveResult
    whose `labeled` flag says whether a label was actually written."""
    row = conn.execute("SELECT * FROM review_queue WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return ResolveResult(resolved=False)
    labeled = bool(
        row["kind"] == "needs_review"
        and label_pass is not None
        and row["case_id"]
        and row["output_sha"]
    )
    if labeled:
        add_label(conn, row["case_id"], row["output_sha"], label_pass, labeler=labeler)
    conn.execute(
        "UPDATE review_queue SET status = 'resolved', resolved_at = ? WHERE id = ?",
        (_now(), item_id),
    )
    conn.commit()
    return ResolveResult(resolved=True, labeled=labeled)


def populate_from_run(conn, run_id: str, gate_result=None) -> int:
    """Enqueue human-notify items from a run: NEEDS_REVIEW cases and gate WARNs."""
    n = 0
    for r in conn.execute(
        "SELECT case_id, output_sha FROM results WHERE run_id = ? AND judge_state = 'NEEDS_REVIEW'",
        (run_id,),
    ).fetchall():
        enqueue(
            conn,
            run_id=run_id,
            kind="needs_review",
            ref_id=r["case_id"],
            case_id=r["case_id"],
            output_sha=r["output_sha"],
            reason="judges disagreed or confidence was low",
        )
        n += 1
    for w in getattr(gate_result, "warnings", []) or []:
        enqueue(conn, run_id=run_id, kind="gate_warn", ref_id=w[:40], reason=w, severity="low")
        n += 1
    return n
