"""String and schema matchers: contains, not_contains, regex, schema_valid."""

from __future__ import annotations

import re

import jsonschema

from tracegym.checks.base import CheckResult, answer_text, get_field, register


@register("contains")
def check_contains(trace: dict, spec: dict) -> CheckResult:
    value = spec.get("value", "")
    ok = value in answer_text(trace)
    return CheckResult(
        spec.get("name", "contains"), ok, f"{'found' if ok else 'missing'}: {value!r}"
    )


@register("not_contains")
def check_not_contains(trace: dict, spec: dict) -> CheckResult:
    value = spec.get("value", "")
    ok = value not in answer_text(trace)
    return CheckResult(
        spec.get("name", "not_contains"), ok, f"{'absent' if ok else 'present'}: {value!r}"
    )


@register("regex")
def check_regex(trace: dict, spec: dict) -> CheckResult:
    pattern = spec.get("pattern", "")
    ok = re.search(pattern, answer_text(trace)) is not None
    return CheckResult(
        spec.get("name", "regex"), ok, f"pattern {pattern!r} {'matched' if ok else 'no match'}"
    )


@register("schema_valid")
def check_schema_valid(trace: dict, spec: dict) -> CheckResult:
    schema = spec.get("schema", {})
    instance = get_field(trace, spec["field"]) if "field" in spec else trace.get("output")
    try:
        jsonschema.validate(instance, schema)
        return CheckResult("schema_valid", True, "valid")
    except jsonschema.ValidationError as exc:
        return CheckResult("schema_valid", False, f"invalid: {exc.message}")
