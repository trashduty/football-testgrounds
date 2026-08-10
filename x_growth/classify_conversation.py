from __future__ import annotations


def classify_conversation(
    post: dict,
    hint: str = "GENERAL",
) -> str:

    text = (
        post.get("text", "")
        .lower()
    )

    media_types = set(
        post.get(
            "media_types",
            [],
        )
    )

    #
    # Strongest signals first.
    #

    injury_terms = [
        "injury",
        "injured",
        "out for",
        "questionable",
        "doubtful",
        "carted off",
        "medical",
        "surgery",
    ]

    if any(
        term in text
        for term in injury_terms
    ):
        return "INJURY"

    depth_terms = [
        "depth chart",
        "named starter",
        "starting quarterback",
        "qb1",
        "position battle",
        "starter",
        "benched",
    ]

    if any(
        term in text
        for term in depth_terms
    ):
        return "DEPTH_CHART"

    futures_terms = [
        "win total",
        "win totals",
        "national championship",
        "national title",
        "super bowl odds",
        "playoff odds",
        "conference odds",
    ]

    if any(
        term in text
        for term in futures_terms
    ):
        return "FUTURES"

    rankings_terms = [
        "ranking",
        "rankings",
        "top 25",
        "ap poll",
        "coaches poll",
        "overrated",
        "underrated",
    ]

    if any(
        term in text
        for term in rankings_terms
    ):
        return "RANKINGS"

    power_terms = [
        "power rating",
        "power ratings",
        "power ranking",
        "power rankings",
    ]

    if any(
        term in text
        for term in power_terms
    ):
        return "POWER_RATINGS"

    matchup_terms = [
        "vs.",
        " vs ",
        "matchup",
        "favorite",
        "underdog",
        "spread",
    ]

    if any(
        term in text
        for term in matchup_terms
    ):
        return "MATCHUP"

    camp_terms = [
        "training camp",
        "practice",
        "preseason",
        "joint practice",
        "scrimmage",
    ]

    if (
        hint == "NFL_CAMP"
        or any(
            term in text
            for term in camp_terms
        )
    ):
        return "NFL_CAMP"

    star_terms = [
        "quarterback",
        "qb",
        "rookie",
        "breakout",
        "contract",
        "holdout",
        "hold in",
        "extension",
    ]

    if (
        hint == "STAR_PLAYER_NEWS"
        or any(
            term in text
            for term in star_terms
        )
    ):
        return "STAR_PLAYER_NEWS"

    #
    # A team-focused video is automatically
    # valuable even without futures terminology.
    #
    if (
        "video" in media_types
        or hint == "TEAM_VIDEO"
    ):
        return "TEAM_VIDEO"

    if hint == "TEAM_HYPE":
        return "TEAM_HYPE"

    return hint or "GENERAL"
