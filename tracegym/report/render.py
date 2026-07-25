"""Render the eval report to a single self-contained HTML file.

One page, no external assets: KPI tiles, the agent scorecard, the benchmark-vs
-reference gate result, the efficiency ledger with validated recommendations, the
needs-review count, and the calibration panel. Charts are CSS bars, so nothing is
fetched at view time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, select_autoescape

_TEMPLATE = (
    files("tracegym.report").joinpath("templates/report.html.j2").read_text(encoding="utf-8")
)


def _scorecard_rows(scorecards: dict) -> list[dict]:
    rows = []
    for suite_id, sc in scorecards.items():
        if not sc or sc.get("cases", 0) == 0:
            continue
        checks = sorted(sc.get("check_pass_rates", {}).items())
        rows.append(
            {
                "suite": suite_id,
                "cases": sc["cases"],
                "task_success_pct": round(sc["task_success_rate"] * 100, 1),
                "mean_score": sc["mean_score"],
                "tool_calls": sc["total_tool_calls"],
                "invariant_failures": sc["invariant_failures"],
                "p95_latency": sc["latency_ms"]["p95"],
                "cost_usd": sc["total_cost_usd"],
                "checks": [{"name": n, "pct": round(v * 100, 1)} for n, v in checks],
            }
        )
    return rows


def _cost_by_model(profile: dict) -> list[dict]:
    entries = profile.get("cost_by_model", [])
    top = max((e["cost_usd"] for e in entries), default=0) or 1
    return [
        {
            "model": e["model"] or "unknown",
            "cost_usd": e["cost_usd"],
            "pct": round(e["cost_usd"] / top * 100, 1),
        }
        for e in entries
    ]


def build_context(bundle: dict, title: str) -> dict:
    recall = bundle["recall"]
    cal = bundle.get("calibration", {})
    profile = bundle.get("profile", {})
    gate = bundle["gate_demo"]
    total_cost = sum(sc.get("total_cost_usd", 0) for sc in bundle["scorecards"].values())
    return {
        "title": title,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "total_cases": bundle["manifest"]["total_cases"],
        "recall": recall,
        "determinism": bundle.get("determinism", {}),
        "needs_review": bundle.get("needs_review", 0),
        "total_cost": round(total_cost, 6),
        "gate": gate,
        "scorecards": _scorecard_rows(bundle["scorecards"]),
        "profile": profile,
        "cost_by_model": _cost_by_model(profile),
        "recommendations": bundle.get("recommendations", []),
        "calibration": cal,
    }


def render_report(
    bundle: dict, out_path: str | Path, *, title: str = "TraceGym eval report"
) -> Path:
    env = Environment(autoescape=select_autoescape(["html"]))
    template = env.from_string(_TEMPLATE)
    html = template.render(**build_context(bundle, title))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
