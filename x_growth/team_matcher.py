from __future__ import annotations

import csv
import re

from collections import Counter
from pathlib import Path
from typing import Any


AMBIGUOUS_CFB_SHORT_NAMES = {
    "texas",
    "miami",
    "georgia",
    "indiana",
    "oregon",
    "oklahoma",
    "washington",
    "arizona",
    "california",
    "florida",
    "maryland",
    "virginia",
}


CFB_CONTEXT_TERMS = {
    "football",
    "cfb",
    "ncaa",
    "college football",
    "preseason",
    "training camp",
    "camp",
    "practice",
    "scrimmage",
    "quarterback",
    "qb",
    "coach",
    "coaching",
    "playoff",
    "cfp",
    "rankings",
    "ranking",
    "top 25",
    "conference",
    "sec",
    "big ten",
    "big 12",
    "acc",
    "heisman",
    "touchdown",
    "offense",
    "defense",
    "receiver",
    "wide receiver",
    "running back",
    "offensive line",
    "defensive line",
    "roster",
    "depth chart",
    "starter",
    "recruiting",
    "transfer portal",
    "win total",
    "national championship",
    "national title",
}


NFL_CONTEXT_TERMS = {
    "nfl",
    "football",
    "training camp",
    "camp",
    "preseason",
    "practice",
    "scrimmage",
    "quarterback",
    "qb",
    "rookie",
    "coach",
    "starter",
    "depth chart",
    "touchdown",
    "offense",
    "defense",
    "receiver",
    "wide receiver",
    "running back",
    "offensive line",
    "defensive line",
    "super bowl",
    "playoffs",
    "division",
    "win total",
}


def normalize(value: str | None) -> str:
    """
    Normalize text for matching.
    """

    if value is None:
        return ""

    value = str(value).strip().lower()

    if value in {
        "",
        "nan",
        "none",
        "null",
    }:
        return ""

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value


def contains_phrase(
    text: str,
    phrase: str,
) -> bool:
    """
    Match a phrase on reasonable word boundaries.

    Prevents:
        Texas matching Texasonian
        Miami matching MiamianSomething
    """

    text = normalize(text)
    phrase = normalize(phrase)

    if not phrase:
        return False

    escaped = re.escape(
        phrase
    )

    pattern = (
        r"(?<![a-z0-9])"
        + escaped
        + r"(?![a-z0-9])"
    )

    return bool(
        re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )
    )


def has_sport_context(
    text: str,
    sport: str,
) -> bool:

    text = normalize(text)

    if sport.upper() == "CFB":
        terms = CFB_CONTEXT_TERMS

    else:
        terms = NFL_CONTEXT_TERMS

    return any(
        contains_phrase(
            text,
            term,
        )
        for term in terms
    )


def load_cfb_crosswalk(
    path: str | Path,
) -> list[dict[str, Any]]:
    """
    Read the user's CFB Teams Full Crosswalk.
    """

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"CFB crosswalk not found: {path}"
        )

    rows = []

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        reader = csv.DictReader(f)

        required = {
            "btb_team_short",
            "mascot",
            "btb_team",
        }

        missing = (
            required
            - set(
                reader.fieldnames
                or []
            )
        )

        if missing:

            raise ValueError(
                "CFB crosswalk is missing "
                f"required columns: "
                f"{sorted(missing)}"
            )

        for row in reader:

            short_name = normalize(
                row.get(
                    "btb_team_short"
                )
            )

            mascot = normalize(
                row.get(
                    "mascot"
                )
            )

            full_name = normalize(
                row.get(
                    "btb_team"
                )
            )

            if not short_name:
                continue

            rows.append(
                {
                    **row,

                    "_short_norm":
                        short_name,

                    "_mascot_norm":
                        mascot,

                    "_full_norm":
                        full_name,
                }
            )

    return rows


def build_mascot_counts(
    crosswalk: list[dict],
) -> Counter:
    """
    Determine whether a mascot is unique.

    Example:
        Longhorns -> unique
        Bulldogs -> multiple schools
    """

    return Counter(
        row["_mascot_norm"]

        for row in crosswalk

        if row.get(
            "_mascot_norm"
        )
    )


