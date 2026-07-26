"""The review queue routes soft signals to a human and closes the loop on resolve."""

from __future__ import annotations

from tracegym import review as rq
from tracegym.gate.gate import GateResult
from tracegym.store import connect


def _add_result(conn, *, run_id, case_id, output_sha, state):
    conn.execute(
        "INSERT INTO results (id, run_id, case_id, output_sha, judge_state, score, created_at) "
        "VALUES (?, ?, ?, ?, ?, 0, '2026-01-01')",
        (f"r-{case_id}", run_id, case_id, output_sha, state),
    )
    conn.commit()


def test_enqueue_dedupes_open_items():
    conn = connect()
    a = rq.enqueue(conn, run_id="run1", kind="gate_warn", ref_id="x", reason="first")
    b = rq.enqueue(conn, run_id="run1", kind="gate_warn", ref_id="x", reason="again")
    assert a == b
    assert len(rq.list_open(conn)) == 1


def test_list_open_sorts_by_severity():
    conn = connect()
    rq.enqueue(conn, run_id="r", kind="gate_warn", ref_id="lo", severity="low")
    rq.enqueue(conn, run_id="r", kind="gate_warn", ref_id="hi", severity="high")
    order = [i["ref_id"] for i in rq.list_open(conn)]
    assert order == ["hi", "lo"]


def test_populate_from_run_enqueues_needs_review_and_warns():
    conn = connect()
    _add_result(conn, run_id="run1", case_id="c1", output_sha="sha1", state="NEEDS_REVIEW")
    _add_result(conn, run_id="run1", case_id="c2", output_sha="sha2", state="PASS")
    gate = GateResult(verdict="WARN", warnings=["cost up 30%"])
    n = rq.populate_from_run(conn, "run1", gate)
    assert n == 2  # one needs_review case + one gate warning
    kinds = sorted(i["kind"] for i in rq.list_open(conn))
    assert kinds == ["gate_warn", "needs_review"]


def test_resolving_needs_review_with_label_feeds_calibration():
    conn = connect()
    _add_result(conn, run_id="run1", case_id="c1", output_sha="sha1", state="NEEDS_REVIEW")
    rq.populate_from_run(conn, "run1")
    item = rq.list_open(conn)[0]

    assert rq.resolve(conn, item["id"], label_pass=True, labeler="alex")

    label = conn.execute(
        "SELECT label_pass, labeler FROM labels WHERE case_id = ? AND output_sha = ?",
        ("c1", "sha1"),
    ).fetchone()
    assert label["label_pass"] == 1
    assert label["labeler"] == "alex"
    assert rq.list_open(conn) == []  # no longer open


def test_ack_and_resolve_change_status():
    conn = connect()
    item_id = rq.enqueue(conn, run_id="r", kind="drift", ref_id="d1")
    assert rq.ack(conn, item_id)
    assert rq.list_open(conn) == []  # ack removes it from the open list
    assert rq.resolve(conn, item_id)
    assert not rq.resolve(conn, "rv-does-not-exist")


def test_ack_does_not_reopen_a_resolved_item():
    conn = connect()
    item_id = rq.enqueue(conn, run_id="r", kind="drift", ref_id="d1")
    assert rq.resolve(conn, item_id)
    assert not rq.ack(conn, item_id)  # resolving is final; ack is a no-op, not a revert
    status = conn.execute("SELECT status FROM review_queue WHERE id = ?", (item_id,)).fetchone()
    assert status["status"] == "resolved"
