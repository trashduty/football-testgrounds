from __future__ import annotations

from typing import Any


def quote_if_needed(value: str) -> str:
    value = value.strip()

    if " " in value or "&" in value:
        return f'"{value}"'

    return value


def _chunk_expressions(
    expressions: list[str],
    suffix: str,
    max_chars: int = 475,
) -> list[str]:

    chunks = []
    current = []

    for expression in expressions:

        proposed = current + [expression]

        query = (
            "("
            + " OR ".join(proposed)
            + ") "
            + suffix
        )

        if len(query) > max_chars and current:

            chunks.append(
                "("
                + " OR ".join(current)
                + ") "
                + suffix
            )

            current = [expression]

        else:
            current = proposed

    if current:

        chunks.append(
            "("
            + " OR ".join(current)
            + ") "
            + suffix
        )

    return chunks


def build_cfb_team_queries(
    team_config: dict[str, Any],
    max_chars: int,
) -> list[dict[str, Any]]:

    cfb = team_config.get("cfb", {})

    expressions = []

    for team_name, info in cfb.items():

        rank = info.get("rank")

        # Phase 1.5: only current Top 10.
        if rank is None or int(rank) > 10:
            continue

        expressions.append(
            quote_if_needed(team_name)
        )

    queries = []

    #
    # General hype/discussion.
    #
    hype_suffix = (
        '(hype OR "big year" OR underrated OR overrated '
        'OR playoff OR championship OR "national title" '
        'OR expectations OR contender OR "win total") '
        'lang:en -is:retweet -is:reply'
    )

    for index, query in enumerate(
        _chunk_expressions(
            expressions,
            hype_suffix,
            max_chars,
        ),
        start=1,
    ):

        queries.append(
            {
                "name": f"cfb_top10_hype_{index}",
                "sport": "CFB",
                "conversation_type": "TEAM_HYPE",
                "query": query,
                "cadence_minutes": 10,
                "lookback_minutes": 25,
            }
        )

    #
    # Videos.
    #
    video_suffix = (
        "has:videos "
        "lang:en -is:retweet -is:reply"
    )

    for index, query in enumerate(
        _chunk_expressions(
            expressions,
            video_suffix,
            max_chars,
        ),
        start=1,
    ):

        queries.append(
            {
                "name": f"cfb_top10_video_{index}",
                "sport": "CFB",
                "conversation_type": "TEAM_VIDEO",
                "query": query,
                "cadence_minutes": 10,
                "lookback_minutes": 25,
            }
        )

    #
    # Images/graphics.
    #
    image_suffix = (
        "has:images "
        "lang:en -is:retweet -is:reply"
    )

    for index, query in enumerate(
        _chunk_expressions(
            expressions,
            image_suffix,
            max_chars,
        ),
        start=1,
    ):

        queries.append(
            {
                "name": f"cfb_top10_images_{index}",
                "sport": "CFB",
                "conversation_type": "TEAM_HYPE",
                "query": query,
                "cadence_minutes": 20,
                "lookback_minutes": 35,
            }
        )

    return queries


def build_priority_account_queries(
    account_config: dict[str, Any],
    max_chars: int,
) -> list[dict[str, Any]]:

    accounts = account_config.get(
        "accounts",
        {},
    )

    nfl_accounts = []
    cfb_accounts = []

    for username, info in accounts.items():

        expression = f"from:{username}"

        sport = str(
            info.get("sport", "BOTH")
        ).upper()

        if sport in ("NFL", "BOTH"):
            nfl_accounts.append(expression)

        if sport in ("CFB", "BOTH"):
            cfb_accounts.append(expression)

    result = []

    nfl_suffix = (
        "(NFL OR football OR camp OR practice "
        "OR preseason OR injury OR quarterback "
        "OR rookie OR starter) "
        "-is:retweet -is:reply"
    )

    for index, query in enumerate(
        _chunk_expressions(
            nfl_accounts,
            nfl_suffix,
            max_chars,
        ),
        start=1,
    ):

        result.append(
            {
                "name": f"priority_nfl_accounts_{index}",
                "sport": "NFL",
                "conversation_type": "GENERAL",
                "query": query,
                "cadence_minutes": 10,
                "lookback_minutes": 25,
            }
        )

    cfb_suffix = (
        '("college football" OR CFB OR football '
        'OR rankings OR playoff OR camp '
        'OR practice OR preseason) '
        "-is:retweet -is:reply"
    )

    for index, query in enumerate(
        _chunk_expressions(
            cfb_accounts,
            cfb_suffix,
            max_chars,
        ),
        start=1,
    ):

        result.append(
            {
                "name": f"priority_cfb_accounts_{index}",
                "sport": "CFB",
                "conversation_type": "GENERAL",
                "query": query,
                "cadence_minutes": 10,
                "lookback_minutes": 25,
            }
        )

    return result


def build_all_queries(
    configured_queries: dict,
    team_config: dict,
    account_config: dict,
    max_chars: int,
) -> list[dict]:

    queries = []

    #
    # Static/configured queries.
    #
    for name, info in configured_queries.get(
        "queries",
        {},
    ).items():

        if not info.get("enabled", True):
            continue

        query = " ".join(
            str(info.get("query", "")).split()
        )

        if not query:
            continue

        queries.append(
            {
                "name": name,
                "sport": info.get(
                    "sport",
                    "Unknown",
                ),
                "conversation_type": info.get(
                    "conversation_type",
                    "GENERAL",
                ),
                "query": query,
                "cadence_minutes": int(
                    info.get(
                        "cadence_minutes",
                        20,
                    )
                ),
                "lookback_minutes": int(
                    info.get(
                        "lookback_minutes",
                        40,
                    )
                ),
            }
        )

    #
    # Generated current Top-10 searches.
    #
    queries.extend(
        build_cfb_team_queries(
            team_config,
            max_chars,
        )
    )

    #
    # Generated priority account searches.
    #
    queries.extend(
        build_priority_account_queries(
            account_config,
            max_chars,
        )
    )

    return queries
