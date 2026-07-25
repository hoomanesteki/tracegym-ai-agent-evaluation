"""LLM judge providers. Each returns {"scores": {...}, "pass": bool, "rationale": str}.

`local_judge` is deterministic and keyless: it honours an authored verdict on the
case (so the bundled demo ships real, stable judgments) and otherwise derives a
stable pseudo-score. `gemini_judge` and `groq_judge` are the live cross-family and
tiebreaker paths; they need the `judges` extra and a key, and force JSON output.
"""

from __future__ import annotations

import json
import os

from tracegym.util.canon import canonical_json, sha256_hex


def criteria(rubric: dict) -> list[dict]:
    return rubric.get("criteria") or [{"id": "quality", "description": "overall answer quality"}]


def _mean(scores: dict) -> float:
    return sum(scores.values()) / len(scores) if scores else 0.0


def local_judge(
    case: dict, output: object, rubric: dict, model: str, threshold: float = 0.6
) -> dict:
    """Deterministic judge. Uses an authored verdict if present, else a stable hash."""
    crits = criteria(rubric)
    authored = (case.get("expected") or {}).get("judge")
    if authored and "scores" in authored:
        scores = {c["id"]: float(authored["scores"].get(c["id"], 1.0)) for c in crits}
        passed = bool(authored.get("pass", _mean(scores) >= threshold))
        return {
            "scores": scores,
            "pass": passed,
            "rationale": authored.get("rationale", "authored"),
        }

    scores = {}
    for c in crits:
        digest = sha256_hex(canonical_json([output, model, c["id"]]))
        scores[c["id"]] = round((int(digest[:8], 16) % 1000) / 1000, 3)
    mean = _mean(scores)
    return {"scores": scores, "pass": mean >= threshold, "rationale": f"local judge {model}"}


_PROMPT = """You are grading an AI agent's answer against a rubric.
Return ONLY a JSON object of the form:
{{"scores": {{"<criterion_id>": <float 0..1>, ...}}, "pass": <true|false>, "rationale": "<=60 words"}}

Rubric criteria:
{criteria}

Task input:
{task}

Agent answer:
{answer}
"""


def _build_prompt(case: dict, output: object, rubric: dict) -> str:
    lines = "\n".join(f"- {c['id']}: {c.get('description', '')}" for c in criteria(rubric))
    return _PROMPT.format(
        criteria=lines,
        task=json.dumps(case.get("input"), ensure_ascii=False),
        answer=json.dumps(output, ensure_ascii=False),
    )


def _parse_verdict(text: str, rubric: dict, threshold: float) -> dict:
    data = json.loads(text)
    scores = {k: float(v) for k, v in (data.get("scores") or {}).items()}
    if not scores:
        scores = {c["id"]: float(data.get("score", 0)) for c in criteria(rubric)}
    passed = bool(data.get("pass", _mean(scores) >= threshold))
    return {"scores": scores, "pass": passed, "rationale": str(data.get("rationale", ""))[:400]}


def gemini_judge(
    case: dict, output: object, rubric: dict, model: str, threshold: float = 0.6
) -> dict:
    from google import genai  # needs tracegym[judges]

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resp = client.models.generate_content(
        model=model,
        contents=_build_prompt(case, output, rubric),
        config={"response_mime_type": "application/json", "temperature": 0},
    )
    return _parse_verdict(resp.text, rubric, threshold)


def groq_judge(
    case: dict, output: object, rubric: dict, model: str, threshold: float = 0.6
) -> dict:
    import httpx  # needs tracegym[judges]

    base = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    resp = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": _build_prompt(case, output, rubric)}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "stream": False,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return _parse_verdict(resp.json()["choices"][0]["message"]["content"], rubric, threshold)


JUDGE_PROVIDERS = {
    "local": local_judge,
    "gemini": gemini_judge,
    "groq": groq_judge,
}
