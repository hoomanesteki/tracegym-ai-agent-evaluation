"""Build the bundled zero-key demo workspace under a destination directory.

Produces a self-contained workspace: the sample database, three golden suites with
rubrics, recorded fixtures, a promoted baseline per suite, and a seeded judgment
cache. `tg demo` builds and replays this with no keys.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import yaml

from tracegym.calibrate import add_label
from tracegym.demos import DEMO_ROSTER
from tracegym.demos.data import (
    CANARIES,
    CUSTOMERS_ROWS,
    SALES_ROWS,
    SQL_QA,
    SUPPORT_QA,
)
from tracegym.demos.harness import agent_and_responder
from tracegym.gate import promote
from tracegym.judge import judge_case, judge_run
from tracegym.replay import run_suite
from tracegym.store import connect
from tracegym.store.blobs import get
from tracegym.util.canon import sha256_hex

DB_REL = "agents/sql_analyst/db.sqlite"
META_RUBRIC_REL = "suites/meta-judge/rubric.yaml"

SUPPORT_RUBRIC = {
    "criteria": [
        {"id": "relevance", "description": "The answer addresses the question."},
        {"id": "grounding", "description": "The answer is supported by the retrieved document."},
    ],
    "pass_threshold": 0.6,
}
SQL_RUBRIC = {
    "criteria": [{"id": "correctness", "description": "The query returns the correct rows."}],
    "pass_threshold": 0.6,
}
META_RUBRIC = {
    "criteria": [{"id": "quality", "description": "The answer is helpful and correct."}],
    "pass_threshold": 0.6,
}


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sales(region TEXT, product TEXT, amount INTEGER, day INTEGER)")
    conn.executemany("INSERT INTO sales VALUES (?, ?, ?, ?)", SALES_ROWS)
    conn.execute("CREATE TABLE customers(id INTEGER, name TEXT, region TEXT)")
    conn.executemany("INSERT INTO customers VALUES (?, ?, ?)", CUSTOMERS_ROWS)
    conn.commit()
    conn.close()


def _support_cases() -> list[dict]:
    cases = []
    for i, (question, _doc, expect) in enumerate(SUPPORT_QA, 1):
        cases.append(
            {
                "id": f"sr-{i:03d}",
                "input": {"question": question},
                "checks": [
                    {"type": "contains", "value": expect},
                    {"type": "citation"},
                    {
                        "type": "schema_valid",
                        "schema": {"type": "object", "required": ["answer", "citations"]},
                    },
                    {"type": "pii"},
                    {"type": "tool_selection", "first": "retrieve"},
                ],
                "expected": {
                    "judge": {"scores": {"relevance": 1.0, "grounding": 1.0}, "pass": True}
                },
                "tags": ["support-rag"],
            }
        )
    return cases


def _sql_cases() -> list[dict]:
    cases = []
    for i, (question, gold) in enumerate(SQL_QA, 1):
        cases.append(
            {
                "id": f"sql-{i:03d}",
                "input": {"question": question},
                "checks": [
                    {"type": "sql_select_only"},
                    {"type": "sql_exec_accuracy", "db": DB_REL, "gold_sql": gold},
                ],
                "expected": {"judge": {"scores": {"correctness": 1.0}, "pass": True}},
                "tags": ["sql-analyst"],
            }
        )
    return cases


def _meta_cases(rubric_sha: str) -> list[dict]:
    cases = []
    for c in CANARIES:
        gold = c["gold_pass"]
        cases.append(
            {
                "id": c["id"],
                "input": {"answer": c["answer"]},
                "checks": [
                    {
                        "type": "rubric_integrity",
                        "rubric_path": META_RUBRIC_REL,
                        "expected_sha": rubric_sha,
                    }
                ],
                "expected": {"judge": {"scores": {"quality": 1.0 if gold else 0.0}, "pass": gold}},
                "tags": ["meta-judge"],
            }
        )
    return cases


def _seed_canary_calibration(conn, blob_root, base_run: str, spec: dict) -> None:
    """Judge each canary and record its gold verdict as a label.

    The meta-judge score stays L1 (rubric_integrity), so a bad-answer canary is
    not penalized for being correctly rejected. Judging plus the gold labels give
    the calibration page a drift check to render, clearly a stand-in for the real
    human-labeled kappa.
    """
    for case in spec["cases"]:
        row = conn.execute(
            "SELECT output_sha FROM results WHERE run_id = ? AND case_id = ?",
            (base_run, case["id"]),
        ).fetchone()
        output = get(blob_root, row["output_sha"])
        judge_case(conn, case, output, row["output_sha"], spec["rubric"], DEMO_ROSTER)
        gold = case["expected"]["judge"]["pass"]
        add_label(conn, case["id"], row["output_sha"], gold, labeler="canary-gold")
    conn.commit()


def build_demodata(dest: str | Path) -> dict:
    """Build the full demo workspace at dest and return a manifest."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "agents" / "sql_analyst").mkdir(parents=True)
    (dest / "blobs").mkdir(parents=True)
    for s in ("support-rag", "sql-analyst", "meta-judge"):
        (dest / "suites" / s).mkdir(parents=True)

    build_db(dest / DB_REL)

    (dest / "suites/support-rag/rubric.yaml").write_text(
        yaml.safe_dump(SUPPORT_RUBRIC, sort_keys=False)
    )
    (dest / "suites/sql-analyst/rubric.yaml").write_text(
        yaml.safe_dump(SQL_RUBRIC, sort_keys=False)
    )
    meta_path = dest / META_RUBRIC_REL
    meta_path.write_text(yaml.safe_dump(META_RUBRIC, sort_keys=False))
    meta_sha = sha256_hex(meta_path.read_bytes())

    suites = {
        "support-rag": {"rubric": SUPPORT_RUBRIC, "cases": _support_cases()},
        "sql-analyst": {"rubric": SQL_RUBRIC, "cases": _sql_cases()},
        "meta-judge": {"rubric": META_RUBRIC, "cases": _meta_cases(meta_sha)},
    }
    for sid, spec in suites.items():
        with open(dest / "suites" / sid / "cases.jsonl", "w") as f:
            for c in spec["cases"]:
                f.write(json.dumps(c) + "\n")

    cwd = os.getcwd()
    os.chdir(dest)
    try:
        conn = connect("traces.db")
        blob_root = Path("blobs")
        for sid, spec in suites.items():
            agent, responder = agent_and_responder(sid)
            run_suite(
                conn,
                blob_root,
                suite_id=sid,
                cases=spec["cases"],
                agent=agent,
                mode="record",
                responder=responder,
            )
            base_run = run_suite(
                conn,
                blob_root,
                suite_id=sid,
                cases=spec["cases"],
                agent=agent,
                mode="frozen",
                responder=responder,
            )
            if sid == "meta-judge":
                _seed_canary_calibration(conn, blob_root, base_run, spec)
            else:
                judge_run(
                    conn,
                    blob_root,
                    base_run,
                    {c["id"]: c for c in spec["cases"]},
                    spec["rubric"],
                    DEMO_ROSTER,
                )
            promote(conn, f"baseline-{sid}", sid, base_run)
            spec["baseline_run"] = base_run
        conn.commit()
        conn.close()
    finally:
        os.chdir(cwd)

    manifest = {
        "suites": {sid: {"cases": len(spec["cases"])} for sid, spec in suites.items()},
        "total_cases": sum(len(spec["cases"]) for spec in suites.values()),
        "baselines": {sid: spec["baseline_run"] for sid, spec in suites.items()},
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
