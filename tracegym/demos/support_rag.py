"""Support-RAG demo agent: retrieve, ground, scrub, cite.

The agent retrieves the most relevant doc from a small corpus, asks the model to
answer from that context (the local responder echoes the grounded context), then
deterministically attaches the citation and scrubs PII. Its correctness lives in
that deterministic layer, which is what makes a regression catchable in frozen
replay with no keys. Seeded bugs are applied as output transforms (see bugs.py),
so a bug reuses the same fixtures and is a real behavior change, not a fake.
"""

from __future__ import annotations

import re

_CONTEXT_MARKER = "Context:\n"
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# Ignore high-frequency words so retrieval ranks on topical terms, not "the"/"is".
_STOPWORDS = frozenset(
    "a an the is are do does how what when where which who to of in on for you your "
    "my i we it can could would should will and or my me be from with".split()
)


def _overlap(query: str, text: str) -> int:
    q = set(re.findall(r"[a-z]+", query.lower())) - _STOPWORDS
    t = set(re.findall(r"[a-z]+", text.lower())) - _STOPWORDS
    return len(q & t)


def retrieve_from(corpus: dict[str, str], query: str, k: int = 2) -> list[dict]:
    """Rank corpus docs by keyword overlap and return the top k as {id, text}."""
    ranked = sorted(corpus.items(), key=lambda kv: (-_overlap(query, kv[1]), kv[0]))
    return [{"id": doc_id, "text": text} for doc_id, text in ranked[:k]]


def context_responder(messages: list[dict], model: str) -> str:
    """A local stand-in model: answer with the grounded context it was given."""
    user = messages[-1].get("content", "")
    if _CONTEXT_MARKER in user:
        return user.split(_CONTEXT_MARKER, 1)[1].strip()
    return user.strip()


def _scrub_pii(text: str) -> str:
    return _SSN.sub("[redacted]", text)


def make_support_rag(corpus: dict[str, str]):
    """Build a support-RAG agent(runtime, case) bound to a corpus."""

    def agent(rt, case) -> dict:
        question = case["input"]["question"]

        @rt.tool
        def retrieve(query):
            return retrieve_from(corpus, query)

        hits = retrieve(query=question)
        top = hits[0] if hits else {"id": None, "text": ""}
        prompt = (
            "Answer the question using only the context.\n"
            f"Question: {question}\n{_CONTEXT_MARKER}{top['text']}"
        )
        draft = rt.chat([{"role": "user", "content": prompt}])["content"]
        return {"answer": _scrub_pii(draft), "citations": [top["id"]]}

    return agent
