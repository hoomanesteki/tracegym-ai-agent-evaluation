"""The regression gate: one pure predicate, shared by CI and the advisor.

A candidate run is compared against a promoted baseline over the cases they share.
The gate returns one of three verdicts:

  BLOCK  a high-confidence regression the merge must not pass:
         1. a new invariant failure (a case that broke a safety rule it used to pass),
         2. a mean per-case score drop past the threshold whose 95% bootstrap CI
            stays below zero (statistically real, not noise),
         3. a cost increase beyond the hard percent,
         4. a paired flip/sign test: too many cases flipped pass->fail to be chance
            (catches a success-rate regression the mean-delta CI can miss).
  WARN   a soft signal worth a human's eyes but not a merge block: a mean drop whose
         CI still crosses zero, or a cost rise in the soft band.
  PASS   nothing fired.

BLOCK is byte-for-byte backward compatible (`.blocked` is true only for BLOCK), so
adding WARN never changes a previously-blocking decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import comb

from tracegym.config import GateConfig
from tracegym.gate.bootstrap import paired_bootstrap


@dataclass
class GateResult:
    verdict: str  # PASS | WARN | BLOCK
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mean_delta: float = 0.0
    ci_low: float = 0.0
    ci_high: float = 0.0
    cost_delta_pct: float = 0.0
    new_invariant_fails: int = 0
    flips: tuple[int, int] = (0, 0)  # (pass->fail, fail->pass)
    churn_cases: list[str] = field(default_factory=list)
    n_cases: int = 0

    @property
    def blocked(self) -> bool:
        return self.verdict == "BLOCK"


def _binomial_tail(b: int, n: int) -> float:
    """One-sided P(X >= b) for X ~ Binomial(n, 0.5). Exact, stdlib only."""
    if n == 0:
        return 1.0
    return sum(comb(n, i) for i in range(b, n + 1)) / (2**n)


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
    zero_base_cost_jump = base_cost <= 0 < cand_cost
    cost_delta_pct = ((cand_cost - base_cost) / base_cost * 100) if base_cost > 0 else 0.0

    # Paired flip / exact sign test on pass<->fail transitions at the threshold.
    t = cfg.success_threshold
    b = sum(1 for c in shared if base_scores[c] >= t and cand_scores[c] < t)
    c_flip = sum(1 for c in shared if cand_scores[c] >= t and base_scores[c] < t)
    flip_p = _binomial_tail(b, b + c_flip)

    reasons: list[str] = []
    warnings: list[str] = []

    # No shared cases means the two runs were never actually compared on quality;
    # a silent PASS there would be a CI false negative, so surface it to a human.
    if not shared:
        warnings.append("no shared cases between candidate and reference; nothing was compared")

    if cfg.block_on_l1_invariant_regression and new_invariant_fails > 0:
        reasons.append(f"{new_invariant_fails} new invariant failure(s)")

    ci_excludes_zero = ci_high < 0 or ci_low > 0
    enough_cases = len(shared) >= cfg.min_cases_for_ci
    if enough_cases and mean_delta < cfg.delta_block:
        if ci_excludes_zero or not cfg.ci_must_exclude_zero:
            reasons.append(
                f"mean score delta {mean_delta:+.3f} < {cfg.delta_block} "
                f"(95% CI [{ci_low:+.3f}, {ci_high:+.3f}], n={len(shared)})"
            )
        else:
            warnings.append(
                f"mean score dipped {mean_delta:+.3f} but the 95% CI "
                f"[{ci_low:+.3f}, {ci_high:+.3f}] still crosses zero"
            )

    if cfg.block_on_flip_test and b >= cfg.flip_min_b and flip_p < cfg.flip_alpha:
        reasons.append(
            f"{b} cases flipped pass->fail vs {c_flip} the other way "
            f"(exact p={flip_p:.3f} < {cfg.flip_alpha})"
        )

    if cost_delta_pct > cfg.cost_regression_pct:
        reasons.append(f"cost up {cost_delta_pct:+.1f}% > {cfg.cost_regression_pct}%")
    elif cost_delta_pct > cfg.soft_cost_pct:
        warnings.append(f"cost up {cost_delta_pct:+.1f}% (soft threshold {cfg.soft_cost_pct}%)")
    elif zero_base_cost_jump:
        # A percent is undefined against a $0 baseline, so the free-to-paid jump
        # would otherwise slip through silently. Flag it for a human.
        warnings.append(f"cost rose from $0 to ${cand_cost:.4f} (was free-tier)")

    verdict = "BLOCK" if reasons else ("WARN" if warnings else "PASS")
    return GateResult(
        verdict=verdict,
        reasons=reasons,
        warnings=warnings,
        mean_delta=round(mean_delta, 4),
        ci_low=round(ci_low, 4),
        ci_high=round(ci_high, 4),
        cost_delta_pct=round(cost_delta_pct, 2),
        new_invariant_fails=new_invariant_fails,
        flips=(b, c_flip),
        n_cases=len(shared),
    )
