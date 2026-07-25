"""Load golden suites from disk: cases.jsonl plus an optional rubric.yaml."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def load_cases(path: str | Path) -> list[dict]:
    """Read a JSONL file of cases (one JSON object per non-empty line)."""
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def load_suite(suite_dir: str | Path) -> tuple[list[dict], dict]:
    """Return (cases, rubric) for a suite directory."""
    suite_dir = Path(suite_dir)
    cases = load_cases(suite_dir / "cases.jsonl")
    rubric_path = suite_dir / "rubric.yaml"
    rubric = yaml.safe_load(rubric_path.read_text()) if rubric_path.exists() else {}
    return cases, rubric or {}
