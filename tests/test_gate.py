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
    assert r.verdict == "PASS"


def test_single_case_drop_does_not_block_on_ci():
    # n=1 has no statistical power; the degenerate zero-width CI must not block.
    r = gate_verdict(_scores([("c1", 0.0)]), _scores([("c1", 1.0)]), cfg=CFG)
    assert r.verdict == "PASS"
    assert r.mean_delta == -1.0


def test_cost_regression_blocks():
    s = _scores([("c1", 0.9)])
    r = gate_verdict(s, s, cand_cost=1.6, base_cost=1.0, cfg=CFG)
    assert r.blocked
    assert r.cost_delta_pct == 60.0


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
