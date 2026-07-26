"""Render the eval report to a single self-contained HTML file.

One page, no external assets: KPI tiles, the agent scorecard, the benchmark-vs
-reference gate result, the efficiency ledger with validated recommendations, the
needs-review count, and the calibration panel. Charts are CSS bars, so nothing is
fetched at view time.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, select_autoescape

from tracegym.report.charts import linechart, waterfall
from tracegym.spc import drift_check
from tracegym.timeseries import METRICS, delta, metric_series

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


def render_output(obj) -> str:
    """Readable form of an agent output: raw SQL, or answer + citations, or JSON."""
    if isinstance(obj, dict):
        if isinstance(obj.get("sql"), str):
            return obj["sql"]
        if isinstance(obj.get("answer"), str):
            cites = obj.get("citations")
            tail = f"\n\ncitations: {cites}" if cites else ""
            return obj["answer"] + tail
    return json.dumps(obj, indent=2, ensure_ascii=False)


def _case_view(d: dict) -> dict:
    question = (d.get("input") or {}).get("question") if isinstance(d.get("input"), dict) else None
    return {
        "case_id": d["case_id"],
        "question": question or json.dumps(d.get("input"), ensure_ascii=False),
        "output": render_output(d["output"]),
        "checks": d["checks"],
        "judge": d["judge"],
        "score": d["score"],
        "cost_usd": d["cost_usd"],
        "latency_ms": d["latency_ms"],
        "spans": d["spans"],
        "invariant_fail": d["invariant_fail"],
    }


def _gallery_view(g: dict) -> dict:
    ex = g.get("example")
    view = {"id": g["id"], "suite": g["suite"], "desc": g["desc"], "caught": g["caught"]}
    if ex:
        question = (
            (ex.get("input") or {}).get("question") if isinstance(ex.get("input"), dict) else None
        )
        view["example"] = {
            "case_id": ex["case_id"],
            "question": question or "",
            "baseline_output": render_output(ex["baseline_output"]),
            "buggy_output": render_output(ex["buggy_output"]),
            "failing_checks": ex["failing_checks"],
        }
    return view


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


_SPC_RANK = {"drift": 0, "recovered": 1}


def _spc_summary(hist: list[dict]) -> dict | None:
    """The most notable SPC verdict across a suite's metrics, or None if all quiet."""
    hits = []
    for metric, (label, direction) in METRICS.items():
        r = drift_check(metric_series(hist, metric), direction)
        if r["status"] in _SPC_RANK:
            hits.append((r["status"], label, r.get("change_point")))
    if not hits:
        return None
    hits.sort(key=lambda h: _SPC_RANK[h[0]])
    status, label, cp = hits[0]
    return {
        "status": status,
        "label": label,
        "shift_run": None if cp is None else cp + 1,
    }


def _trends(history_by_suite: dict) -> dict:
    out = {}
    for sid, hist in (history_by_suite or {}).items():
        if len(hist) < 2:
            continue
        labels = [f"{h['created_at']} {h['git_sha']}" for h in hist]
        charts = []
        for metric, (label, direction) in METRICS.items():
            vals = metric_series(hist, metric)
            d = delta(hist, metric)
            good = None if d == 0 else (d > 0 if direction == "up" else d < 0)
            charts.append(
                {
                    "metric": metric,
                    "label": label,
                    "latest": vals[-1],
                    "delta": d,
                    "delta_good": good,
                    "chart": linechart(vals, labels=labels),
                }
            )
        out[sid] = {
            "runs": len(hist),
            "synthetic": hist[0].get("synthetic", False),
            "charts": charts,
            "spc": _spc_summary(hist),
        }
    return out


def _loop(bundle: dict) -> dict:
    recs = bundle.get("recommendations", [])
    return {
        "determinism_pct": bundle.get("determinism", {}).get("pct", 0),
        "gate_verdict": bundle["gate_demo"]["verdict"],
        "needs_review": bundle.get("needs_review", 0),
        "review_open": len(bundle.get("review", [])),
        "n_safe": sum(1 for r in recs if r.get("status") == "SAFE"),
    }


_KIND_LABEL = {
    "needs_review": "judge review",
    "gate_warn": "gate warning",
    "drift": "drift alert",
}


def _multiagent(bundle: dict) -> dict | None:
    ma = bundle.get("multiagent")
    if not ma or not ma.get("trajectory"):
        return None
    return {
        "case_id": ma["case_id"],
        "agents": ma.get("agents", []),
        "chart": waterfall(ma["trajectory"]),
    }


def _review_view(items: list[dict]) -> list[dict]:
    return [
        {
            "id": it["id"],
            "kind": _KIND_LABEL.get(it["kind"], it["kind"]),
            "severity": it.get("severity") or "med",
            "ref": it.get("ref_id") or "",
            "reason": it.get("reason") or "",
        }
        for it in (items or [])
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
        "gallery": [_gallery_view(g) for g in bundle.get("regression_gallery", [])],
        "case_details": {
            sid: [_case_view(d) for d in cases]
            for sid, cases in bundle.get("case_details", {}).items()
        },
        "trends": _trends(bundle.get("history", {})),
        "loop": _loop(bundle),
        "review": _review_view(bundle.get("review", [])),
        "multiagent": _multiagent(bundle),
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
