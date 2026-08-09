from __future__ import annotations

from typing import Any


def _format_number(number: int) -> str:

    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"

    if number >= 1_000:
        return f"{number / 1_000:.1f}K"

    return str(number)


def _truncate(
    text: str,
    length: int = 700,
) -> str:

    text = text.strip()

    if len(text) <= length:
        return text

    return text[: length - 3].rstrip() + "..."


def generate_discord_embed(
    opportunity: dict[str, Any],
    high_priority_threshold: float = 88,
) -> dict:

    score = float(
        opportunity.get("score", 0)
    )

    high_priority = (
        score >= high_priority_threshold
    )

    title = (
        f"🔥 HIGH PRIORITY X OPPORTUNITY — {score:.0f}/100"
        if high_priority
        else f"X Opportunity — {score:.0f}/100"
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
        opportunity.get("followers", 0)
    )

    age = opportunity.get(
        "age_minutes",
        0,
    )

    likes = int(
        opportunity.get("likes", 0)
    )

    replies = int(
        opportunity.get("replies", 0)
    )

    reposts = int(
        opportunity.get("reposts", 0)
    )

    quotes = int(
        opportunity.get("quotes", 0)
    )

    reasons = opportunity.get(
        "reasons",
        [],
    )

    matched_terms = opportunity.get(
        "matched_terms",
        [],
    )

    reason_text = (
        ", ".join(reasons)
        if reasons
        else "Passed opportunity threshold"
    )

    topic_text = (
        ", ".join(matched_terms[:6])
        if matched_terms
        else opportunity.get(
            "query_name",
            "Football",
        )
    )

    original_text = _truncate(
        opportunity.get(
            "text",
            "",
        )
    )

    post_url = opportunity.get(
        "url",
        "https://x.com",
    )

    embed = {
        "title": title,

        "url": post_url,

        "description": (
            f"**{author_name} (@{username})**\n\n"
            f"{original_text}"
        ),

        "fields": [

            {
                "name": "Why it was flagged",
                "value": reason_text,
                "inline": False,
            },

            {
                "name": "Sport",
                "value": opportunity.get(
                    "sport",
                    "Unknown",
                ),
                "inline": True,
            },

            {
                "name": "Age",
                "value": f"{age:.0f} min",
                "inline": True,
            },

            {
                "name": "Followers",
                "value": _format_number(
                    followers
                ),
                "inline": True,
            },

            {
                "name": "Likes",
                "value": _format_number(
                    likes
                ),
                "inline": True,
            },

            {
                "name": "Replies",
                "value": _format_number(
                    replies
                ),
                "inline": True,
            },

            {
                "name": "Reposts",
                "value": _format_number(
                    reposts
                ),
                "inline": True,
            },

            {
                "name": "Topics",
                "value": topic_text,
                "inline": False,
            },

            {
                "name": "BTB Reply",
                "value": (
                    "Phase 1: Review this conversation and "
                    "reply manually if BTB has a useful angle.\n\n"
                    "**Phase 2 will populate the copy/paste "
                    "BTB response here automatically.**"
                ),
                "inline": False,
            },

            {
                "name": "Open on X",
                "value": f"[View Post]({post_url})",
                "inline": False,
            },
        ],

        "footer": {
            "text": (
                "BTB Analytics • X Growth Radar • "
                f"{opportunity.get('query_name', '')}"
            )
        },
    }

    return embed
