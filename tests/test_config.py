"""Config loads with real files and falls back cleanly with none."""

from __future__ import annotations

from tracegym.config import cost_usd, load_config, load_prices


def test_load_config_reads_repo_config():
    cfg = load_config("tracegym.yaml")
    assert cfg.demo_model == "llama-3.1-8b-instant"
    assert cfg.judges["primary"].provider == "gemini"
    assert cfg.gate.bootstrap_samples == 10000


def test_load_config_uses_defaults_when_missing(tmp_path):
    cfg = load_config(tmp_path / "does-not-exist.yaml")
    assert cfg.gate.delta_block == -0.15
    assert cfg.judge_pass_threshold == 0.6


def test_cost_usd_prices_known_and_unknown_models():
    prices = load_prices("configs/prices.yaml")
    known = cost_usd(prices, "groq", "llama-3.1-8b-instant", 1_000_000, 1_000_000)
    assert known == 0.13  # 0.05 + 0.08
    unknown = cost_usd(prices, "acme", "mystery-model", 1_000_000, 0)
    assert unknown == 0.10  # default input price
