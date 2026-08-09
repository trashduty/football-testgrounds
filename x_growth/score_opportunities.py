from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


HIGH_VALUE_TERMS = {
    "win total",
    "win totals",
    "overrated",
    "underrated",
    "playoff",
    "playoffs",
    "cfp",
    "power ranking",
    "power rankings",
    "ranking",
    "rankings",
    "projection",
    "projections",
    "injury",
    "injured",
    "quarterback",
    "qb",
    "starter",
    "suspended",
    "transfer",
    "conference",
    "championship",
}


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)

    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    ).astimezone(timezone.utc)


def _freshness_score(age_minutes: float) -> float:
    """
    Maximum: 25 points.
    """

    if age_minutes <= 10:
        return 25

    if age_minutes <= 20:
        return 22

    if age_minutes <= 30:
        return 18

    if age_minutes <= 45:
        return 15

    if age_minutes <= 60:
        return 12

    if age_minutes <= 90:
        return 8

    if age_minutes <= 120:
        return 4

    return 0


def _reach_score(followers: int) -> float:
    """
    Maximum: 25 points.
    """

    if followers >= 500_000:
        return 25

    if followers >= 250_000:
        return 23

    if followers >= 100_000:
        return 21

    if followers >= 50_000:
        return 18

    if followers >= 25_000:
        return 15

    if followers >= 10_000:
        return 12

    if followers >= 5_000:
        return 9

    if followers >= 1_000:
        return 6

    return 3


def _velocity_score(
    likes: int,
    replies: int,
    reposts: int,
    quotes: int,
    age_minutes: float,
) -> tuple[float, float]:

    """
    Maximum: 30 points.

    Replies/reposts/quotes are weighted more heavily
    than likes because they suggest an active conversation.
    """

    weighted_engagement = (
        likes
        + (replies * 2.0)
        + (reposts * 2.0)
        + (quotes * 2.5)
    )

    denominator = max(age_minutes, 1)

    velocity = weighted_engagement / denominator

    if velocity >= 20:
        score = 30

    elif velocity >= 10:
        score = 27

    elif velocity >= 5:
        score = 24

    elif velocity >= 2:
        score = 20

    elif velocity >= 1:
        score = 16

    elif velocity >= 0.5:
        score = 12

    elif velocity >= 0.20:
        score = 8

    elif velocity > 0:
        score = 4

    else:
        score = 0

    return score, velocity


def _relevance_score(text: str) -> tuple[float, list[str]]:
    """
    Maximum: 20 points.
    """

    lower = text.lower()

    matched = [
        term
        for term in HIGH_VALUE_TERMS
        if term in lower
    ]

    matched = sorted(set(matched))

    count = len(matched)

    if count >= 4:
        score = 20

    elif count == 3:
        score = 18

    elif count == 2:
        score = 15

    elif count == 1:
        score = 10

    else:
        score = 4

    return score, matched


def score_post(post: dict[str, Any]) -> dict[str, Any]:

    now = datetime.now(timezone.utc)

    created_at = _parse_datetime(
        post.get("created_at")
    )

    age_minutes = max(
        (now - created_at).total_seconds() / 60,
        0,
    )

    freshness = _freshness_score(age_minutes)

    reach = _reach_score(
        int(post.get("followers", 0))
    )

    velocity_score, velocity = _velocity_score(
        likes=int(post.get("likes", 0)),
        replies=int(post.get("replies", 0)),
        reposts=int(post.get("reposts", 0)),
        quotes=int(post.get("quotes", 0)),
        age_minutes=age_minutes,
    )

    relevance, matched_terms = _relevance_score(
        post.get("text", "")
    )

    total = (
        freshness
        + reach
        + velocity_score
        + relevance
    )

    total = round(min(total, 100), 1)

    reasons = []

    if freshness >= 22:
        reasons.append("very fresh")

    if reach >= 21:
        reasons.append("large audience")

    elif reach >= 15:
        reasons.append("meaningful audience")

    if velocity_score >= 24:
        reasons.append("rapid engagement")

    elif velocity_score >= 16:
        reasons.append("engagement building")

    if matched_terms:
        reasons.append(
            "BTB-relevant topic"
        )

    result = dict(post)

    result.update(
        {
            "score": total,
            "age_minutes": round(age_minutes, 1),

            "freshness_score": freshness,
            "reach_score": reach,

            "velocity_score": velocity_score,
            "engagement_velocity": round(
                velocity,
                3,
            ),

            "relevance_score": relevance,
            "matched_terms": matched_terms,
            "reasons": reasons,
        }
    )

    return result
