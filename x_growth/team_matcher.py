from __future__ import annotations

import re
from typing import Any


def _contains_alias(
    text: str,
    alias: str,
) -> bool:

    escaped = re.escape(
        alias.lower()
    )

    pattern = (
        r"(?<![a-z0-9])"
        + escaped
        + r"(?![a-z0-9])"
    )

    return bool(
        re.search(
            pattern,
            text.lower(),
        )
    )


def identify_teams(
    text: str,
    team_config: dict[str, Any],
    sport_hint: str | None = None,
) -> list[dict]:

    matches = []

    sports = []

    if sport_hint:

        sport_hint = sport_hint.upper()

        if sport_hint == "CFB":
            sports = ["cfb"]

        elif sport_hint == "NFL":
            sports = ["nfl"]

    if not sports:
        sports = ["cfb", "nfl"]

    for sport_key in sports:

        teams = team_config.get(
            sport_key,
            {},
        )

        for team_name, info in teams.items():

            aliases = list(
                info.get(
                    "aliases",
                    [],
                )
            )

            aliases.append(team_name)

            matched_alias = None

            #
            # Prefer longer aliases first.
            #
            for alias in sorted(
                set(aliases),
                key=len,
                reverse=True,
            ):

                if _contains_alias(
                    text,
                    alias,
                ):

                    matched_alias = alias
                    break

            if matched_alias:

                matches.append(
                    {
                        "team": team_name,
                        "sport": sport_key.upper(),
                        "priority": int(
                            info.get(
                                "priority",
                                5,
                            )
                        ),
                        "rank": info.get(
                            "rank"
                        ),
                        "matched_alias": (
                            matched_alias
                        ),
                    }
                )

    matches.sort(
        key=lambda x: (
            x.get("priority", 0),
            -(x.get("rank") or 999),
        ),
        reverse=True,
    )

    return matches
