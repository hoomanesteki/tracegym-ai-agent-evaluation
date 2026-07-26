"""Drive the whole zero-key demo and return one results bundle.

Builds a self-contained workspace from bundled code (no keys, no network), replays
it deterministically, judges from the local ensemble, measures seeded-regression
recall, profiles cost, and gathers the advisor's recommendations. The report
renders straight from what this returns.
"""

from __future__ import annotations

import os
from pathlib import Path

from tracegym.advisor import advise, build_profile
from tracegym.calibrate import calibrate_from_db
from tracegym.demos import DEMO_ROSTER
from tracegym.demos.build import build_demodata
from tracegym.demos.harness import seeded_bug_recall
from tracegym.gate import gate_runs
from tracegym.metrics import suite_scorecard
from tracegym.replay.loader import load_suite
from tracegym.review import list_open, populate_from_run
from tracegym.store import connect


def _determinism_check(conn, blob_root, suites: dict) -> dict:
    """Replay each agent suite twice more and confirm output hashes are identical."""
    from tracegym.demos.harness import agent_and_responder
    from tracegym.replay import run_suite

    total = identical = 0
    for sid in ("support-rag", "sql-analyst"):
        cases = suites[sid]["cases"]
        agent, responder = agent_and_responder(sid)
        runs = [
            run_suite(
                conn,
                blob_root,
                suite_id=sid,
                cases=cases,
                agent=agent,
                mode="frozen",
                responder=responder,
            )
            for _ in range(2)
        ]

        def shas(run_id):
            rows = conn.execute(
                "SELECT case_id, output_sha FROM results WHERE run_id = ? ORDER BY case_id",
                (run_id,),
            ).fetchall()
            return {r["case_id"]: r["output_sha"] for r in rows}

        a, b = shas(runs[0]), shas(runs[1])
        for case_id in a:
            total += 1
            identical += int(a[case_id] == b.get(case_id))
    return {
        "cases": total,
        "identical": identical,
        "pct": round(identical / total * 100, 1) if total else 0.0,
    }


def run_demo(workspace: str | Path) -> dict:
    """Build and replay the demo at ``workspace``; return the results bundle."""
    workspace = Path(workspace)
    manifest = build_demodata(workspace)

    cwd = os.getcwd()
    os.chdir(workspace)
    try:
        conn = connect("traces.db")
        blob_root = Path("blobs")

        suites = {}
        for sid in manifest["suites"]:
            cases, _ = load_suite(Path("suites") / sid)
            suites[sid] = {"cases": cases, "baseline_run": manifest["baselines"][sid]}

        agent_suites = {k: v for k, v in suites.items() if k != "meta-judge"}
        recall = seeded_bug_recall(conn, blob_root, agent_suites)

        scorecards = {sid: suite_scorecard(conn, suites[sid]["baseline_run"]) for sid in suites}
        calibration = calibrate_from_db(conn, labeler="canary-gold", min_labels=1)

        # Per-case drill-down and the regression gallery: the actual data behind
        # the headline numbers.
        from tracegym.inspection import case_detail, regression_examples

        case_details = {
            sid: [
                d
                for c in suites[sid]["cases"]
                if (d := case_detail(conn, blob_root, suites[sid]["baseline_run"], c["id"], c))
            ]
            for sid in suites
        }
        regression_gallery = regression_examples(conn, blob_root, agent_suites)

        from tracegym.timeseries import run_history

        history = {sid: run_history(conn, sid) for sid in agent_suites}

        sql_baseline = suites["sql-analyst"]["baseline_run"]
        profile = build_profile(conn, sql_baseline)
        recommendations = [r.__dict__ for r in advise(conn, sql_baseline, roster=DEMO_ROSTER)]

        # One concrete gate demo: the destructive-SQL bug blocked against baseline.
        from tracegym.demos.bugs import BUGS, buggy_agent
        from tracegym.demos.harness import agent_and_responder
        from tracegym.replay import run_suite

        bug = next(b for b in BUGS if b["id"] == "sql_delete")
        base_agent, responder = agent_and_responder("sql-analyst")
        buggy_run = run_suite(
            conn,
            blob_root,
            suite_id="sql-analyst",
            cases=suites["sql-analyst"]["cases"],
            agent=buggy_agent(base_agent, bug["fn"]),
            mode="frozen",
            responder=responder,
        )
        gate_demo = gate_runs(conn, buggy_run, sql_baseline)

        # Determinism: replay a suite twice more and compare output hashes.
        determinism = _determinism_check(conn, blob_root, suites)
        needs_review = conn.execute(
            "SELECT COUNT(*) FROM results WHERE judge_state = 'NEEDS_REVIEW'"
        ).fetchone()[0]

        # Human-notify tier: fold any real needs_review/gate-warn signals from this
        # run into the queue seeded at build time, then read it back for the report.
        populate_from_run(conn, buggy_run, gate_demo)
        review = list_open(conn)

        conn.commit()
        conn.close()
    finally:
        os.chdir(cwd)

    return {
        "manifest": manifest,
        "scorecards": scorecards,
        "recall": recall,
        "calibration": calibration,
        "profile": profile,
        "recommendations": recommendations,
        "determinism": determinism,
        "needs_review": needs_review,
        "case_details": case_details,
        "regression_gallery": regression_gallery,
        "history": history,
        "review": review,
        "gate_demo": {
            "bug": bug["id"],
            "verdict": gate_demo.verdict,
            "reasons": gate_demo.reasons,
            "mean_delta": gate_demo.mean_delta,
            "ci": [gate_demo.ci_low, gate_demo.ci_high],
            "new_invariant_fails": gate_demo.new_invariant_fails,
        },
    }
