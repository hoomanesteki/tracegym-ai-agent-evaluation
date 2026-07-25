"""The L1 check registry.

L1 checks are deterministic, keyless, and fast: they read a replayed trace plus a
per-check spec and return a CheckResult. Some checks are invariants (safety rules
like "never emit DML" or "never leak PII"); a single invariant failure is a hard
gate block regardless of the quality score. Checks register themselves by name so
a golden case just lists the checks it wants.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    invariant: bool = False  # a failed invariant blocks the gate no matter the score

    def to_dict(self) -> dict:
        return asdict(self)


# name -> check function. A check takes (trace, spec) and returns a CheckResult.
CHECKS: dict[str, Callable[[dict, dict], CheckResult]] = {}


def register(name: str) -> Callable:
    """Register a check under ``name`` so cases can reference it by string."""

    def decorator(fn: Callable[[dict, dict], CheckResult]) -> Callable:
        CHECKS[name] = fn
        return fn

    return decorator


def run_checks(trace: dict, case: dict) -> list[CheckResult]:
    """Run the case's checks in order. Unknown check names fail loudly."""
    results: list[CheckResult] = []
    for spec in case.get("checks", []):
        if isinstance(spec, str):
            spec = {"type": spec}
        name = spec.get("type", "")
        fn = CHECKS.get(name)
        if fn is None:
            results.append(CheckResult(name or "<missing type>", False, f"unknown check: {name!r}"))
            continue
        try:
            results.append(fn(trace, spec))
        except Exception as exc:  # a broken check must not crash the run
            results.append(CheckResult(name, False, f"check raised: {exc}"))
    return results


# -- small helpers shared by checks ------------------------------------------


def answer_text(trace: dict) -> str:
    """Best-effort plain text of the agent's final answer."""
    out = trace.get("output")
    if isinstance(out, str):
        return out
    if isinstance(out, dict):
        for key in ("answer", "text", "content", "message"):
            if isinstance(out.get(key), str):
                return out[key]
    return str(out)


def get_field(trace: dict, field: str, default: Any = None) -> Any:
    """Read a dotted field from the agent output (e.g. "sql", "result.rows")."""
    node: Any = trace.get("output")
    for part in field.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def tool_spans(trace: dict) -> list[dict]:
    """Ordered tool spans from the trace (already sorted by start time)."""
    spans = [s for s in trace.get("spans", []) if s.get("kind") == "tool"]
    return sorted(spans, key=lambda s: s.get("start_ns", 0))
