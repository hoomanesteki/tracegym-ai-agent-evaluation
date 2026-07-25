"""Judge ensemble, cache, and confidence.

Two cross-family judges vote on each output; when they disagree a tiebreaker is
called. Every verdict is cached by (case, output, rubric, model), so a rerun costs
nothing and the bundled demo runs entirely from a seed cache with no keys.

Crucially, the ensemble reports its own confidence and can return NEEDS_REVIEW
instead of a forced pass/fail. Disagreement, a parse failure, or a score that
sits right on the threshold all route a case to human review rather than pretend
the judge was sure. That is the honest alternative to a confident wrong verdict.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tracegym.judge.providers import JUDGE_PROVIDERS
from tracegym.util.canon import canonical_json, sha256_hex

# Thresholds for routing a case to human review.
DISAGREE_SCORE_GAP = 0.4  # ~2 points on a 5-point scale
REVIEW_MARGIN = 0.1  # score this close to the pass threshold is not decisive


@dataclass
class Vote:
    provider: str
    model: str
    scores: dict
    passed: bool
    rationale: str
    parse_ok: bool = True

    @property
    def mean(self) -> float:
        return sum(self.scores.values()) / len(self.scores) if self.scores else 0.0


@dataclass
class CaseVerdict:
    case_id: str
    output_sha: str
    score: float
    passed: bool
    confidence: float
    state: str  # PASS | FAIL | NEEDS_REVIEW
    rationale: str
    votes: list[Vote] = field(default_factory=list)


def rubric_sha(rubric: dict) -> str:
    return sha256_hex(canonical_json(rubric or {}))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _cached_vote(conn, case_id, output_sha, rsha, provider, model) -> Vote | None:
    row = conn.execute(
        "SELECT provider, model, scores, pass, rationale FROM judgments "
        "WHERE case_id=? AND output_sha=? AND rubric_sha=? AND provider=? AND model=?",
        (case_id, output_sha, rsha, provider, model),
    ).fetchone()
    if row is None:
        return None
    return Vote(
        provider=row["provider"],
        model=row["model"],
        scores=json.loads(row["scores"] or "{}"),
        passed=bool(row["pass"]),
        rationale=row["rationale"] or "",
    )


def _store_vote(conn, case_id, output_sha, rsha, vote: Vote) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO judgments
            (id, case_id, output_sha, rubric_sha, provider, model, scores, pass, rationale, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "j-" + uuid.uuid4().hex[:12],
            case_id,
            output_sha,
            rsha,
            vote.provider,
            vote.model,
            json.dumps(vote.scores, sort_keys=True),
            1 if vote.passed else 0,
            vote.rationale,
            _now(),
        ),
    )


def _judge_one(
    conn, case, output, output_sha, rubric, rsha, role, threshold, providers, use_cache
) -> Vote:
    provider_name, model = role
    if use_cache:
        cached = _cached_vote(conn, case["id"], output_sha, rsha, provider_name, model)
        if cached is not None:
            return cached
    fn = providers[provider_name]
    for _ in range(3):  # one try plus two retries on a parse failure
        try:
            v = fn(case, output, rubric, model, threshold)
            vote = Vote(provider_name, model, v["scores"], bool(v["pass"]), v.get("rationale", ""))
            _store_vote(conn, case["id"], output_sha, rsha, vote)
            return vote
        except Exception as exc:  # includes JSON parse failures from a real judge
            last = exc
    return Vote(provider_name, model, {}, False, f"JUDGE_PARSE_FAIL: {last}", parse_ok=False)


def judge_case(
    conn,
    case: dict,
    output: object,
    output_sha: str,
    rubric: dict,
    roster: dict,
    *,
    threshold: float = 0.6,
    providers: dict | None = None,
    use_cache: bool = True,
) -> CaseVerdict:
    """Run the ensemble for one output and return a confidence-aware verdict."""
    providers = providers or JUDGE_PROVIDERS
    rsha = rubric_sha(rubric)

    def run(role_key):
        return _judge_one(
            conn,
            case,
            output,
            output_sha,
            rubric,
            rsha,
            roster[role_key],
            threshold,
            providers,
            use_cache,
        )

    primary = run("primary")
    secondary = run("secondary")
    votes = [primary, secondary]

    disagreed = primary.passed != secondary.passed
    gap = abs(primary.mean - secondary.mean)
    if "tiebreaker" in roster and (disagreed or gap >= DISAGREE_SCORE_GAP):
        votes.append(run("tiebreaker"))

    # Only votes that actually parsed contribute to the score and the tally; a
    # parse-failed judge must not be counted as a 0 and manufacture a regression.
    valid = [v for v in votes if v.parse_ok]
    if valid:
        mean_score = sum(v.mean for v in valid) / len(valid)
        yes = sum(1 for v in valid if v.passed)
        no = len(valid) - yes
        passed = yes > no if yes != no else mean_score >= threshold
        confidence = round((yes if passed else no) / len(valid), 4)
    else:
        # No judge could score: stay neutral rather than deflate to zero.
        mean_score = threshold
        passed = False
        confidence = 0.0

    parse_failed = any(not v.parse_ok for v in votes)
    on_the_fence = abs(mean_score - threshold) < REVIEW_MARGIN
    needs_review = disagreed or parse_failed or on_the_fence or confidence < 0.67 or not valid

    state = "NEEDS_REVIEW" if needs_review else ("PASS" if passed else "FAIL")
    rationale = next((v.rationale for v in votes if v.passed == passed and v.parse_ok), "")

    return CaseVerdict(
        case_id=case["id"],
        output_sha=output_sha,
        score=round(mean_score, 4),
        passed=passed,
        confidence=confidence,
        state=state,
        rationale=rationale,
        votes=votes,
    )
