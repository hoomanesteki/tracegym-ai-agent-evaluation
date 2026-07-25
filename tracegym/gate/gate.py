"""The regression gate: one pure predicate, shared by CI and the advisor.

A candidate run is compared against a promoted baseline over the cases they share.
The gate BLOCKS on any of three high-confidence signals, and only these:

  1. a new invariant failure (a case that broke a safety rule it used to pass),
  2. a mean per-case score drop past the threshold whose 95% bootstrap CI stays
     below zero (a statistically real quality regression, not noise),
  3. a cost increase beyond the allowed percent.

Signal 2 requires the CI to exclude zero on purpose: the gate blocks merges only
when the evidence is strong, so an uncertain wobble warns rather than blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tracegym.config import GateConfig
from tracegym.gate.bootstrap import paired_bootstrap


@dataclass
class GateResult:
    verdict: str  # PASS | BLOCK
    reasons: list[str] = field(default_factory=list)
    mean_delta: float = 0.0
    ci_low: float = 0.0
    ci_high: float = 0.0
    cost_delta_pct: float = 0.0
    new_invariant_fails: int = 0
    n_cases: int = 0

    @property
    def blocked(self) -> bool:
        return self.verdict == "BLOCK"


def gate_verdict(
    cand_scores: dict[str, float],
    base_scores: dict[str, float],
    *,
    cand_invariant_fails: dict[str, int] | None = None,
    base_invariant_fails: dict[str, int] | None = None,
    cand_cost: float = 0.0,
    base_cost: float = 0.0,
    cfg: GateConfig | None = None,
    seed: int = 1729,
) -> GateResult:
    """Pure gate predicate over aligned per-case scores. No I/O, deterministic."""
    cfg = cfg or GateConfig()
    cand_invariant_fails = cand_invariant_fails or {}
    base_invariant_fails = base_invariant_fails or {}

    shared = sorted(set(cand_scores) & set(base_scores))
    deltas = [cand_scores[c] - base_scores[c] for c in shared]
    mean_delta, ci_low, ci_high = paired_bootstrap(deltas, cfg.bootstrap_samples, seed)

    new_invariant_fails = sum(
        1
        for c in shared
        if cand_invariant_fails.get(c, 0) > 0 and base_invariant_fails.get(c, 0) == 0
    )
    cost_delta_pct = ((cand_cost - base_cost) / base_cost * 100) if base_cost > 0 else 0.0

    reasons: list[str] = []
    if cfg.block_on_l1_invariant_regression and new_invariant_fails > 0:
        reasons.append(f"{new_invariant_fails} new invariant failure(s)")

    ci_excludes_zero = ci_high < 0 or ci_low > 0
    if mean_delta < cfg.delta_block and (ci_excludes_zero or not cfg.ci_must_exclude_zero):
        reasons.append(
            f"mean score delta {mean_delta:+.3f} < {cfg.delta_block} "
            f"(95% CI [{ci_low:+.3f}, {ci_high:+.3f}])"
        )

    if cost_delta_pct > cfg.cost_regression_pct:
        reasons.append(f"cost up {cost_delta_pct:+.1f}% > {cfg.cost_regression_pct}%")

    return GateResult(
        verdict="BLOCK" if reasons else "PASS",
        reasons=reasons,
        mean_delta=round(mean_delta, 4),
        ci_low=round(ci_low, 4),
        ci_high=round(ci_high, 4),
        cost_delta_pct=round(cost_delta_pct, 2),
        new_invariant_fails=new_invariant_fails,
        n_cases=len(shared),
    )
