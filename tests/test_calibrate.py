"""Calibration statistics, the ladder, and graceful missing-label degradation."""

from __future__ import annotations

from tracegym.calibrate import (
    add_label,
    agreement_report,
    calibrate_from_db,
    cohen_kappa,
    pabak,
)
from tracegym.store import connect


def test_perfect_agreement_kappa_is_one():
    assert cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0


def test_chance_agreement_kappa_near_zero():
    # Judge always says pass; humans split. Kappa collapses even though raw agreement is 0.5.
    human = [1, 0, 1, 0]
    judge = [1, 1, 1, 1]
    assert cohen_kappa(human, judge) == 0.0
    assert pabak(human, judge) == 0.0  # 2*0.5 - 1


def test_report_ladder_tiers():
    strong = agreement_report([1, 1, 1, 0, 0, 0, 1, 0, 1, 0], [1, 1, 1, 0, 0, 0, 1, 0, 0, 1])
    assert strong["tier"] in {"ship", "iterate"}
    assert "confusion" in strong and strong["n"] == 10


def test_calibrate_degrades_with_no_labels():
    conn = connect()
    report = calibrate_from_db(conn)
    assert report["gate_mode"] == "l1_only"  # never blocks CI without evidence


def test_calibrate_matches_labels_to_cached_judgments():
    conn = connect()
    # Seed judgments (two model verdicts per output) and matching human labels.
    for out, jpass, hpass in [("o1", 1, 1), ("o2", 0, 0), ("o3", 1, 0)]:
        for model in ("m1", "m2"):
            conn.execute(
                "INSERT INTO judgments (id, case_id, output_sha, rubric_sha, provider, model, pass, created_at) "
                "VALUES (?, ?, ?, 'r', 'local', ?, ?, '2026-01-01')",
                (f"j-{out}-{model}", f"c-{out}", out, model, jpass),
            )
        add_label(conn, f"c-{out}", out, bool(hpass), labeler="me")
    report = calibrate_from_db(conn, min_labels=1)
    assert report["n"] == 3
    assert report["coverage"] == 1.0
    assert report["confusion"]["fp"] == 1  # o3: judge says pass, human says fail
