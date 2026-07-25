"""Judge-vs-human agreement statistics and the calibration ladder.

Cohen's kappa alone is misleading under class imbalance (most agent answers pass),
so we always report raw agreement and PABAK next to it, plus the confusion matrix.
The ladder is pre-decided, not a judgment call: at or above 0.70 the judge gates;
between 0.55 and 0.70 it gates after documented fixes; below 0.55 it is demoted to
advisory and never blocks CI. Reporting the honest number beats faking 0.70.
"""

from __future__ import annotations


def raw_agreement(human: list[int], judge: list[int]) -> float:
    if not human:
        return 0.0
    return sum(1 for h, j in zip(human, judge, strict=True) if h == j) / len(human)


def cohen_kappa(human: list[int], judge: list[int]) -> float:
    """Cohen's kappa for two binary raters. Returns 1.0 for perfect, ~0 for chance."""
    n = len(human)
    if n == 0:
        return 0.0
    po = raw_agreement(human, judge)
    p_h = sum(human) / n
    p_j = sum(judge) / n
    pe = p_h * p_j + (1 - p_h) * (1 - p_j)
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1 - pe)


def pabak(human: list[int], judge: list[int]) -> float:
    """Prevalence- and bias-adjusted kappa: 2 * observed_agreement - 1."""
    return 2 * raw_agreement(human, judge) - 1


def confusion(human: list[int], judge: list[int]) -> dict:
    """Judge treated as prediction, human as ground truth."""
    tp = sum(1 for h, j in zip(human, judge, strict=True) if h == 1 and j == 1)
    tn = sum(1 for h, j in zip(human, judge, strict=True) if h == 0 and j == 0)
    fp = sum(1 for h, j in zip(human, judge, strict=True) if h == 0 and j == 1)
    fn = sum(1 for h, j in zip(human, judge, strict=True) if h == 1 and j == 0)
    return {"tp": tp, "tn": tn, "fp": fp, "fn": fn}


def ladder(kappa: float) -> tuple[str, str]:
    """Map a kappa value to (tier, action) with a pre-decided policy."""
    if kappa >= 0.70:
        return "ship", "Judge agreement is strong; the judge gates in CI."
    if kappa >= 0.55:
        return (
            "iterate",
            "Apply the documented rubric fixes, then re-run; judge gates on the agreement subset.",
        )
    return (
        "advisory",
        "Agreement is weak; judge is advisory only and never blocks CI. Gate runs on L1 + cost.",
    )


def agreement_report(human: list[int], judge: list[int]) -> dict:
    """Full calibration report for a set of paired (human, judge) verdicts."""
    k = round(cohen_kappa(human, judge), 4)
    tier, action = ladder(k)
    return {
        "n": len(human),
        "raw_agreement": round(raw_agreement(human, judge), 4),
        "cohen_kappa": k,
        "pabak": round(pabak(human, judge), 4),
        "confusion": confusion(human, judge),
        "tier": tier,
        "action": action,
    }


def self_kappa(round1: list[int], round2: list[int]) -> float:
    """Intra-rater agreement: relabel the same items later and compare. This is the
    ceiling; the judge cannot be more consistent with you than you are with yourself."""
    return round(cohen_kappa(round1, round2), 4)
