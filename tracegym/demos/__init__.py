"""Two small, fully deterministic demo agents used by the bundled suites.

Both do their gradable work (citations, PII scrubbing, SQL) deterministically on
top of a local responder, so their traces record and replay with zero API keys.
The same agent code runs live against Groq when a key is present; only the
responder changes. Bug variants (used by the seeded-regression demo) are the same
functions with a `bug` flag, so a regression is a real code change, not a fake.
"""

from tracegym.demos.sql_analyst import make_sql_analyst, sql_responder
from tracegym.demos.support_rag import context_responder, make_support_rag

# Two cross-model local judges. Build-time seeding and demo-time judging both use
# this roster so cached verdicts (keyed by model) are hit and the demo stays keyless.
DEMO_ROSTER = {
    "primary": ("local", "demo-judge-a"),
    "secondary": ("local", "demo-judge-b"),
}

__all__ = [
    "make_support_rag",
    "context_responder",
    "make_sql_analyst",
    "sql_responder",
    "DEMO_ROSTER",
]
