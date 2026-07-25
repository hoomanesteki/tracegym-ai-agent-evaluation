"""Run the advisor: gather recommendations, then store them for the report.

The advisor never applies a change. It emits recommendations with a verdict and,
for SAFE ones, the evidence that proved them. A human (and the merge gate) decides.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from tracegym.advisor.rules import (
    Recommendation,
    rule_drop_secondary_judge,
    rule_duplicate_llm,
    rule_latency_outliers,
    rule_redundant_tools,
)
from tracegym.config import AdvisorConfig


def advise(
    conn, run_id: str, *, cfg: AdvisorConfig | None = None, roster: dict | None = None
) -> list[Recommendation]:
    """Collect all recommendations for a run. Read-only; nothing is applied."""
    cfg = cfg or AdvisorConfig()
    recs: list[Recommendation] = []
    recs += rule_redundant_tools(conn, run_id)
    recs += rule_duplicate_llm(conn, run_id)
    if roster:
        recs += rule_drop_secondary_judge(conn, run_id, roster)
    recs += rule_latency_outliers(conn, run_id, cfg.latency_cap_ms)
    return recs


def store_recommendations(conn, run_id: str, recs: list[Recommendation]) -> None:
    now = datetime.now(UTC).isoformat()
    for r in recs:
        conn.execute(
            """
            INSERT INTO recommendations
                (id, run_id, rule_id, status, title, est_saving_usd, est_saving_pct, evidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "rec-" + uuid.uuid4().hex[:12],
                run_id,
                r.rule_id,
                r.status,
                r.title,
                r.est_saving_usd,
                r.est_saving_pct,
                json.dumps({"detail": r.detail, **r.evidence}),
                now,
            ),
        )
    conn.commit()
