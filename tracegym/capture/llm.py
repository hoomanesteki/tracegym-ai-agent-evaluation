"""LLM providers behind a single record shape.

Every provider returns the same dict so the runtime does not care who answered:

    {"content": str, "usage": {"input_tokens": int, "output_tokens": int},
     "model": str, "provider": str}

`local_chat` is a deterministic stand-in used to bootstrap fixtures with no keys
(the demo agents supply their own canned responder on top of it). `groq_chat` is
the live path; it needs the `judges` extra and a GROQ_API_KEY.
"""

from __future__ import annotations

import os

from tracegym.util.canon import canonical_json, sha256_hex


def estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars/token). Good enough for cost and budget math."""
    return max(1, len(text) // 4)


def record_from_content(content: str, model: str, provider: str, prompt: str = "") -> dict:
    """Wrap a bare content string into the common record shape with token counts."""
    return {
        "content": content,
        "usage": {
            "input_tokens": estimate_tokens(prompt),
            "output_tokens": estimate_tokens(content),
        },
        "model": model,
        "provider": provider,
    }


def _prompt_text(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content", "")) for m in messages)


def local_chat(messages: list[dict], model: str, seed: int = 0) -> dict:
    """Deterministic offline responder. Content is a stable function of the input,
    so recording fixtures needs no network and reruns are byte-identical."""
    prompt = _prompt_text(messages)
    digest = sha256_hex(canonical_json({"m": messages, "s": seed}))[:8]
    content = f"[local:{model}] acknowledged ({digest})"
    return record_from_content(content, model, "local", prompt)


def groq_chat(messages: list[dict], model: str, **params) -> dict:
    """Live call to Groq's OpenAI-compatible endpoint. Streaming is not used."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - needs the extra
        raise RuntimeError("groq_chat needs the extra: uv sync --extra judges") from exc

    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set; cannot make a live Groq call")

    base = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    resp = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": messages, "stream": False, **params},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usage", {})
    return {
        "content": data["choices"][0]["message"]["content"],
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
        "model": data.get("model", model),
        "provider": "groq",
    }
