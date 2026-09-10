#!/usr/bin/env python3
"""College Football team stats for the weekly matchup articles.

Uses the official CFBD Python library (`cfbd`, v5.x).

Article metrics:
    - Offensive Pass EPA
    - Offensive Rush EPA
    - Defensive Pass EPA
    - Defensive Rush EPA
    - Offensive Eckel Rate
    - Defensive Eckel Rate

Design:
    1. Pull directly from CFBD.
    2. Use rolling last-10-game averages.
    3. Cross season boundaries when necessary.
    4. Rank only unique mapped FBS teams.
    5. Rank 1 = best.
    6. Defensive EPA ranks invert because lower EPA allowed is better.

IMPORTANT:
The ranking pipeline explicitly enforces one row per team_id before rankings
are calculated. This prevents duplicated join rows from generating impossible
FBS ranks such as 200th or 300th.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

import cfbd
from cfbd.models.season_type import SeasonType


ROLLING_GAMES = 10

ECKEL_YARDS_TO_GOAL = 40

BOTH = SeasonType("both")

LOGO_ID_RE = re.compile(
    r"/500/(\d+)\.png"
)


# --------------------------------------------------------------------------- #
# Stat definitions
# --------------------------------------------------------------------------- #

STAT_SPECS: List[
    Tuple[str, str, bool]
] = [
    (
        "off_pass_epa",
        "Offensive Pass EPA",
        True,
    ),
    (
        "off_rush_epa",
        "Offensive Rush EPA",
        True,
    ),
    (
        "def_pass_epa",
        "Defensive Pass EPA",
        False,
    ),
    (
        "def_rush_epa",
        "Defensive Rush EPA",
        False,
    ),
    (
        "off_eckel",
        "Offensive Eckel Rate",
        True,
    ),
    (
        "def_eckel",
        "Defensive Eckel Rate",
        False,
    ),
]

STAT_COLS = [
    name
    for name, _, _
    in STAT_SPECS
]


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _ordinal(
    rank: object,
) -> str:
    """
    Convert numeric rank to ordinal.

    Example:
        1 -> 1st
        22 -> 22nd
    """

    if (
        rank is None
        or pd.isna(rank)
    ):
        return "unranked"

    rank = int(rank)

    suffix = (
        "th"
        if 10 <= rank % 100 <= 20
        else {
            1: "st",
            2: "nd",
            3: "rd",
        }.get(
            rank % 10,
            "th",
        )
    )

    return (
        f"{rank}{suffix}"
    )


def team_id_from_logo(
    url: object,
) -> Optional[int]:
    """
    Pull ESPN numeric team ID from logo URL.
    """

    if not isinstance(
        url,
        str,
    ):
        return None

    match = LOGO_ID_RE.search(
        url
    )

    return (
        int(match.group(1))
        if match
        else None
    )


def load_crosswalk(
    path: str,
) -> pd.DataFrame:
    """
    Read BTB crosswalk and normalize team_id.
    """

    cw = pd.read_csv(
        path
    )

    cw["team_id"] = pd.to_numeric(
        cw["team_id"],
        errors="coerce",
    ).astype(
        "Int64"
    )

    return cw


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #

def rank_team_stats(
    stats: pd.DataFrame,
) -> pd.DataFrame:
    """
    Add <stat>_rank columns.

    CRITICAL:
    Ranking must occur across one and only one row per FBS team.

    Rank 1 = best.

    Offensive metrics:
        higher = better

    Defensive EPA:
        lower EPA allowed = better

    Defensive Eckel:
        lower opponent opportunity rate = better
    """

    ranked = stats.copy()

    if "team_id" in ranked.columns:

        ranked["team_id"] = pd.to_numeric(
            ranked["team_id"],
            errors="coerce",
        ).astype(
            "Int64"
        )

        ranked = (
            ranked
            .dropna(
                subset=["team_id"]
            )
            .drop_duplicates(
                subset=["team_id"],
                keep="first",
            )
            .reset_index(
                drop=True
            )
        )

    elif "team" in ranked.columns:

        ranked = (
            ranked
            .drop_duplicates(
                subset=["team"],
                keep="first",
            )
            .reset_index(
                drop=True
            )
        )

    for (
        name,
        _,
        higher_is_better,
    ) in STAT_SPECS:

        if name not in ranked.columns:
            continue

        ranked[name] = pd.to_numeric(
            ranked[name],
            errors="coerce",
        )

        ranked[
            f"{name}_rank"
        ] = (
            ranked[name]
            .rank(
                ascending=(
                    not higher_is_better
                ),
                method="min",
                na_option="keep",
            )
            .astype(
                "Int64"
            )
        )

    return ranked


def build_cfb_tale_of_tape(
    bet_id: int,
    opp_id: int,
    ranked_stats: pd.DataFrame,
    crosswalk: pd.DataFrame,
) -> List[str]:
    """
    Build a branded tale-of-the-tape table for the matchup article.

    - Stat names are bold
    - Rows are styled via CSS class hooks
    - Better team in each category is bolded
    - Lower rank is considered better
    """

    def _lookup_names(
        cw: pd.DataFrame,
        column: str,
    ) -> Dict[int, str]:

        clean = (
            cw.dropna(
                subset=[
                    "team_id",
                    column,
                ]
            )
            .copy()
        )

        clean["team_id"] = pd.to_numeric(
            clean["team_id"],
            errors="coerce",
        )

        clean = clean.dropna(
            subset=["team_id"]
        )

        clean["team_id"] = (
            clean["team_id"]
            .astype(int)
        )

        return (
            clean.drop_duplicates(
                subset=["team_id"]
            )
            .set_index("team_id")[column]
            .astype(str)
            .to_dict()
        )

    def _safe_rank(
        row: pd.Series,
        column: str,
    ) -> Optional[int]:

        if row is None or column not in row.index:
            return None

        value = row.get(column)

        if value is None or pd.isna(value):
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _display_rank(
        value: Optional[int],
    ) -> str:

        if value is None:
            return "N/A"

        return f"#{value}"

    def _is_better(
        left: Optional[int],
        right: Optional[int],
    ) -> bool:
        """
        Lower rank is better.
        """

        if left is None:
            return False

        if right is None:
            return True

        return left < right

    id_to_full = _lookup_names(
        crosswalk,
        "btb_team",
    )

    id_to_short = _lookup_names(
        crosswalk,
        "btb_team_short",
    )

    bet_name = id_to_full.get(
        int(bet_id),
        str(bet_id),
    )

    opp_name = id_to_full.get(
        int(opp_id),
        str(opp_id),
    )

    bet_short = id_to_short.get(
        int(bet_id),
        bet_name,
    )

    opp_short = id_to_short.get(
        int(opp_id),
        opp_name,
    )

    stats = ranked_stats.copy()

    stats["team_id"] = pd.to_numeric(
        stats["team_id"],
        errors="coerce",
    )

    bet_row_df = stats[
        stats["team_id"]
        == int(bet_id)
    ]

    opp_row_df = stats[
        stats["team_id"]
        == int(opp_id)
    ]

    if bet_row_df.empty or opp_row_df.empty:
        return [
            "<p><em>Matchup stat table unavailable for this game.</em></p>"
        ]

    bet_row = bet_row_df.iloc[0]
    opp_row = opp_row_df.iloc[0]

    metric_rows = [
        (
            "Offensive Pass EPA Rank",
            "off_pass_epa_rank",
        ),
        (
            "Offensive Rush EPA Rank",
            "off_rush_epa_rank",
        ),
        (
            "Defensive Pass EPA Rank",
            "def_pass_epa_rank",
        ),
        (
            "Defensive Rush EPA Rank",
            "def_rush_epa_rank",
        ),
        (
            "Offensive Eckel Rate Rank",
            "off_eckel_rank",
        ),
        (
            "Defensive Eckel Rate Rank",
            "def_eckel_rank",
        ),
    ]

    lines: List[str] = [
        '<table class="btb-stats-table">',
        "<thead>",
        "<tr>",
        "<th>Stat</th>",
        f"<th>{html.escape(bet_short)}</th>",
        f"<th>{html.escape(opp_short)}</th>",
        "</tr>",
        "</thead>",
        "<tbody>",
    ]

    for (
        label,
        column,
    ) in metric_rows:

        bet_rank = _safe_rank(
            bet_row,
            column,
        )

        opp_rank = _safe_rank(
            opp_row,
            column,
        )

        bet_class = (
            ' class="btb-better"'
            if _is_better(
                bet_rank,
                opp_rank,
            )
            else ""
        )

        opp_class = (
            ' class="btb-better"'
            if _is_better(
                opp_rank,
                bet_rank,
            )
            else ""
        )

        lines.extend(
            [
                "<tr>",
                (
                    f'<td class="btb-stat-name">'
                    f"{html.escape(label)}"
                    f"</td>"
                ),
                (
                    f"<td{bet_class}>"
                    f"{_display_rank(bet_rank)}"
                    f"</td>"
                ),
                (
                    f"<td{opp_class}>"
                    f"{_display_rank(opp_rank)}"
                    f"</td>"
                ),
                "</tr>",
            ]
        )

    lines.extend(
        [
            "</tbody>",
            "</table>",
        ]
    )

    return lines


# --------------------------------------------------------------------------- #
# Rolling window
# --------------------------------------------------------------------------- #

def rolling_last_n_games(
    game_level: pd.DataFrame,
    n: int = ROLLING_GAMES,
) -> pd.DataFrame:
    """
    Collapse game-level data to the most recent N games for each team.

    The final result contains one row per team.
    """

    if game_level.empty:
        return game_level.copy()

    df = game_level.copy()

    df = df.sort_values(
        [
            "team",
            "season",
            "week",
        ]
    )

    recent = (
        df.groupby(
            "team",
            group_keys=False,
        )
        .tail(n)
    )

    present = [
        column
        for column in STAT_COLS
        if column
        in recent.columns
    ]

    rolled = (
        recent
        .groupby(
            "team",
            as_index=False,
        )[present]
        .mean()
    )

    return rolled


# --------------------------------------------------------------------------- #
# Eckel
# --------------------------------------------------------------------------- #

def compute_game_eckel(
    drives: pd.DataFrame,
    games: pd.DataFrame,
) -> pd.DataFrame:
    """
    Calculate per-team/week offensive and defensive Eckel rate.

    A drive counts as an Eckel/scoring-opportunity drive if it:
        - scores
        OR
        - starts at opponent 40 or closer
        OR
        - reaches opponent 40 or closer
    """

    if (
        drives.empty
        or games.empty
    ):
        return pd.DataFrame(
            columns=[
                "team",
                "season",
                "week",
                "off_eckel",
                "def_eckel",
            ]
        )

    gmap = (
        games[
            [
                "game_id",
                "season",
                "week",
            ]
        ]
        .drop_duplicates(
            subset=["game_id"]
        )
    )

    d = drives.merge(
        gmap,
        on="game_id",
        how="left",
        validate="many_to_one",
    )

    start = pd.to_numeric(
        d["start"],
        errors="coerce",
    )

    end = pd.to_numeric(
        d["end"],
        errors="coerce",
    )

    scoring = (
        d["scoring"]
        .fillna(False)
        .astype(bool)
    )

    d["eckel"] = (
        scoring
        | (
            start
            <= ECKEL_YARDS_TO_GOAL
        )
        | (
            end
            <= ECKEL_YARDS_TO_GOAL
        )
    ).astype(int)

    off = (
        d.groupby(
            [
                "offense",
                "season",
                "week",
            ]
        )["eckel"]
        .mean()
        .rename(
            "off_eckel"
        )
        .reset_index()
        .rename(
            columns={
                "offense": "team"
            }
        )
    )

    deff = (
        d.groupby(
            [
                "defense",
                "season",
                "week",
            ]
        )["eckel"]
        .mean()
        .rename(
            "def_eckel"
        )
        .reset_index()
        .rename(
            columns={
                "defense": "team"
            }
        )
    )

    return off.merge(
        deff,
        on=[
            "team",
            "season",
            "week",
        ],
        how="outer",
        validate="one_to_one",
    )


# --------------------------------------------------------------------------- #
# Venue
# --------------------------------------------------------------------------- #

def build_venue_lookup(
    games: pd.DataFrame,
) -> Dict[
    Tuple[frozenset, int],
    str,
]:
    """
    Map:
        ({home_id, away_id}, week) -> venue
    """

    lut: Dict[
        Tuple[frozenset, int],
        str,
    ] = {}

    for _, g in games.iterrows():

        if (
            pd.isna(
                g.get("home_id")
            )
            or pd.isna(
                g.get("away_id")
            )
            or pd.isna(
                g.get("week")
            )
        ):
            continue

        key = (
            frozenset(
                [
                    int(
                        g["home_id"]
                    ),
                    int(
                        g["away_id"]
                    ),
                ]
            ),
            int(
                g["week"]
            ),
        )

        lut[key] = (
            g.get("venue")
        )

    return lut


# --------------------------------------------------------------------------- #
# Build team stats
# --------------------------------------------------------------------------- #

def build_team_stats(
    epa: pd.DataFrame,
    eckel: pd.DataFrame,
    teams: pd.DataFrame,
    crosswalk: pd.DataFrame,
    n: int = ROLLING_GAMES,
) -> pd.DataFrame:
    """
    Build unique FBS team-level rolling stats.

    Important distinction:

    FCS/non-FBS opponents can remain in the GAME-LEVEL data because games
    against them are part of an FBS team's rolling performance history.

    But only teams returned by CFBD's current get_fbs_teams() endpoint are
    allowed into the final ranking population.

    Pipeline:

    EPA + Eckel game data
        ↓
    one team-season-week row
        ↓
    rolling last N games for every observed team
        ↓
    map current FBS teams to team_id
        ↓
    DROP teams with no FBS team_id
        ↓
    attach BTB crosswalk
        ↓
    one row per FBS team_id
        ↓
    rank across FBS only
    """

    # ------------------------------------------------------------------
    # 1. Combine EPA and Eckel data
    # ------------------------------------------------------------------

    game_level = epa.merge(
        eckel,
        on=[
            "team",
            "season",
            "week",
        ],
        how="outer",
    )

    present_stats = [
        column
        for column in STAT_COLS
        if column in game_level.columns
    ]

    # ------------------------------------------------------------------
    # 2. Guarantee one observation per team-season-week
    #
    # This prevents API duplication or merge artifacts from flowing into
    # the rolling averages.
    # ------------------------------------------------------------------

    game_level = (
        game_level
        .groupby(
            [
                "team",
                "season",
                "week",
            ],
            as_index=False,
            dropna=False,
        )[present_stats]
        .mean()
    )

    # ------------------------------------------------------------------
    # 3. Calculate rolling last-N-game averages
    #
    # This can still include FCS/non-FBS teams at this point.
    # That is okay.
    # ------------------------------------------------------------------

    rolled = rolling_last_n_games(
        game_level,
        n=n,
    )

    # rolling_last_n_games should already be unique by team,
    # but enforce it explicitly.
    rolled = (
        rolled
        .drop_duplicates(
            subset=["team"],
            keep="first",
        )
        .reset_index(drop=True)
    )

    # ------------------------------------------------------------------
    # 4. Build a clean CURRENT FBS school -> team_id mapping
    # ------------------------------------------------------------------

    teams_clean = teams.copy()

    teams_clean["team_id"] = pd.to_numeric(
        teams_clean["team_id"],
        errors="coerce",
    ).astype("Int64")

    teams_clean = (
        teams_clean
        .dropna(
            subset=[
                "team",
                "team_id",
            ]
        )
        .drop_duplicates(
            subset=["team"],
            keep="first",
        )
        [
            [
                "team",
                "team_id",
            ]
        ]
        .reset_index(drop=True)
    )

    # Helpful validation: each CFBD FBS school should map to one team ID.
    duplicate_fbs_names = (
        teams_clean["team"]
        .duplicated()
        .sum()
    )

    if duplicate_fbs_names:
        raise RuntimeError(
            "Duplicate school names found in CFBD FBS team mapping."
        )

    # ------------------------------------------------------------------
    # 5. Attach FBS team IDs
    #
    # Use many_to_one defensively. rolled should be one row/team and
    # teams_clean should be one mapping/team.
    # ------------------------------------------------------------------

    keyed = rolled.merge(
        teams_clean,
        on="team",
        how="left",
        validate="many_to_one",
    )

    # ------------------------------------------------------------------
    # 6. REMOVE NON-FBS / UNMAPPED TEAMS BEFORE USING team_id AS A KEY
    #
    # THIS IS THE FIX FOR THE ERROR YOU JUST HIT.
    #
    # FCS teams may have rolling data, but they should not participate in
    # FBS rankings and do not have current FBS team IDs.
    # ------------------------------------------------------------------

    unmapped_count = int(
        keyed["team_id"]
        .isna()
        .sum()
    )

    if unmapped_count:
        print(
            f"Excluding {unmapped_count} non-FBS/unmapped teams "
            f"from FBS rankings."
        )

    keyed = (
        keyed
        .dropna(
            subset=["team_id"]
        )
        .copy()
    )

    keyed["team_id"] = (
        keyed["team_id"]
        .astype("Int64")
    )

    # ------------------------------------------------------------------
    # 7. Check that the remaining FBS team IDs are actually unique
    # ------------------------------------------------------------------

    duplicate_ids = (
        keyed[
            keyed["team_id"].duplicated(
                keep=False
            )
        ][
            [
                "team",
                "team_id",
            ]
        ]
    )

    if not duplicate_ids.empty:
        raise RuntimeError(
            "Multiple CFBD team names mapped to the same FBS team_id:\n"
            + duplicate_ids.to_string(
                index=False
            )
        )

    # ------------------------------------------------------------------
    # 8. Clean BTB crosswalk
    # ------------------------------------------------------------------

    crosswalk_clean = crosswalk.copy()

    crosswalk_clean["team_id"] = pd.to_numeric(
        crosswalk_clean["team_id"],
        errors="coerce",
    ).astype("Int64")

    crosswalk_clean = (
        crosswalk_clean
        .dropna(
            subset=["team_id"]
        )
        .drop_duplicates(
            subset=["team_id"],
            keep="first",
        )
        .reset_index(drop=True)
    )

    # ------------------------------------------------------------------
    # 9. Attach BTB display names
    #
    # Now one_to_one validation is safe because <NA> IDs were removed.
    # ------------------------------------------------------------------

    keyed = keyed.merge(
        crosswalk_clean[
            [
                "team_id",
                "btb_team",
            ]
        ],
        on="team_id",
        how="left",
        validate="one_to_one",
    )

    # ------------------------------------------------------------------
    # 10. Final uniqueness enforcement
    # ------------------------------------------------------------------

    keyed = (
        keyed
        .drop_duplicates(
            subset=["team_id"],
            keep="first",
        )
        .reset_index(drop=True)
    )

    # ------------------------------------------------------------------
    # 11. Diagnostic output
    # ------------------------------------------------------------------

    mapped_name_count = int(
        keyed["btb_team"]
        .notna()
        .sum()
    )

    missing_btb_names = int(
        keyed["btb_team"]
        .isna()
        .sum()
    )

    print(
        f"FBS ranking population before ranking: "
        f"{keyed['team_id'].nunique()} unique teams."
    )

    print(
        f"BTB names mapped: {mapped_name_count}; "
        f"missing BTB names: {missing_btb_names}."
    )

    if missing_btb_names:

        missing = (
            keyed[
                keyed["btb_team"].isna()
            ][
                [
                    "team",
                    "team_id",
                ]
            ]
        )

        print(
            "FBS teams missing from BTB crosswalk:"
        )

        print(
            missing.to_string(
                index=False
            )
        )

    # ------------------------------------------------------------------
    # 12. Rank ONLY the valid FBS population
    # ------------------------------------------------------------------

    ranked = rank_team_stats(
        keyed
    )

    return ranked


# --------------------------------------------------------------------------- #
# CFBD client
# --------------------------------------------------------------------------- #

def make_client(
    api_key: Optional[str] = None,
) -> cfbd.ApiClient:

    key = (
        api_key
        or os.getenv(
            "CFBD_API_KEY"
        )
    )

    if not key:
        raise RuntimeError(
            "CFBD_API_KEY not set."
        )

    return cfbd.ApiClient(
        cfbd.Configuration(
            access_token=key
        )
    )


def _ppa(
    side: object,
    plays_attr: str,
) -> Optional[float]:

    sub = (
        getattr(
            side,
            plays_attr,
            None,
        )
        if side is not None
        else None
    )

    return (
        getattr(
            sub,
            "ppa",
            None,
        )
        if sub is not None
        else None
    )


# --------------------------------------------------------------------------- #
# CFBD fetchers
# --------------------------------------------------------------------------- #

def fetch_fbs_team_ids(
    year: int,
    client: cfbd.ApiClient,
) -> pd.DataFrame:

    teams = (
        cfbd.TeamsApi(
            client
        )
        .get_fbs_teams(
            year=year
        )
    )

    rows = [
        {
            "team": t.school,
            "team_id": t.id,
        }
        for t in teams
    ]

    return pd.DataFrame(
        rows
    )


def fetch_game_epa(
    years: List[int],
    client: cfbd.ApiClient,
) -> pd.DataFrame:

    api = cfbd.StatsApi(
        client
    )

    rows = []

    for year in years:

        games = (
            api.get_advanced_game_stats(
                year=year,
                exclude_garbage_time=True,
                season_type=BOTH,
            )
        )

        for g in games:

            off = g.offense
            deff = g.defense

            rows.append(
                {
                    "team": g.team,
                    "season": g.season,
                    "week": g.week,
                    "off_pass_epa": _ppa(
                        off,
                        "passing_plays",
                    ),
                    "off_rush_epa": _ppa(
                        off,
                        "rushing_plays",
                    ),
                    "def_pass_epa": _ppa(
                        deff,
                        "passing_plays",
                    ),
                    "def_rush_epa": _ppa(
                        deff,
                        "rushing_plays",
                    ),
                }
            )

    return pd.DataFrame(
        rows
    )


def fetch_games(
    years: List[int],
    client: cfbd.ApiClient,
) -> pd.DataFrame:

    api = cfbd.GamesApi(
        client
    )

    rows = []

    for year in years:

        games = api.get_games(
            year=year,
            season_type=BOTH,
        )

        for g in games:

            rows.append(
                {
                    "game_id": g.id,
                    "season": g.season,
                    "week": g.week,
                    "home_id": g.home_id,
                    "away_id": g.away_id,
                    "venue": g.venue,
                    "neutral_site": (
                        g.neutral_site
                    ),
                }
            )

    return pd.DataFrame(
        rows
    )


def fetch_drives(
    years: List[int],
    client: cfbd.ApiClient,
) -> pd.DataFrame:

    api = cfbd.DrivesApi(
        client
    )

    rows = []

    for year in years:

        drives = api.get_drives(
            year=year,
            season_type=BOTH,
        )

        for d in drives:

            rows.append(
                {
                    "game_id": d.game_id,
                    "offense": d.offense,
                    "defense": d.defense,
                    "scoring": bool(
                        d.scoring
                    ),
                    "start": (
                        d.start_yards_to_goal
                    ),
                    "end": (
                        d.end_yards_to_goal
                    ),
                }
            )

    return pd.DataFrame(
        rows
    )


# --------------------------------------------------------------------------- #
# End-to-end stats builder
# --------------------------------------------------------------------------- #

def build_cfb_stats_and_venues(
    years: List[int],
    crosswalk: pd.DataFrame,
    api_key: Optional[str] = None,
    n: int = ROLLING_GAMES,
) -> Tuple[
    pd.DataFrame,
    Dict[
        Tuple[frozenset, int],
        str,
    ],
]:

    with make_client(
        api_key
    ) as client:

        teams = fetch_fbs_team_ids(
            max(years),
            client,
        )

        epa = fetch_game_epa(
            years,
            client,
        )

        games = fetch_games(
            years,
            client,
        )

        drives = fetch_drives(
            years,
            client,
        )

    eckel = compute_game_eckel(
        drives,
        games,
    )

    stats = build_team_stats(
        epa,
        eckel,
        teams,
        crosswalk,
        n=n,
    )

    # ---------------------------------------------------------
    # Ranking sanity checks
    # ---------------------------------------------------------

    team_count = int(
        stats[
            "team_id"
        ].nunique()
    )

    if team_count == 0:
        raise RuntimeError(
            "CFB stats pipeline produced "
            "zero mapped FBS teams."
        )

    for (
        stat_name,
        _,
        _,
    ) in STAT_SPECS:

        rank_col = (
            f"{stat_name}_rank"
        )

        if rank_col not in stats.columns:
            continue

        max_rank = (
            stats[
                rank_col
            ].max()
        )

        if (
            pd.notna(
                max_rank
            )
            and int(
                max_rank
            ) > team_count
        ):
            raise RuntimeError(
                "Invalid FBS ranking detected: "
                f"{rank_col} has max rank "
                f"{max_rank}, but only "
                f"{team_count} unique teams exist."
            )

    duplicate_ids = (
        stats[
            "team_id"
        ]
        .duplicated()
        .sum()
    )

    if duplicate_ids:
        raise RuntimeError(
            "Duplicate team IDs survived "
            "the FBS ranking pipeline."
        )

    print(
        "CFB article stats built for "
        f"{team_count} unique FBS teams."
    )

    for (
        stat_name,
        display_name,
        _,
    ) in STAT_SPECS:

        rank_col = (
            f"{stat_name}_rank"
        )

        if rank_col in stats.columns:

            max_rank = (
                stats[
                    rank_col
                ].max()
            )

            print(
                f"  {display_name}: "
                f"max rank = {max_rank}"
            )

    return (
        stats,
        build_venue_lookup(
            games
        ),
    )


# --------------------------------------------------------------------------- #
# CSV alternative
# --------------------------------------------------------------------------- #

def load_team_stats_csv(
    path: str,
    crosswalk: pd.DataFrame,
) -> pd.DataFrame:
    """
    Optional alternative if using BTB's Team-Data-Table CSV rather than
    raw CFBD metrics.
    """

    td = pd.read_csv(
        path
    )

    colmap = {
        "Offensive Pass EPA":
            "off_pass_epa",

        "Offensive Rush EPA":
            "off_rush_epa",

        "Defensive Pass EPA":
            "def_pass_epa",

        "Defensive Rush EPA":
            "def_rush_epa",

        "Offensive Eckel Rate Over Expected (%)":
            "off_eckel",

        "Defensive Eckel Rate Over Expected (%)":
            "def_eckel",
    }

    td = td.rename(
        columns=colmap
    )

    crosswalk_clean = (
        crosswalk.copy()
    )

    crosswalk_clean[
        "team_id"
    ] = pd.to_numeric(
        crosswalk_clean[
            "team_id"
        ],
        errors="coerce",
    ).astype(
        "Int64"
    )

    short_to_id = (
        crosswalk_clean
        .dropna(
            subset=[
                "btb_team_short",
                "team_id",
            ]
        )
        .drop_duplicates(
            subset=[
                "btb_team_short"
            ]
        )
        .set_index(
            "btb_team_short"
        )[
            "team_id"
        ]
        .to_dict()
    )

    td[
        "team_id"
    ] = (
        td["Team"]
        .map(
            short_to_id
        )
        .astype(
            "Int64"
        )
    )

    keyed = td.merge(
        crosswalk_clean[
            [
                "team_id",
                "btb_team",
            ]
        ]
        .drop_duplicates(
            subset=[
                "team_id"
            ]
        ),
        on="team_id",
        how="left",
        validate="many_to_one",
    )

    keyed = (
        keyed
        .dropna(
            subset=[
                "team_id"
            ]
        )
        .drop_duplicates(
            subset=[
                "team_id"
            ],
            keep="first",
        )
        .reset_index(
            drop=True
        )
    )

    return rank_team_stats(
        keyed
    )
