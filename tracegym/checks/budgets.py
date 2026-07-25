"""Budget adherence: an agent that answers well but costs or waits too much fails.

These read the recorded per-span cost and latency (preserved through frozen
replay), so budgets are enforced on the numbers the agent actually produced, not
on replay timing.
"""

from __future__ import annotations

from tracegym.capture.otel import COST_USD, LATENCY_MS
from tracegym.checks.base import CheckResult, register


def _sum_attr(trace: dict, attr: str) -> float:
    total = 0.0
    for span in trace.get("spans", []):
        total += float((span.get("attributes") or {}).get(attr, 0) or 0)
    return total


@register("budget")
def check_budget(trace: dict, spec: dict) -> CheckResult:
    max_usd = float(spec.get("max_usd", float("inf")))
    total = _sum_attr(trace, COST_USD)
    ok = total <= max_usd
    return CheckResult("budget", ok, f"cost ${total:.6f} (limit ${max_usd})")


@register("latency")
def check_latency(trace: dict, spec: dict) -> CheckResult:
    max_ms = float(spec.get("max_ms", float("inf")))
    total = _sum_attr(trace, LATENCY_MS)
    ok = total <= max_ms
    return CheckResult("latency", ok, f"latency {total:.1f}ms (limit {max_ms}ms)")
