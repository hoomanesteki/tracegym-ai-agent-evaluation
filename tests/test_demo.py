"""End-to-end demo: build a workspace, replay it, and check the headline numbers."""

from __future__ import annotations

import builtins
import re
from pathlib import Path

import typer
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


def test_docs_reference_only_real_commands():
    """Every `tg <cmd>` / `tracegym <cmd>` shown in the docs must be a real command."""
    commands = set(typer.main.get_command(app).commands.keys())
    root = Path(__file__).resolve().parent.parent
    docs = [root / "README.md", *sorted((root / "docs").glob("*.qmd"))]

    referenced: set[str] = set()
    for doc in docs:
        text = doc.read_text()
        # Command tokens shown inside inline code spans.
        for span in re.findall(r"`([^`]+)`", text):
            m = re.match(r"(?:tg|tracegym)\s+([a-z][a-z-]*)", span.strip())
            if m:
                referenced.add(m.group(1))
        # Command tokens shown at the start of a line in a fenced code block.
        for m in re.finditer(r"(?m)^\s*(?:tg|tracegym)\s+([a-z][a-z-]*)", text):
            referenced.add(m.group(1))

    missing = referenced - commands
    assert not missing, f"docs reference unknown commands: {sorted(missing)}"


def test_record_missing_proxy_extra_names_the_extra(monkeypatch):
    """Without the proxy extra, the hint must name `tracegym[proxy]` intact.

    Rich reads a bare `[proxy]` as a style tag and would silently drop it, leaving
    a misleading `pip install "tracegym"` that does not add the extra.
    """
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "uvicorn":
            raise ImportError("uvicorn not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = CliRunner().invoke(app, ["record", "--proxy"])
    assert result.exit_code == 1
    assert "tracegym[proxy]" in result.stdout
