from __future__ import annotations

from typing import Any


def _format_number(
    number: int,
) -> str:

    if number >= 1_000_000:

        return (
            f"{number / 1_000_000:.1f}M"
        )

    if number >= 1_000:

        return (
            f"{number / 1_000:.1f}K"
        )

    return str(number)


def _truncate(
    text: str,
    length: int = 750,
) -> str:

    text = text.strip()

    if len(text) <= length:
        return text

    return (
        text[: length - 3]
        .rstrip()
        + "..."
    )


def _team_text(
    teams: list[dict],
) -> str:

    if not teams:
        return "Not identified"

    output = []

    for team in teams[:3]:

        label = team["team"]

        rank = team.get(
            "rank"
        )

        if rank:

            label += (
                f" (Preseason #{rank})"
            )

        output.append(label)

    return ", ".join(output)


def _opportunity_angle(
    conversation_type: str,
) -> str:

    angles = {

        "TEAM_VIDEO":
            (
                "Team hype is already attracting "
                "attention. Look for a natural way "
                "to add BTB's futures perspective."
            ),

        "TEAM_HYPE":
            (
                "Audience sentiment around this team "
                "creates an opening for BTB's projection "
                "or futures outlook."
            ),

        "INJURY":
            (
                "Potentially meaningful team/player news. "
                "Consider whether this changes or supports "
                "BTB's existing outlook."
            ),

        "NFL_CAMP":
            (
                "Training-camp discussion is building. "
                "Look for a model or futures angle rather "
                "than simply repeating the camp update."
            ),

        "STAR_PLAYER_NEWS":
            (
                "Star-player discussion can create a "
                "high-interest entry point for the team's "
                "season outlook."
            ),

        "RANKINGS":
            (
                "Ranking discussion is an ideal place "
                "to compare public perception with "
                "BTB's numbers."
            ),

        "POWER_RATINGS":
            (
                "Power-rating discussion is highly "
                "aligned with BTB's analytical positioning."
            ),

        "FUTURES":
            (
                "Directly relevant to existing "
                "BTB futures content."
            ),

        "MATCHUP":
            (
                "Game-specific discussion can be compared "
                "with BTB's ratings and projections."
            ),
    }

    return angles.get(
        conversation_type,
        (
            "Potential football conversation "
            "where BTB may have a differentiated "
            "analytical angle."
        ),
    )


def generate_discord_embed(
    opportunity: dict[str, Any],
    high_priority_threshold: float = 86,
) -> dict:

    score = float(
        opportunity.get(
            "score",
            0,
        )
    )

    high_priority = (
        score
        >= high_priority_threshold
    )

    conversation_type = (
        opportunity.get(
            "conversation_type",
            "GENERAL",
        )
    )

    if high_priority:

        title = (
            "🔥 HIGH PRIORITY "
            f"X OPPORTUNITY — {score:.0f}/100"
        )

    else:

        title = (
            "X Opportunity — "
            f"{score:.0f}/100"
        )

    username = opportunity.get(
        "username",
        "unknown",
    )

    author_name = opportunity.get(
        "author_name",
        username,
    )

    followers = int(
        opportunity.get(
            "followers",
            0,
        )
    )

    reasons = opportunity.get(
        "reasons",
        [],
    )

    reason_text = (
        ", ".join(reasons)
        if reasons
        else "Passed alert threshold"
    )

    post_url = opportunity.get(
        "url",
        "https://x.com",
    )

    embed = {

        "title": title,

        "url": post_url,

        "description": (
            f"**{author_name} "
            f"(@{username})**\n\n"
            + _truncate(
                opportunity.get(
                    "text",
                    "",
                )
            )
        ),

        "fields": [

            {
                "name":
                    "Conversation Type",

                "value":
                    conversation_type,

                "inline":
                    True,
            },

            {
                "name":
                    "Team",

                "value":
                    _team_text(
                        opportunity.get(
                            "teams",
                            [],
                        )
                    ),

                "inline":
                    True,
            },

            {
                "name":
                    "Sport",

                "value":
                    opportunity.get(
                        "sport",
                        "Unknown",
                    ),

                "inline":
                    True,
            },

            {
                "name":
                    "Age",

                "value":
                    (
                        f"{opportunity.get('age_minutes', 0):.0f} min"
                    ),

                "inline":
                    True,
            },

            {
                "name":
                    "Followers",

                "value":
                    _format_number(
                        followers
                    ),

                "inline":
                    True,
            },

            {
                "name":
                    "Engagement Velocity",

                "value":
                    (
                        f"{opportunity.get('engagement_velocity', 0):.1f}/min"
                    ),

                "inline":
                    True,
            },

            {
                "name":
                    "Likes",

                "value":
                    _format_number(
                        int(
                            opportunity.get(
                                "likes",
                                0,
                            )
                        )
                    ),

                "inline":
                    True,
            },

            {
                "name":
                    "Replies",

                "value":
                    _format_number(
                        int(
                            opportunity.get(
                                "replies",
                                0,
                            )
                        )
                    ),

                "inline":
                    True,
            },

            {
                "name":
                    "Reposts",

                "value":
                    _format_number(
                        int(
                            opportunity.get(
                                "reposts",
                                0,
                            )
                        )
                    ),

                "inline":
                    True,
            },

            {
                "name":
                    "Why It Was Flagged",

                "value":
                    reason_text,

                "inline":
                    False,
            },

            {
                "name":
                    "BTB Opportunity",

                "value":
                    _opportunity_angle(
                        conversation_type
                    ),

                "inline":
                    False,
            },

            {
                "name":
                    "Suggested BTB Reply",

                "value":
                    (
                        "Phase 1.5: Discovery only.\n\n"
                        "**Phase 2 will automatically "
                        "retrieve BTB team data and put "
                        "the copy/paste response here.**"
                    ),

                "inline":
                    False,
            },

            {
                "name":
                    "Open on X",

                "value":
                    f"[View Post]({post_url})",

                "inline":
                    False,
            },
        ],

        "footer": {
            "text":
                (
                    "BTB Analytics • X Growth Radar • "
                    f"{opportunity.get('query_name', '')}"
                )
        },
    }

    return embed
