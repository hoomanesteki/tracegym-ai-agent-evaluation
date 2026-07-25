"""Load and expose project configuration.

One loader reads tracegym.yaml, configs/prices.yaml, and limits.yaml so the rest
of the code never touches YAML directly. Defaults are baked in, so a caller can
run with no config file at all (the demo relies on that).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class JudgeRole:
    provider: str
    model: str


@dataclass(frozen=True)
class GateConfig:
    bootstrap_samples: int = 10000
    delta_block: float = -0.15
    ci_must_exclude_zero: bool = True
    cost_regression_pct: float = 50.0
    block_on_l1_invariant_regression: bool = True


@dataclass(frozen=True)
class AdvisorConfig:
    budget_cap_usd: float = 5.0
    budget_window_days: int = 7
    bootstrap_seed: int = 1729
    top_k: int = 10
    latency_cap_ms: float = 8000.0


@dataclass(frozen=True)
class Config:
    schema_version: int = 1
    semconv_genai_version: str = "1.42.0"
    demo_provider: str = "groq"
    demo_model: str = "llama-3.1-8b-instant"
    judges: dict[str, JudgeRole] = field(default_factory=dict)
    judge_pass_threshold: float = 0.6
    judge_max_retries: int = 2
    gate: GateConfig = field(default_factory=GateConfig)
    advisor: AdvisorConfig = field(default_factory=AdvisorConfig)
    paths: dict[str, str] = field(default_factory=dict)
    root: Path = field(default_factory=Path.cwd)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def load_config(path: str | Path = "tracegym.yaml") -> Config:
    """Read tracegym.yaml, filling in defaults for anything absent."""
    p = Path(path)
    root = p.parent if p.is_absolute() else Path.cwd()
    raw = _read_yaml(p)

    otel = raw.get("otel", {})
    agents = raw.get("demo_agents", {})
    j = raw.get("judges", {})
    judges = {
        role: JudgeRole(**j[role]) for role in ("primary", "secondary", "tiebreaker") if role in j
    }
    g = raw.get("gate", {})
    gate = GateConfig(
        bootstrap_samples=g.get("bootstrap_samples", 10000),
        delta_block=g.get("delta_block", -0.15),
        ci_must_exclude_zero=g.get("ci_must_exclude_zero", True),
        cost_regression_pct=g.get("cost_regression_pct", 50.0),
        block_on_l1_invariant_regression=g.get("block_on_l1_invariant_regression", True),
    )
    a = raw.get("advisor", {})
    advisor = AdvisorConfig(
        budget_cap_usd=a.get("budget_cap_usd", 5.0),
        budget_window_days=a.get("budget_window_days", 7),
        bootstrap_seed=a.get("bootstrap_seed", 1729),
        top_k=a.get("top_k", 10),
        latency_cap_ms=a.get("latency_cap_ms", 8000.0),
    )
    return Config(
        schema_version=raw.get("schema_version", 1),
        semconv_genai_version=otel.get("semconv_genai_version", "1.42.0"),
        demo_provider=agents.get("provider", "groq"),
        demo_model=agents.get("model", "llama-3.1-8b-instant"),
        judges=judges,
        judge_pass_threshold=j.get("pass_threshold", 0.6),
        judge_max_retries=j.get("max_retries", 2),
        gate=gate,
        advisor=advisor,
        paths=raw.get("paths", {}),
        root=root,
    )


def load_prices(path: str | Path = "configs/prices.yaml") -> dict[str, Any]:
    """Return the price table plus a `default` entry for unknown models."""
    raw = _read_yaml(Path(path))
    table = raw.get("prices", {})
    table["default"] = raw.get("default", {"input": 0.10, "output": 0.30})
    return table


def cost_usd(
    prices: dict[str, Any],
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Counterfactual cost for one call, in USD. Free-tier calls still get priced
    so the gate can detect a cost regression."""
    entry = prices.get(f"{provider}/{model}") or prices.get("default", {})
    per_in = entry.get("input", 0.0) / 1_000_000
    per_out = entry.get("output", 0.0) / 1_000_000
    return round(input_tokens * per_in + output_tokens * per_out, 8)


def load_limits(path: str | Path = "limits.yaml") -> dict[str, Any]:
    """Observed rate limits, refreshed by scripts/probe_limits.py."""
    return _read_yaml(Path(path))
