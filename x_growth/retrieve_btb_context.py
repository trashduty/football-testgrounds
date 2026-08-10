from __future__ import annotations

from pathlib import Path
from typing import Any

from x_growth.data_sources import (
    load_source_for_team,
)


# ============================================================
# WHICH INFORMATION TYPES MATTER FOR EACH CONVERSATION?
# ============================================================

CONVERSATION_CATEGORIES = {

    "TEAM_VIDEO": {
        "futures",
        "analysis_notes",
        "rankings",
        "season_stats",
    },

    "TEAM_HYPE": {
        "futures",
        "analysis_notes",
        "rankings",
        "season_stats",
    },

    "STAR_PLAYER_NEWS": {
        "futures",
        "current_matchup",
        "analysis_notes",
        "season_stats",
    },

    "NFL_CAMP": {
        "futures",
        "analysis_notes",
        "current_matchup",
    },

    "INJURY": {
        "futures",
        "current_matchup",
        "analysis_notes",
    },

    "DEPTH_CHART": {
        "futures",
        "current_matchup",
        "analysis_notes",
    },

    "FUTURES": {
        "futures",
        "analysis_notes",
        "rankings",
        "season_stats",
    },

    "RANKINGS": {
        "futures",
        "rankings",
        "season_stats",
        "analysis_notes",
    },

    "POWER_RATINGS": {
        "futures",
        "rankings",
        "season_stats",
        "current_matchup",
    },

    "MATCHUP": {
        "current_matchup",
        "season_stats",
        "rankings",
        "analysis_notes",
    },

    "GENERAL": {
        "futures",
        "current_matchup",
        "analysis_notes",
    },
}


def source_is_relevant(
    category: str,
    conversation_type: str,
) -> bool:

    allowed = (
        CONVERSATION_CATEGORIES.get(
            conversation_type,
            CONVERSATION_CATEGORIES[
                "GENERAL"
            ],
        )
    )

    return (
        category
        in allowed
    )


def retrieve_btb_context(
    root: Path,
    team: dict[str, Any],
    conversation_type: str,
    source_config: dict,
    reply_settings: dict,
) -> dict[str, Any]:

    sport = str(
        team.get(
            "sport",
            "",
        )
    ).upper()

    max_rows = int(
        reply_settings.get(
            "max_context_rows_per_source",
            3,
        )
    )

    max_note_characters = int(
        reply_settings.get(
            "max_note_characters",
            4500,
        )
    )

    context = {

        "team":
            team.get(
                "team"
            ),

        "btb_team_short":
            team.get(
                "btb_team_short"
            ),

        "mascot":
            team.get(
                "mascot"
            ),

        "sport":
            sport,

        "conversation_type":
            conversation_type,

        "team_match_confidence":
            team.get(
                "match_confidence"
            ),

        "sources":
            {},
    }

    sources = (
        source_config.get(
            "sources",
            {}
        )
    )

    for (
        source_name,
        config,
    ) in sources.items():

        if not config.get(
            "enabled",
            True,
        ):
            continue

        source_sport = str(
            config.get(
                "sport",
                "",
            )
        ).upper()

        if (
            source_sport
            and source_sport
            != sport
        ):
            continue

        category = (
            config.get(
                "category",
                "general",
            )
        )

        if not source_is_relevant(
            category=category,
            conversation_type=(
                conversation_type
            ),
        ):
            continue

        rows = (
            load_source_for_team(
                root=root,
                source_name=source_name,
                source_config=config,
                team=team,
                max_rows=max_rows,
                max_note_characters=(
                    max_note_characters
                ),
            )
        )

        if not rows:
            continue

        context[
            "sources"
        ][
            source_name
        ] = {

            "category":
                category,

            "records":
                rows,
        }

    context[
        "has_btb_context"
    ] = bool(
        context["sources"]
    )

    return context
