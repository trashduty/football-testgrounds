from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)


def _parse_datetime(
    value: str | None,
) -> datetime:

    if not value:
        return datetime.now(
            timezone.utc
        )

    return (
        datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )
        .astimezone(
            timezone.utc
        )
    )


def _freshness_score(
    age_minutes: float,
) -> float:

    if age_minutes <= 5:
        return 20

    if age_minutes <= 10:
        return 19

    if age_minutes <= 20:
        return 17

    if age_minutes <= 30:
        return 14

    if age_minutes <= 45:
        return 11

    if age_minutes <= 60:
        return 8

    if age_minutes <= 90:
        return 5

    return 2


def _reach_score(
    followers: int,
) -> float:

    if followers >= 1_000_000:
        return 20

    if followers >= 500_000:
        return 19

    if followers >= 250_000:
        return 18

    if followers >= 100_000:
        return 16

    if followers >= 50_000:
        return 14

    if followers >= 25_000:
        return 12

    if followers >= 10_000:
        return 10

    if followers >= 5_000:
        return 8

    if followers >= 1_000:
        return 5

    return 2


def _velocity_score(
    likes: int,
    replies: int,
    reposts: int,
    quotes: int,
    age_minutes: float,
) -> tuple[float, float]:

    weighted = (
        likes
        + replies * 2.25
        + reposts * 2.0
        + quotes * 2.5
    )

    velocity = (
        weighted
        / max(
            age_minutes,
            1,
        )
    )

    if velocity >= 40:
        score = 25

    elif velocity >= 20:
        score = 23

    elif velocity >= 10:
        score = 21

    elif velocity >= 5:
        score = 18

    elif velocity >= 2:
        score = 15

    elif velocity >= 1:
        score = 12

    elif velocity >= 0.5:
        score = 9

    elif velocity >= 0.2:
        score = 6

    elif velocity > 0:
        score = 3

    else:
        score = 0

    return (
        score,
        velocity,
    )


def _relevance_score(
    conversation_type: str,
    has_team: bool,
) -> float:

    values = {

        "INJURY": 15,

        "TEAM_VIDEO": 15,

        "TEAM_HYPE": 14,

        "STAR_PLAYER_NEWS": 14,

        "NFL_CAMP": 13,

        "FUTURES": 15,

        "RANKINGS": 14,

        "POWER_RATINGS": 14,

        "MATCHUP": 13,

        "DEPTH_CHART": 14,

        "GENERAL": 7,
    }

    score = values.get(
        conversation_type,
        7,
    )

    if (
        has_team
        and score < 15
    ):
        score += 1

    return min(
        score,
        15,
    )


def _team_priority_score(
    teams: list[dict],
) -> float:

    if not teams:
        return 0

    highest_priority = max(
        int(
            team.get(
                "priority",
                0,
            )
        )
        for team in teams
    )

    return min(
        highest_priority,
        10,
    )


def _source_priority_score(
    username: str,
    priority_accounts: dict,
) -> tuple[float, bool]:

    accounts = (
        priority_accounts.get(
            "accounts",
            {},
        )
    )

    lookup = {
        key.lower(): value
        for key, value
        in accounts.items()
    }

    info = lookup.get(
        username.lower()
    )

    if not info:
        return 0, False

    priority = int(
        info.get(
            "priority",
            5,
        )
    )

    return (
        min(
            priority,
            10,
        ),
        True,
    )


def score_post(
    post: dict,
    teams: list[dict],
    conversation_type: str,
    priority_accounts: dict,
) -> dict:

    now = datetime.now(
        timezone.utc
    )

    created = _parse_datetime(
        post.get(
            "created_at"
        )
    )

    age_minutes = max(
        (
            now
            - created
        ).total_seconds()
        / 60,
        0,
    )

    freshness = (
        _freshness_score(
            age_minutes
        )
    )

    reach = _reach_score(
        int(
            post.get(
                "followers",
                0,
            )
        )
    )

    (
        velocity_score,
        velocity,
    ) = _velocity_score(

        int(
            post.get(
                "likes",
                0,
            )
        ),

        int(
            post.get(
                "replies",
                0,
            )
        ),

        int(
            post.get(
                "reposts",
                0,
            )
        ),

        int(
            post.get(
                "quotes",
                0,
            )
        ),

        age_minutes,
    )

    relevance = (
        _relevance_score(
            conversation_type,
            bool(teams),
        )
    )

    team_score = (
        _team_priority_score(
            teams
        )
    )

    (
        source_score,
        priority_source,
    ) = (
        _source_priority_score(
            post.get(
                "username",
                "",
            ),
            priority_accounts,
        )
    )

    total = (
        freshness
        + reach
        + velocity_score
        + relevance
        + team_score
        + source_score
    )

    total = round(
        min(
            total,
            100,
        ),
        1,
    )

    reasons = []

    if freshness >= 17:
        reasons.append(
            "very fresh"
        )

    if reach >= 16:
        reasons.append(
            "large audience"
        )

    elif reach >= 12:
        reasons.append(
            "meaningful audience"
        )

    if velocity_score >= 21:
        reasons.append(
            "rapid engagement"
        )

    elif velocity_score >= 15:
        reasons.append(
            "engagement building"
        )

    if team_score >= 9:
        reasons.append(
            "high-priority team"
        )

    if priority_source:
        reasons.append(
            "priority account"
        )

    if conversation_type == (
        "TEAM_VIDEO"
    ):
        reasons.append(
            "team video/hype content"
        )

    elif conversation_type == (
        "INJURY"
    ):
        reasons.append(
            "player/team news"
        )

    elif conversation_type == (
        "RANKINGS"
    ):
        reasons.append(
            "ranking discussion"
        )

    result = dict(post)

    result.update(
        {
            "score": total,

            "age_minutes": round(
                age_minutes,
                1,
            ),

            "freshness_score": (
                freshness
            ),

            "reach_score": reach,

            "velocity_score": (
                velocity_score
            ),

            "engagement_velocity": (
                round(
                    velocity,
                    3,
                )
            ),

            "relevance_score": (
                relevance
            ),

            "team_priority_score": (
                team_score
            ),

            "source_priority_score": (
                source_score
            ),

            "priority_source": (
                priority_source
            ),

            "conversation_type": (
                conversation_type
            ),

            "teams": teams,

            "reasons": reasons,
        }
    )

    return result
