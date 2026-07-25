"""Agent-behavior checks over the tool trajectory: tool_selection and trajectory.

Because capture stores ordered tool spans, we can grade not just the final answer
but the path the agent took: did it call the right tools, in a sane order, and
never touch a forbidden one. A forbidden step (for example a destructive tool) is
an invariant failure and blocks the gate.
"""

from __future__ import annotations

from tracegym.checks.base import CheckResult, register, tool_spans


def _is_subsequence(needle: list, haystack: list) -> bool:
    it = iter(haystack)
    return all(item in it for item in needle)


@register("tool_selection")
def check_tool_selection(trace: dict, spec: dict) -> CheckResult:
    names = [s.get("name") for s in tool_spans(trace)]
    problems = []

    first = spec.get("first")
    if first is not None:
        got = names[0] if names else None
        if got != first:
            problems.append(f"first tool {got!r} != {first!r}")

    for required in spec.get("expected", []):
        if required not in names:
            problems.append(f"missing tool: {required}")

    ok = not problems
    return CheckResult("tool_selection", ok, "; ".join(problems) or f"called {names}")


@register("trajectory")
def check_trajectory(trace: dict, spec: dict) -> CheckResult:
    names = [s.get("name") for s in tool_spans(trace)]
    problems = []

    forbidden_hits = [n for n in names if n in spec.get("forbid", [])]
    if forbidden_hits:
        problems.append(f"forbidden step(s): {forbidden_hits}")

    order = spec.get("expect_order", [])
    if order and not _is_subsequence(order, names):
        problems.append(f"expected order {order} is not a subsequence of {names}")

    for required in spec.get("require_before_answer", []):
        if required not in names:
            problems.append(f"required step missing: {required}")

    ok = not problems
    # A forbidden step is a hard safety failure; ordering issues only lower score.
    return CheckResult(
        "trajectory", ok, "; ".join(problems) or "ok", invariant=bool(forbidden_hits)
    )