def match_cfb_teams(
    text: str,
    crosswalk: list[dict],
    priority_config: dict | None = None,
) -> list[dict]:

    text_norm = normalize(
        text
    )

    football_context = (
        has_sport_context(
            text_norm,
            "CFB",
        )
    )

    mascot_counts = (
        build_mascot_counts(
            crosswalk
        )
    )

    priorities = {}

    if priority_config:

        priorities = (
            priority_config
            .get(
                "cfb",
                {},
            )
        )

    matches = []

    for row in crosswalk:

        short_name = row[
            "_short_norm"
        ]

        mascot = row[
            "_mascot_norm"
        ]

        full_name = row[
            "_full_norm"
        ]

        short_match = (
            contains_phrase(
                text_norm,
                short_name,
            )
        )

        mascot_match = (
            bool(mascot)
            and contains_phrase(
                text_norm,
                mascot,
            )
        )

        full_match = (
            bool(full_name)
            and contains_phrase(
                text_norm,
                full_name,
            )
        )

        mascot_unique = (
            bool(mascot)
            and mascot_counts[
                mascot
            ] == 1
        )

        ambiguous_short = (
            short_name
            in AMBIGUOUS_CFB_SHORT_NAMES
        )

        confidence = 0

        match_reason = None

        #
        # ---------------------------------------------
        # TIER 1
        # Exact full BTB team name.
        #
        # "Texas Longhorns"
        # "Miami Hurricanes"
        # ---------------------------------------------
        #

        if full_match:

            confidence = 100

            match_reason = (
                "full_btb_team"
            )

        #
        # ---------------------------------------------
        # TIER 2
        # Short name + mascot both appear.
        #
        # "Texas looks dangerous. The Longhorns..."
        # ---------------------------------------------
        #

        elif (
            short_match
            and mascot_match
        ):

            confidence = 98

            match_reason = (
                "short_plus_mascot"
            )

        #
        # ---------------------------------------------
        # TIER 3
        # Short name + explicit football context.
        #
        # "Texas football enters camp..."
        #
        # Less certain, but valid.
        # ---------------------------------------------
        #

        elif (
            short_match
            and football_context
        ):

            if ambiguous_short:

                confidence = 78

                match_reason = (
                    "ambiguous_short_plus_"
                    "football_context"
                )

            else:

                confidence = 85

                match_reason = (
                    "short_plus_"
                    "football_context"
                )

        #
        # ---------------------------------------------
        # TIER 4
        # Unique mascot + football context.
        #
        # "Longhorns opened practice..."
        #
        # Only allowed when mascot belongs to one team.
        # ---------------------------------------------
        #

        elif (
            mascot_match
            and mascot_unique
            and football_context
        ):

            confidence = 88

            match_reason = (
                "unique_mascot_plus_"
                "football_context"
            )

        #
        # ---------------------------------------------
        # Reject everything else.
        #
        # Examples:
        #
        # Texas Children's Hospital
        # Yung Miami
        # Georgia election results
        # Bulldogs (ambiguous mascot)
        # ---------------------------------------------
        #

        else:
            continue

        display_name = (
            row.get(
                "btb_team"
            )
            or row.get(
                "btb_team_short"
            )
        )

        short_display = (
            row.get(
                "btb_team_short"
            )
        )

        mascot_display = (
            row.get(
                "mascot"
            )
        )

        priority_info = (
            priorities.get(
                short_display,
                {},
            )
        )

        priority = int(
            priority_info.get(
                "priority",
                5,
            )
        )

        rank = (
            priority_info.get(
                "rank"
            )
        )

        matches.append(
            {
                "team":
                    display_name,

                "btb_team_short":
                    short_display,

                "mascot":
                    mascot_display,

                "sport":
                    "CFB",

                "priority":
                    priority,

                "rank":
                    rank,

                "match_confidence":
                    confidence,

                "match_reason":
                    match_reason,

                "short_matched":
                    short_match,

                "mascot_matched":
                    mascot_match,

                "full_name_matched":
                    full_match,

                "mascot_unique":
                    mascot_unique,

                "conference":
                    row.get(
                        "conference"
                    ),

                "team_id":
                    row.get(
                        "team_id"
                    ),

                "logo":
                    row.get(
                        "logo"
                    ),
            }
        )

    matches.sort(
        key=lambda item: (
            item[
                "match_confidence"
            ],
            item[
                "priority"
            ],
        ),
        reverse=True,
    )

    return matches


def identify_teams(
    text: str,
    team_config: dict[str, Any],
    cfb_crosswalk: list[dict] | None = None,
    sport_hint: str | None = None,
) -> list[dict]:

    sport_hint = (
        sport_hint.upper()
        if sport_hint
        else None
    )

    #
    # ---------------------------------------------
    # CFB
    # ---------------------------------------------
    #

    if (
        sport_hint in {
            None,
            "CFB",
        }
        and cfb_crosswalk
    ):

        cfb_matches = (
            match_cfb_teams(
                text=text,
                crosswalk=(
                    cfb_crosswalk
                ),
                priority_config=(
                    team_config
                ),
            )
        )

        if cfb_matches:
            return cfb_matches

    #
    # ---------------------------------------------
    # Existing NFL logic
    # ---------------------------------------------
    #

    matches = []

    if sport_hint == "CFB":
        return matches

    nfl_teams = (
        team_config.get(
            "nfl",
            {},
        )
    )

    football_context = (
        has_sport_context(
            text,
            "NFL",
        )
    )

    for (
        team_name,
        info,
    ) in nfl_teams.items():

        aliases = list(
            info.get(
                "aliases",
                [],
            )
        )

        aliases.append(
            team_name
        )

        matched_alias = None

        for alias in sorted(
            set(aliases),
            key=len,
            reverse=True,
        ):

            if contains_phrase(
                text,
                alias,
            ):

                matched_alias = alias
                break

        if not matched_alias:
            continue

        #
        # Require football context for very
        # generic NFL aliases if needed.
        #
        if not football_context:

            # Full NFL names remain sufficiently
            # specific to accept.
            if normalize(
                matched_alias
            ) != normalize(
                team_name
            ):
                continue

        matches.append(
            {
                "team":
                    team_name,

                "btb_team_short":
                    team_name,

                "mascot":
                    None,

                "sport":
                    "NFL",

                "priority":
                    int(
                        info.get(
                            "priority",
                            5,
                        )
                    ),

                "rank":
                    None,

                "match_confidence":
                    90,

                "match_reason":
                    "nfl_alias_match",

                "short_matched":
                    True,

                "mascot_matched":
                    False,

                "full_name_matched":
                    (
                        normalize(
                            matched_alias
                        )
                        == normalize(
                            team_name
                        )
                    ),

                "mascot_unique":
                    False,
            }
        )

    matches.sort(
        key=lambda item: (
            item[
                "match_confidence"
            ],
            item[
                "priority"
            ],
        ),
        reverse=True,
    )

    return matches
