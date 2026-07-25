"""Rubric integrity: pin the rubric so tampering is caught without re-judging.

Judgments are cached by rubric hash, so a tampered rubric would quietly miss the
cache. This check pins the expected hash of the rubric file; if the rubric is
edited, the hash no longer matches and the meta-judge canary fails as an
invariant. It catches judge-rubric tampering deterministically and keyless.
"""

from __future__ import annotations

from pathlib import Path

from tracegym.checks.base import CheckResult, register
from tracegym.util.canon import sha256_hex


@register("rubric_integrity")
def check_rubric_integrity(trace: dict, spec: dict) -> CheckResult:
    path = spec.get("rubric_path")
    expected = spec.get("expected_sha")
    if not path or not expected:
        return CheckResult(
            "rubric_integrity", False, "spec needs rubric_path and expected_sha", invariant=True
        )
    try:
        content = Path(path).read_bytes()
    except OSError as exc:
        return CheckResult("rubric_integrity", False, f"cannot read rubric: {exc}", invariant=True)
    got = sha256_hex(content)
    ok = got == expected
    detail = "rubric unchanged" if ok else "rubric hash differs from the pinned value (tampered)"
    return CheckResult("rubric_integrity", ok, detail, invariant=True)
