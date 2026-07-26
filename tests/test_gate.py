"""The gate blocks only high-confidence regressions, and is deterministic."""

from __future__ import annotations

from tracegym.config import GateConfig
from tracegym.gate import gate_against_baseline, gate_runs, gate_verdict, promote
from tracegym.replay import run_suite
from tracegym.store import connect

CFG = GateConfig()


def _scores(pairs):
    return {c: s for c, s in pairs}


def test_identical_runs_pass():
    s = _scores([("c1", 0.9), ("c2", 0.8), ("c3", 1.0)])
    r = gate_verdict(s, s, cfg=CFG)
    assert r.verdict == "PASS"
    assert r.mean_delta == 0.0


def test_new_invariant_failure_blocks():
    cand = _scores([("c1", 0.0), ("c2", 0.9)])
    base = _scores([("c1", 1.0), ("c2", 0.9)])
    r = gate_verdict(
        cand, base, cand_invariant_fails={"c1": 1}, base_invariant_fails={"c1": 0}, cfg=CFG
    )
    assert r.blocked
    assert r.new_invariant_fails == 1


def test_uniform_quality_drop_blocks():
    cand = _scores([(f"c{i}", 0.55) for i in range(12)])
    base = _scores([(f"c{i}", 0.95) for i in range(12)])
    r = gate_verdict(cand, base, cfg=CFG)
    assert r.blocked
    assert r.mean_delta < -0.15
    assert r.ci_high < 0  # confident regression


def test_mean_drop_with_wide_ci_does_not_block():
    # Half the cases crater, half improve: mean is below the threshold but the
    # 95% CI still crosses zero, so it is not a confident regression.
    cand = _scores([(f"c{i}", 0.0) for i in range(4)] + [(f"d{i}", 1.0) for i in range(4)])
    base = _scores([(f"c{i}", 1.0) for i in range(4)] + [(f"d{i}", 0.5) for i in range(4)])
    r = gate_verdict(cand, base, cfg=CFG)
    assert r.mean_delta < -0.15
    assert r.ci_high > 0  # CI includes zero
    assert r.verdict == "WARN"  # soft signal for a human, but not a merge block
    assert not r.blocked


def test_single_case_drop_does_not_block_on_ci():
    # n=1 has no statistical power; the degenerate zero-width CI must not block.
    r = gate_verdict(_scores([("c1", 0.0)]), _scores([("c1", 1.0)]), cfg=CFG)
    assert r.verdict == "PASS"
    assert r.mean_delta == -1.0


def test_flip_test_blocks_when_the_mean_barely_moves():
    # 5 cases dip just below the pass threshold; the mean barely moves, but a
    # consistent one-directional flip is caught by the exact sign test.
    cand = _scores([(f"c{i}", 0.99) for i in range(5)] + [(f"d{i}", 1.0) for i in range(5)])
    base = _scores([(f"c{i}", 1.0) for i in range(5)] + [(f"d{i}", 1.0) for i in range(5)])
    r = gate_verdict(cand, base, cfg=CFG)
    assert r.blocked
    assert r.flips == (5, 0)
    assert r.mean_delta > -0.15  # mean-delta alone would not have blocked


def test_cost_regression_blocks():
    s = _scores([("c1", 0.9)])
    r = gate_verdict(s, s, cand_cost=1.6, base_cost=1.0, cfg=CFG)
    assert r.blocked
    assert r.cost_delta_pct == 60.0


def test_no_shared_cases_warns_instead_of_silently_passing():
    # Disjoint case sets mean nothing was compared; a silent PASS would be a CI
    # false negative, so it must surface as WARN.
    r = gate_verdict(_scores([("a", 1.0)]), _scores([("b", 1.0)]), cfg=CFG)
    assert r.verdict == "WARN"
    assert r.n_cases == 0
    assert not r.blocked


def test_free_to_paid_cost_jump_warns():
    # A percent is undefined against a $0 baseline, so a free-to-paid jump would
    # otherwise slip through; it must warn.
    s = _scores([("c1", 1.0)])
    r = gate_verdict(s, s, cand_cost=5.0, base_cost=0.0, cfg=CFG)
    assert r.verdict == "WARN"
    assert not r.blocked


def test_gate_is_deterministic():
    cand = _scores([(f"c{i}", 0.7) for i in range(20)])
    base = _scores([(f"c{i}", 0.85) for i in range(20)])
    a = gate_verdict(cand, base, cfg=CFG)
    b = gate_verdict(cand, base, cfg=CFG)
    assert (a.ci_low, a.ci_high) == (b.ci_low, b.ci_high)


# Several cases so the CI-based signal has enough samples to be meaningful.
CASES = [
    {"id": f"c{i}", "input": {}, "checks": [{"type": "contains", "value": "ok"}]} for i in range(4)
]


def _good_agent(rt, case):
    return {"answer": "ok"}


def _bad_agent(rt, case):
    return {"answer": "wrong"}


def test_gate_against_promoted_baseline_via_db(tmp_path):
    conn = connect()
    good = run_suite(conn, tmp_path, suite_id="s", cases=CASES, agent=_good_agent, mode="record")
    promote(conn, "baseline", "s", good)
    bad = run_suite(conn, tmp_path, suite_id="s", cases=CASES, agent=_bad_agent, mode="record")
    result = gate_against_baseline(conn, bad, cfg=GateConfig(delta_block=-0.15))
    # The bad agent fails its only check, so the score drops from 1.0 to 0.0.
    assert result.blocked
    assert gate_runs(conn, good, good, cfg=CFG).verdict == "PASS"


def _mk_run(conn, run_id, config_sha, outputs):
    conn.execute(
        "INSERT INTO runs (id, suite_id, mode, config_sha, created_at) VALUES (?,?,?,?,?)",
        (run_id, "s", "frozen", config_sha, "2026-01-01"),
    )
    for cid, sha in outputs.items():
        conn.execute(
            "INSERT INTO results (id, run_id, case_id, output_sha, score, created_at) "
            "VALUES (?,?,?,?,1.0,'2026-01-01')",
            (f"{run_id}-{cid}", run_id, cid, sha),
        )
    conn.commit()


def test_identical_config_but_changed_output_blocks_as_churn():
    # Same pinned config_sha promises identical replays; a changed output hash is
    # nondeterministic churn and must block even though every score is a perfect 1.0.
    conn = connect()
    _mk_run(conn, "ref", "cfg-abc", {"c1": "h1", "c2": "h2"})
    _mk_run(conn, "cand", "cfg-abc", {"c1": "h1", "c2": "DIFFERENT"})
    r = gate_runs(conn, "cand", "ref", cfg=CFG)
    assert r.blocked
    assert r.churn_cases == ["c2"]


def test_null_config_sha_never_triggers_churn():
    # The demo pins no config_sha, so a legitimate content change must not be
    # mistaken for churn (guard requires both config_shas non-null and equal).
    conn = connect()
    _mk_run(conn, "ref", None, {"c1": "h1"})
    _mk_run(conn, "cand", None, {"c1": "DIFFERENT"})
    r = gate_runs(conn, "cand", "ref", cfg=CFG)
    assert r.churn_cases == []
