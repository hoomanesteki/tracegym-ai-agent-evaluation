"""Per-case drill-down data and the tg cases / show / diff commands."""

from __future__ import annotations

import os

from typer.testing import CliRunner

from tracegym.cli import app
from tracegym.demos.run import run_demo


def test_bundle_carries_case_details_and_regression_gallery(tmp_path):
    b = run_demo(tmp_path / "ws")

    sql_cases = b["case_details"]["sql-analyst"]
    assert len(sql_cases) == 12
    d = sql_cases[0]
    assert "SELECT" in str(d["output"])
    assert {c["name"] for c in d["checks"]} >= {"sql_select_only", "sql_exec_accuracy"}
    assert any(s["kind"] == "llm" for s in d["spans"])

    gallery = b["regression_gallery"]
    assert len(gallery) == 10
    assert all(g["caught"] for g in gallery)
    sd = next(g for g in gallery if g["id"] == "sql_delete")
    assert "DELETE" in str(sd["example"]["buggy_output"])
    assert any(c["name"] == "sql_select_only" for c in sd["example"]["failing_checks"])


def _invoke(ws, args):
    """Run a CLI command that chdir's into the workspace, restoring cwd after."""
    cwd = os.getcwd()
    try:
        return CliRunner().invoke(app, args + ["--workspace", str(ws)])
    finally:
        os.chdir(cwd)


def test_cli_show_and_diff(tmp_path):
    ws = tmp_path / "ws"
    run_demo(ws)  # builds the workspace (and restores cwd itself)

    show = _invoke(ws, ["show", "sql-001"])
    assert show.exit_code == 0
    assert "SELECT region" in show.stdout
    assert "sql_select_only" in show.stdout

    diff = _invoke(ws, ["diff", "sql_delete"])
    assert diff.exit_code == 0
    assert "DELETE FROM sales" in diff.stdout
    assert "CAUGHT" in diff.stdout

    cases = _invoke(ws, ["cases"])
    assert cases.exit_code == 0
    assert "sql-001" in cases.stdout
