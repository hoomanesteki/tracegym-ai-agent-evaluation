"""End-to-end demo: build a workspace, replay it, and check the headline numbers."""

from __future__ import annotations

from typer.testing import CliRunner

from tracegym.cli import app
from tracegym.demos.run import run_demo
from tracegym.report import render_report


def test_demo_pipeline_produces_expected_numbers(tmp_path):
    bundle = run_demo(tmp_path / "ws")
    assert bundle["manifest"]["total_cases"] == 32
    assert bundle["recall"]["caught"] == bundle["recall"]["total"] == 10
    assert bundle["determinism"]["pct"] == 100.0
    assert bundle["gate_demo"]["verdict"] == "BLOCK"
    assert bundle["gate_demo"]["new_invariant_fails"] == 12
    # Every agent-suite case passes at baseline.
    assert bundle["scorecards"]["sql-analyst"]["task_success_rate"] == 1.0
    assert bundle["scorecards"]["support-rag"]["task_success_rate"] == 1.0


def test_report_renders_self_contained_html(tmp_path):
    bundle = run_demo(tmp_path / "ws")
    out = render_report(bundle, tmp_path / "report.html")
    html = out.read_text()
    assert html.startswith("<!doctype html>")
    assert "http://" not in html.replace("http-equiv", "")  # no external asset fetches
    assert "Seeded regressions caught" in html
    assert "BLOCK" in html


def test_cli_version():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout
