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
    length: int = 700,
) -> str:

    text = (
        str(text)
        .strip()
    )

    if len(text) <= length:
        return text

    return (
        text[
            :length - 3
        ].rstrip()
        + "..."
    )


def _team_text(
    teams: list[dict],
) -> str:

    if not teams:
        return (
            "Not identified"
        )

    output = []

    for team in teams[:3]:

        label = str(
            team.get(
                "team",
                ""
            )
        )

        rank = (
            team.get(
                "rank"
            )
        )

        if rank:

            label += (
                f" (Preseason #{rank})"
            )

        confidence = (
            team.get(
                "match_confidence"
            )
        )

        if confidence is not None:

            label += (
                f" [{confidence}% match]"
            )

        output.append(
            label
        )

    return ", ".join(
        output
    )


def _context_summary(
    btb_context: dict,
) -> str:

    sources = (
        btb_context.get(
            "sources",
            {}
        )
    )

    if not sources:

        return (
            "No proprietary BTB context "
            "was found for this team."
        )

    lines = []

    for (
        source_name,
        source,
    ) in sources.items():

        category = (
            source.get(
                "category",
                ""
            )
        )

        records = (
            source.get(
                "records",
                []
            )
        )

        lines.append(
            f"• **{source_name}** "
            f"({category}): "
            f"{len(records)} relevant record(s)"
        )

    return "\n".join(
        lines
    )


def generate_discord_embed(
    opportunity: dict[str, Any],
    btb_context: dict | None = None,
    generated_reply: dict | None = None,
    high_priority_threshold: float = 86,
) -> dict:

    btb_context = (
        btb_context
        or {}
    )

    generated_reply = (
        generated_reply
        or {}
    )

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
            "🔥 HIGH PRIORITY X OPPORTUNITY "
            f"— {score:.0f}/100"
        )

    else:

        title = (
            "X Opportunity "
            f"— {score:.0f}/100"
        )

    username = (
        opportunity.get(
            "username",
            "unknown",
        )
    )

    author_name = (
        opportunity.get(
            "author_name",
            username,
        )
    )

    followers = int(
        opportunity.get(
            "followers",
            0,
        )
    )

    reasons = (
        opportunity.get(
            "reasons",
            [],
        )
    )

    reason_text = (
        ", ".join(
            reasons
        )
        if reasons
        else "Passed alert threshold"
    )

    post_url = (
        opportunity.get(
            "url",
            "https://x.com",
        )
    )

    fields = [

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
                "Why It Was Flagged",

            "value":
                reason_text,

            "inline":
                False,
        },
    ]

    if btb_context:

        fields.append(
            {
                "name":
                    "BTB Data Retrieved",

                "value":
                    _context_summary(
                        btb_context
                    ),

                "inline":
                    False,
            }
        )

    preferred = (
        generated_reply.get(
            "preferred_reply"
        )
    )

    alternative = (
        generated_reply.get(
            "alternative_reply"
        )
    )

    angle = (
        generated_reply.get(
            "angle"
        )
    )

    if angle:

        fields.append(
            {
                "name":
                    "Recommended Angle",

                "value":
                    _truncate(
                        angle,
                        500,
                    ),

                "inline":
                    False,
            }
        )

    if preferred:

        fields.append(
            {
                "name":
                    "📋 Suggested BTB Reply — COPY/PASTE",

                "value":
                    _truncate(
                        preferred,
                        1000,
                    ),

                "inline":
                    False,
            }
        )

    if alternative:

        fields.append(
            {
                "name":
                    "Alternative Reply",

                "value":
                    _truncate(
                        alternative,
                        1000,
                    ),

                "inline":
                    False,
            }
        )

    facts_used = (
        generated_reply.get(
            "facts_used",
            [],
        )
    )

    if facts_used:

        fields.append(
            {
                "name":
                    "BTB Facts Used",

                "value":
                    "\n".join(
                        f"• {fact}"
                        for fact
                        in facts_used[:8]
                    ),

                "inline":
                    False,
            }
        )

    fields.append(
        {
            "name":
                "Open on X",

            "value":
                f"[View Post]({post_url})",

            "inline":
                False,
        }
    )

    return {

        "title":
            title,

        "url":
            post_url,

        "description":
            (
                f"**{author_name} "
                f"(@{username})**\n\n"
                + _truncate(
                    opportunity.get(
                        "text",
                        "",
                    )
                )
            ),

        "fields":
            fields,

        "footer": {
            "text":
                (
                    "BTB Analytics • "
                    "X Growth Radar • Phase 2"
                )
        },
    }
