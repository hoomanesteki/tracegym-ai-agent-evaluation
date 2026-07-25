#!/usr/bin/env python
"""Measure free-tier rate limits and write them to limits.yaml.

Providers no longer publish reliable static free-tier tables, so we measure.
For every provider whose API key is present in the environment, send a light
request once per interval, ramp up, and record the first HTTP 429. Providers
with no key are skipped, so this is safe to run with only some keys set.

Usage:
    GROQ_API_KEY=... GEMINI_API_KEY=... python scripts/probe_limits.py
    python scripts/probe_limits.py --requests 12 --model llama-3.1-8b-instant
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover - script-only dependency
    sys.exit("probe_limits needs httpx: uv sync --extra proxy")

import yaml

ROOT = Path(__file__).resolve().parent.parent

# (env var, provider key in limits.yaml, default model, request builder)
PROVIDERS = {
    "groq": {
        "env": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "default_model": "llama-3.1-8b-instant",
    },
    "gemini": {
        "env": "GEMINI_API_KEY",
        "url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "default_model": "gemini-2.5-flash",
    },
}


def _one_call(provider: str, spec: dict, model: str, key: str) -> int:
    """Send one tiny request, return the HTTP status code."""
    if provider == "groq":
        r = httpx.post(
            spec["url"],
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "stream": False,
            },
            timeout=30,
        )
    else:  # gemini
        r = httpx.post(
            spec["url"].format(model=model),
            params={"key": key},
            json={"contents": [{"parts": [{"text": "ping"}]}]},
            timeout=30,
        )
    return r.status_code


def probe(provider: str, model: str, requests: int, interval: float) -> dict:
    spec = PROVIDERS[provider]
    key = os.environ.get(spec["env"])
    if not key:
        return {}
    observed_rpm = None
    hit_429 = False
    start = time.time()
    for i in range(requests):
        try:
            status = _one_call(provider, spec, model, key)
        except Exception as exc:  # network hiccup, keep going
            print(f"  {provider} call {i}: error {exc}")
            continue
        elapsed = time.time() - start
        print(f"  {provider} call {i}: HTTP {status} at {elapsed:.1f}s")
        if status == 429:
            hit_429 = True
            observed_rpm = i  # calls that went through before the limit
            break
        time.sleep(interval)
    return {
        model: {
            "rpm_observed": observed_rpm if hit_429 else f">={requests}",
            "rpd_observed": None,
            "probed_at": time.strftime("%Y-%m-%d %H:%M"),
        }
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--requests", type=int, default=12, help="max probe calls per provider")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between calls")
    ap.add_argument("--model", default=None, help="override the model for every provider")
    ap.add_argument("--out", default=str(ROOT / "limits.yaml"))
    args = ap.parse_args()

    existing = {}
    out = Path(args.out)
    if out.exists():
        existing = yaml.safe_load(out.read_text()) or {}

    probed_any = False
    for provider, spec in PROVIDERS.items():
        if not os.environ.get(spec["env"]):
            print(f"skip {provider}: {spec['env']} not set")
            continue
        probed_any = True
        model = args.model or spec["default_model"]
        print(f"probing {provider} / {model} ...")
        result = probe(provider, model, args.requests, args.interval)
        existing.setdefault(provider, {}).update(result)

    if not probed_any:
        print("no provider keys found; nothing probed. Set GROQ_API_KEY / GEMINI_API_KEY.")
        return 0

    out.write_text(yaml.safe_dump(existing, sort_keys=True))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
