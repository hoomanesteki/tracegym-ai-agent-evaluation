"""Judge ensemble: agreement, tiebreak-to-review, caching, and score blending."""

from __future__ import annotations

from tracegym.judge import judge_case, judge_run
from tracegym.replay import run_suite
from tracegym.store import connect

RUBRIC = {"criteria": [{"id": "quality", "description": "overall quality"}]}
CASE = {"id": "c1", "input": {"q": "hi"}}


def _provider(passed, score, calls=None, name="stub"):
    def fn(case, output, rubric, model, threshold=0.6):
        if calls is not None:
            calls.append(model)
        return {"scores": {"quality": score}, "pass": passed, "rationale": name}

    return fn


def test_agreement_yields_confident_pass():
    conn = connect()
    providers = {"a": _provider(True, 0.9), "b": _provider(True, 0.85)}
    roster = {"primary": ("a", "m1"), "secondary": ("b", "m2")}
    v = judge_case(conn, CASE, {"answer": "x"}, "sha1", RUBRIC, roster, providers=providers)
    assert v.state == "PASS"
    assert v.confidence == 1.0
    assert len(v.votes) == 2


def test_disagreement_triggers_tiebreaker_and_review():
    conn = connect()
    providers = {"y": _provider(True, 0.9), "n": _provider(False, 0.2), "t": _provider(True, 0.8)}
    roster = {"primary": ("y", "m1"), "secondary": ("n", "m2"), "tiebreaker": ("t", "m3")}
    v = judge_case(conn, CASE, {"answer": "x"}, "sha2", RUBRIC, roster, providers=providers)
    assert len(v.votes) == 3  # tiebreaker was consulted
    assert v.passed is True  # 2 of 3 say pass
    assert v.state == "NEEDS_REVIEW"  # but the primaries disagreed, so a human should look


def test_verdicts_are_cached():
    conn = connect()
    calls: list[str] = []
    providers = {"a": _provider(True, 0.9, calls), "b": _provider(True, 0.9, calls)}
    roster = {"primary": ("a", "m1"), "secondary": ("b", "m2")}
    judge_case(conn, CASE, {"answer": "x"}, "sha3", RUBRIC, roster, providers=providers)
    assert len(calls) == 2
    judge_case(conn, CASE, {"answer": "x"}, "sha3", RUBRIC, roster, providers=providers)
    assert len(calls) == 2  # served from cache, no new provider calls


def test_parse_failure_routes_to_review():
    conn = connect()

    def boom(case, output, rubric, model, threshold=0.6):
        raise ValueError("not json")

    providers = {"ok": _provider(True, 0.9), "boom": boom}
    roster = {"primary": ("ok", "m1"), "secondary": ("boom", "m2")}
    v = judge_case(conn, CASE, {"answer": "x"}, "sha4", RUBRIC, roster, providers=providers)
    assert v.state == "NEEDS_REVIEW"
    assert any(not vote.parse_ok for vote in v.votes)


def test_parse_failure_does_not_deflate_the_score():
    conn = connect()

    def boom(case, output, rubric, model, threshold=0.6):
        raise ValueError("bad json")

    providers = {"ok": _provider(True, 0.9), "boom": boom}
    roster = {"primary": ("ok", "m1"), "secondary": ("boom", "m2")}
    v = judge_case(conn, CASE, {"answer": "x"}, "sha5", RUBRIC, roster, providers=providers)
    # The surviving judge scored 0.9; a failed parse must not average it down to 0.45.
    assert v.score == 0.9


def test_same_model_different_provider_do_not_collapse():
    conn = connect()
    calls: list[str] = []

    def prov(passed, name):
        def fn(case, output, rubric, model, threshold=0.6):
            calls.append(name)
            return {
                "scores": {"quality": 0.9 if passed else 0.2},
                "pass": passed,
                "rationale": name,
            }

        return fn

    providers = {"a": prov(True, "a"), "b": prov(False, "b"), "t": prov(True, "t")}
    roster = {"primary": ("a", "shared"), "secondary": ("b", "shared"), "tiebreaker": ("t", "tb")}
    judge_case(conn, CASE, {"answer": "x"}, "sha6", RUBRIC, roster, providers=providers)
    # Both providers ran despite sharing the model name; the ensemble did not collapse.
    assert "a" in calls and "b" in calls


def _agent(rt, case):
    @rt.tool
    def retrieve(query):
        return {"hits": [{"id": "doc-1"}]}

    retrieve(query="q")
    return {"answer": "grounded answer", "citations": ["doc-1"]}


CASES = [{"id": "c1", "input": {"q": "hi"}, "checks": [{"type": "citation"}]}]


def test_judge_run_blends_and_updates_results(tmp_path):
    conn = connect()
    run_suite(conn, tmp_path, suite_id="s", cases=CASES, agent=_agent, mode="record")
    rid = run_suite(conn, tmp_path, suite_id="s", cases=CASES, agent=_agent, mode="frozen")
    providers = {"a": _provider(True, 0.9), "b": _provider(True, 0.9)}
    roster = {"primary": ("a", "m1"), "secondary": ("b", "m2")}
    summary = judge_run(
        conn, tmp_path, rid, {c["id"]: c for c in CASES}, RUBRIC, roster, providers=providers
    )
    assert summary["judged"] == 1
    row = conn.execute(
        "SELECT judge_state, judge_confidence, score FROM results WHERE run_id=?", (rid,)
    ).fetchone()
    assert row["judge_state"] == "PASS"
    assert row["judge_confidence"] == 1.0
    assert 0.0 < row["score"] <= 1.0
