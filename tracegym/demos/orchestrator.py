"""A small multi-agent demo: an orchestrator delegating to two sub-agents.

The orchestrator runs a retriever (a search tool plus a ranking turn) and then a
writer (one drafting turn). Each stage runs inside an ``rt.agent(name)`` block, so
its tool and LLM spans nest under an agent span and cost, latency and tool use can
be attributed per agent, and the trajectory can be drawn from the spans alone.

It is deliberately tiny and fully deterministic: the sub-agents call ``rt.tool``
and ``rt.chat`` against the local responder, so the whole trajectory records as
fixtures with no keys and no network, exactly like the single-agent demos.
"""

from __future__ import annotations

from tracegym.demos.data import CORPUS, SUPPORT_QA
from tracegym.demos.support_rag import _CONTEXT_MARKER, context_responder, retrieve_from

orchestrator_responder = context_responder


def make_orchestrator(corpus: dict[str, str]):
    """Build an orchestrator agent(runtime, case) bound to a corpus."""

    def agent(rt, case) -> dict:
        question = case["input"]["question"]

        @rt.tool
        def search(query):
            return retrieve_from(corpus, query)

        with rt.agent("orchestrator"):
            with rt.agent("retriever"):
                hits = search(query=question)
                top = hits[0] if hits else {"id": None, "text": ""}
                ranked = rt.chat(
                    [
                        {
                            "role": "user",
                            "content": f"Rank the context for: {question}\n"
                            f"{_CONTEXT_MARKER}{top['text']}",
                        }
                    ]
                )["content"]
            with rt.agent("writer"):
                draft = rt.chat(
                    [
                        {
                            "role": "user",
                            "content": "Answer using only the context.\n"
                            f"Question: {question}\n{_CONTEXT_MARKER}{ranked}",
                        }
                    ]
                )["content"]
        return {"answer": draft, "citations": [top["id"]]}

    return agent


def orchestrator_cases() -> list[dict]:
    """Two cases drawn from the support corpus, each grounded in one doc."""
    picks = [SUPPORT_QA[0], SUPPORT_QA[4]]  # refunds, warranty
    return [
        {
            "id": f"orch-{doc_id}",
            "input": {"question": question},
            "checks": [{"type": "contains", "value": expect}],
            "expected": {"citation": doc_id},
        }
        for question, doc_id, expect in picks
    ]


__all__ = ["make_orchestrator", "orchestrator_cases", "orchestrator_responder", "CORPUS"]
