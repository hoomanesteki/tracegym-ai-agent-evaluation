"""The advisor profiles a run and only marks a change SAFE once it is proven."""

from __future__ import annotations

import json

from tracegym.advisor import advise, build_profile, store_recommendations
from tracegym.advisor.rules import rule_drop_secondary_judge, rule_duplicate_llm
from tracegym.judge import judge_run
from tracegym.replay import run_suite
from tracegym.store import connect


def _seed_run(conn, run_id="run1", trace_id="tr1"):
    conn.execute(
        "INSERT INTO runs (id, suite_id, mode, created_at) VALUES (?, 's', 'frozen', 't')",
        (run_id,),
    )
    conn.execute(
        "INSERT INTO results (id, run_id, case_id, trace_id, output_sha, cost_usd, created_at) "
        "VALUES ('res1', ?, 'c1', ?, 'o1', 0.02, 't')",
        (run_id, trace_id),
    )


def test_r2_is_advisory_when_identical_inputs_gave_different_outputs():
    conn = connect()
    _seed_run(conn)
    for i, osha in enumerate(["oA", "oB"]):
        conn.execute(
            "INSERT INTO spans (id, trace_id, kind, name, input_sha, output_sha, start_ns, end_ns, attributes) "
            "VALUES (?, 'tr1', 'llm', 'chat', 'SAME', ?, 0, 1, ?)",
            (f"sp{i}", osha, json.dumps({"tracegym.cost_usd": 0.01})),
        )
    rec = rule_duplicate_llm(conn, "run1")[0]
    assert rec.status == "ADVISORY_ONLY"  # not byte-identical, so not proven safe


def test_r4_is_advisory_when_secondary_is_decisive():
    conn = connect()
    _seed_run(conn)
    # primary fail, secondary pass: a 1-1 split the production ensemble breaks by score.
    conn.execute(
        "INSERT INTO judgments (id, case_id, output_sha, rubric_sha, provider, model, pass, created_at) "
        "VALUES ('j1', 'c1', 'o1', 'r', 'pa', 'ma', 0, 't1')"
    )
    conn.execute(
        "INSERT INTO judgments (id, case_id, output_sha, rubric_sha, provider, model, pass, created_at) "
        "VALUES ('j2', 'c1', 'o1', 'r', 'pb', 'mb', 1, 't2')"
    )
    rec = rule_drop_secondary_judge(conn, "run1", {"secondary": ("pb", "mb")})[0]
    assert rec.status == "ADVISORY_ONLY"


def test_r4_is_safe_when_judges_are_unanimous():
    conn = connect()
    _seed_run(conn)
    conn.execute(
        "INSERT INTO judgments (id, case_id, output_sha, rubric_sha, provider, model, pass, created_at) "
        "VALUES ('j1', 'c1', 'o1', 'r', 'pa', 'ma', 1, 't1')"
    )
    conn.execute(
        "INSERT INTO judgments (id, case_id, output_sha, rubric_sha, provider, model, pass, created_at) "
        "VALUES ('j2', 'c1', 'o1', 'r', 'pb', 'mb', 1, 't2')"
    )
    rec = rule_drop_secondary_judge(conn, "run1", {"secondary": ("pb", "mb")})[0]
    assert rec.status == "SAFE"


RUBRIC = {"criteria": [{"id": "quality"}]}
CASES = [{"id": "c1", "input": {"q": "hi"}, "checks": []}]


def _wasteful_agent(rt, case):
    @rt.tool
    def search(q):
        return {"hits": [{"id": "d1"}]}

    search(q="a")
    search(q="a")  # identical repeat -> redundant tool call
    rt.chat([{"role": "user", "content": "same prompt"}])
    rt.chat([{"role": "user", "content": "same prompt"}])  # identical repeat -> duplicate LLM call
    return {"answer": "ok"}


def _seed(tmp_path, conn):
    run_suite(conn, tmp_path, suite_id="s", cases=CASES, agent=_wasteful_agent, mode="record")
    return run_suite(
        conn, tmp_path, suite_id="s", cases=CASES, agent=_wasteful_agent, mode="frozen"
    )


def test_profile_reports_totals_and_hotspots(tmp_path):
    conn = connect()
    rid = _seed(tmp_path, conn)
    prof = build_profile(conn, rid)
    assert prof["cases"] == 1
    assert prof["totals"]["tool_calls"] == 2
    assert prof["totals"]["cost_usd"] >= 0
    assert prof["top_cost_cases"][0]["case_id"] == "c1"


def test_byte_identical_rules_are_safe(tmp_path):
    conn = connect()
    rid = _seed(tmp_path, conn)
    recs = {r.rule_id: r for r in advise(conn, rid)}
    assert recs["R1"].status == "SAFE"  # redundant tool call is safe to memoize
    assert recs["R2"].status == "SAFE"  # duplicate LLM call is safe to memoize
    assert recs["R2"].est_saving_usd >= 0


def _provider(passed, score):
    def fn(case, output, rubric, model, threshold=0.6):
        return {"scores": {"quality": score}, "pass": passed, "rationale": "x"}

    return fn


def test_drop_secondary_judge_is_safe_when_no_flips(tmp_path):
    conn = connect()
    rid = _seed(tmp_path, conn)
    providers = {"a": _provider(True, 0.9), "b": _provider(True, 0.9)}
    roster = {"primary": ("a", "m1"), "secondary": ("b", "m2")}
    judge_run(conn, tmp_path, rid, {c["id"]: c for c in CASES}, RUBRIC, roster, providers=providers)
    recs = {r.rule_id: r for r in advise(conn, rid, roster=roster)}
    assert recs["R4"].status == "SAFE"
    assert recs["R4"].evidence["not_provable"] == 0


def test_recommendations_persist(tmp_path):
    conn = connect()
    rid = _seed(tmp_path, conn)
    recs = advise(conn, rid)
    store_recommendations(conn, rid, recs)
    n = conn.execute("SELECT COUNT(*) FROM recommendations WHERE run_id = ?", (rid,)).fetchone()[0]
    assert n == len(recs) >= 2
