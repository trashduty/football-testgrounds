import re
from typing import Any


METRIC_ALIASES = {
    "epa/rush": "off_epa_per_rush",
    "epa per rush": "off_epa_per_rush",
    "rush epa": "off_epa_per_rush",
    "rushing epa": "off_epa_per_rush",

    "epa/pass": "off_epa_per_pass",
    "epa per pass": "off_epa_per_pass",
    "pass epa": "off_epa_per_pass",
    "passing epa": "off_epa_per_pass",

    "epa/play": "off_epa_per_play",
    "epa per play": "off_epa_per_play",

    "rush success rate": "off_rush_success_rate",
    "rushing success rate": "off_rush_success_rate",

    "pass success rate": "off_pass_success_rate",
    "passing success rate": "off_pass_success_rate",

    "success rate": "off_success_rate",
}


def identify_metric(query: str) -> str | None:
    normalized = query.lower()

    aliases = sorted(
        METRIC_ALIASES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    for phrase, metric in aliases:
        if phrase in normalized:
            return metric

    return None


def identify_season(query: str, default_season: int) -> int:
    match = re.search(r"\b(20\d{2})\b", query)

    if match:
        return int(match.group(1))

    return default_season


def parse_query(
    query: str,
    team_names: list[str],
    default_season: int,
) -> dict[str, Any]:
    normalized = query.lower()

    matching_teams = [
        team
        for team in team_names
        if team.lower() in normalized
    ]

    matching_teams.sort(key=len, reverse=True)

    return {
        "team": matching_teams[0] if matching_teams else None,
        "metric": identify_metric(query),
        "season": identify_season(query, default_season),
    }
