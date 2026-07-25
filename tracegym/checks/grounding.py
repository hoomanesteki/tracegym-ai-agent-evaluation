"""Context fidelity: every id the answer cites must come from retrieved context.

Grounding is the antidote to a confident hallucination: an answer may only cite
document ids that actually appeared in a retrieval tool's output during the trace.
A citation to something never retrieved fails the check. This is what the report
calls the "context fidelity" rate.
"""

from __future__ import annotations

from tracegym.checks.base import CheckResult, get_field, register, tool_spans


def _ids_in(obj) -> set[str]:
    """Collect any "id" values found anywhere in a nested structure."""
    found: set[str] = set()
    if isinstance(obj, dict):
        if isinstance(obj.get("id"), (str, int)):
            found.add(str(obj["id"]))
        for value in obj.values():
            found |= _ids_in(value)
    elif isinstance(obj, list):
        for item in obj:
            found |= _ids_in(item)
    return found


@register("citation")
def check_citation(trace: dict, spec: dict) -> CheckResult:
    cited = get_field(trace, spec.get("field", "citations")) or []
    cited = [str(c) for c in cited] if isinstance(cited, list) else [str(cited)]

    retrieved: set[str] = set()
    for span in tool_spans(trace):
        retrieved |= _ids_in(span.get("output"))

    require = spec.get("require", True)
    if require and not cited:
        return CheckResult("citation", False, "answer cites no sources")

    unsupported = [c for c in cited if c not in retrieved]
    ok = not unsupported
    detail = "all citations grounded" if ok else f"unsupported citations: {unsupported}"
    return CheckResult("citation", ok, detail)
