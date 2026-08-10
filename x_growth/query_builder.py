from __future__ import annotations

from typing import Any


# Names that commonly occur outside college football.
AMBIGUOUS_CFB_NAMES = {
    "Texas",
    "Miami",
    "Georgia",
    "Indiana",
    "Oregon",
    "Oklahoma",
}


def quote(value: str) -> str:
    return f'"{value.strip()}"'


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


def _cfb_team_expression(
    team_name: str,
    info: dict[str, Any],
) -> str:
    """
    Generate safer team expressions.

    Ambiguous names such as Texas/Miami cannot be
    searched as standalone words.
    """

    aliases = list(
        info.get("aliases", [])
    )

    if team_name in AMBIGUOUS_CFB_NAMES:

        safe_aliases = []

        for alias in aliases:

            alias_lower = alias.lower()

            # Reject generic geographic names.
            if alias_lower == team_name.lower():
                continue

            # Reject extremely short ambiguous aliases.
            if len(alias.strip()) <= 3:
                continue

            safe_aliases.append(alias)

        # Add explicit football-specific phrases.
        safe_aliases.append(
            f"{team_name} football"
        )

        expressions = [
            quote(alias)
            for alias in sorted(
                set(safe_aliases)
            )
        ]

    else:

        expressions = [
            quote(team_name)
        ]

        for alias in aliases:

            if len(alias.strip()) >= 4:
                expressions.append(
                    quote(alias)
                )

        expressions = sorted(
            set(expressions)
        )

    return (
        "("
        + " OR ".join(expressions)
        + ")"
    )


def build_cfb_team_queries(
    team_config: dict[str, Any],
    max_chars: int,
) -> list[dict[str, Any]]:

    cfb = team_config.get(
        "cfb",
        {},
    )

    expressions = []

    for team_name, info in cfb.items():

        rank = info.get("rank")

        if rank is None:
            continue

        if int(rank) > 10:
            continue

        expressions.append(
            _cfb_team_expression(
                team_name,
                info,
            )
        )

    queries = []

    # Explicit football context.
    football_context = (
        '(football OR CFB OR NCAA OR preseason '
        'OR practice OR quarterback OR QB '
        'OR coach OR playoff OR rankings '
        'OR "college football")'
    )

    hype_suffix = (
        f"{football_context} "
        '(hype OR "big year" OR underrated '
        'OR overrated OR playoff OR championship '
        'OR "national title" OR expectations '
        'OR contender OR "win total") '
        "lang:en "
        "-is:retweet "
        "-is:reply"
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
                "name":
                    f"cfb_top10_hype_{index}",

                "sport":
                    "CFB",

                "conversation_type":
                    "TEAM_HYPE",

                "query":
                    query,

                "cadence_minutes":
                    10,

                "lookback_minutes":
                    25,
            }
        )

    video_suffix = (
        f"{football_context} "
        "has:videos "
        "lang:en "
        "-is:retweet "
        "-is:reply"
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
                "name":
                    f"cfb_top10_video_{index}",

                "sport":
                    "CFB",

                "conversation_type":
                    "TEAM_VIDEO",

                "query":
                    query,

                "cadence_minutes":
                    10,

                "lookback_minutes":
                    25,
            }
        )

    image_suffix = (
        f"{football_context} "
        "has:images "
        "lang:en "
        "-is:retweet "
        "-is:reply"
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
                "name":
                    f"cfb_top10_images_{index}",

                "sport":
                    "CFB",

                "conversation_type":
                    "TEAM_HYPE",

                "query":
                    query,

                "cadence_minutes":
                    20,

                "lookback_minutes":
                    35,
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

        expression = (
            f"from:{username}"
        )

        sport = str(
            info.get(
                "sport",
                "BOTH",
            )
        ).upper()

        if sport in (
            "NFL",
            "BOTH",
        ):
            nfl_accounts.append(
                expression
            )

        if sport in (
            "CFB",
            "BOTH",
        ):
            cfb_accounts.append(
                expression
            )

    result = []

    nfl_suffix = (
        "(NFL OR football OR camp "
        "OR practice OR preseason "
        "OR injury OR quarterback "
        "OR rookie OR starter) "
        "-is:retweet "
        "-is:reply"
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
                "name":
                    f"priority_nfl_accounts_{index}",

                "sport":
                    "NFL",

                "conversation_type":
                    "GENERAL",

                "query":
                    query,

                "cadence_minutes":
                    10,

                "lookback_minutes":
                    25,
            }
        )

    cfb_suffix = (
        '("college football" OR CFB '
        'OR NCAA OR football '
        'OR rankings OR playoff '
        'OR preseason OR quarterback) '
        "-is:retweet "
        "-is:reply"
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
                "name":
                    f"priority_cfb_accounts_{index}",

                "sport":
                    "CFB",

                "conversation_type":
                    "GENERAL",

                "query":
                    query,

                "cadence_minutes":
                    10,

                "lookback_minutes":
                    25,
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

    for name, info in (
        configured_queries
        .get(
            "queries",
            {},
        )
        .items()
    ):

        if not info.get(
            "enabled",
            True,
        ):
            continue

        query = " ".join(
            str(
                info.get(
                    "query",
                    "",
                )
            ).split()
        )

        if not query:
            continue

        queries.append(
            {
                "name": name,

                "sport":
                    info.get(
                        "sport",
                        "Unknown",
                    ),

                "conversation_type":
                    info.get(
                        "conversation_type",
                        "GENERAL",
                    ),

                "query":
                    query,

                "cadence_minutes":
                    int(
                        info.get(
                            "cadence_minutes",
                            20,
                        )
                    ),

                "lookback_minutes":
                    int(
                        info.get(
                            "lookback_minutes",
                            40,
                        )
                    ),
            }
        )

    queries.extend(
        build_cfb_team_queries(
            team_config,
            max_chars,
        )
    )

    queries.extend(
        build_priority_account_queries(
            account_config,
            max_chars,
        )
    )

    return queries
