from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd


# =============================================================================
# File locations
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent.parent

SITUATIONAL_FILE = (
    BASE_DIR
    / "data"
    / "processed"
    / "historical_situational_stats.parquet"
)

SITUATIONAL_METADATA_FILE = (
    BASE_DIR
    / "data"
    / "processed"
    / "historical_situational_metadata.json"
)

FBS_CROSSWALK_FILE = (
    BASE_DIR
    / "CFB Teams Full Crosswalk.csv"
)


# =============================================================================
# Known name differences between the parquet and crosswalk
# =============================================================================

RAW_TEAM_ALIASES = {
    "uconn": "Connecticut",
    "massachusetts": "UMass",
    "utsa": "UT San Antonio",
    "ul monroe": "Louisiana Monroe",
    "sam houston": "Sam Houston State",
    "san josé state": "San Jose State",
    "southern miss": "Southern Mississippi",
}


# =============================================================================
# Final chart-data structure
# =============================================================================

TEAM_TIERS_OUTPUT_COLUMNS = [
    "team",
    "conference",
    "off_epa_per_play",
    "def_epa_allowed_per_play",
    "off_success_rate",
    "def_success_rate_allowed",
    "off_yards_per_play",
    "def_yards_allowed_per_play",
    "off_explosive_rate",
    "def_explosive_rate_allowed",
    "offensive_plays",
    "defensive_plays",
]


# =============================================================================
# General helpers
# =============================================================================

def _require_file(
    path: Path,
    description: str,
) -> None:
    """Raise a readable error when a required file is missing."""

    if not path.exists():
        raise FileNotFoundError(
            f"{description} was not found at {path}."
        )


def _normalize_text(
    series: pd.Series,
) -> pd.Series:
    """Strip whitespace while preserving capitalization."""

    return (
        series
        .fillna("")
        .astype(str)
        .str.strip()
    )


def _normalize_team_key(
    series: pd.Series,
) -> pd.Series:
    """
    Create a standardized team-matching key and apply known aliases.

    The returned key is case-insensitive and collapses repeated spaces.
    Parquet aliases are converted to their matching crosswalk names.
    """

    normalized = (
        series
        .fillna("")
        .astype(str)
        .str.strip()
        .str.casefold()
        .str.replace(
            r"\s+",
            " ",
            regex=True,
        )
    )

    alias_lookup = {
        raw_name.casefold(): crosswalk_name.casefold()
        for raw_name, crosswalk_name in RAW_TEAM_ALIASES.items()
    }

    return normalized.replace(
        alias_lookup
    )


def _safe_rate(
    numerator: pd.Series,
    denominator: pd.Series,
) -> pd.Series:
    """Calculate a rate without dividing by zero."""

    numerator_values = pd.to_numeric(
        numerator,
        errors="coerce",
    )

    denominator_values = pd.to_numeric(
        denominator,
        errors="coerce",
    )

    return pd.Series(
        np.where(
            denominator_values > 0,
            numerator_values / denominator_values,
            np.nan,
        ),
        index=numerator.index,
        dtype="float64",
    )


def _get_parquet_columns() -> list[str]:
    """Return columns in the situational parquet."""

    _require_file(
        SITUATIONAL_FILE,
        "The historical situational statistics file",
    )

    connection = duckdb.connect(
        database=":memory:"
    )

    try:
        rows = connection.execute(
            """
            DESCRIBE
            SELECT *
            FROM read_parquet(?)
            """,
            [str(SITUATIONAL_FILE)],
        ).fetchall()
    finally:
        connection.close()

    return [
        str(row[0])
        for row in rows
    ]


# =============================================================================
# Authoritative FBS crosswalk
# =============================================================================

def get_fbs_crosswalk() -> pd.DataFrame:
    """
    Return the authoritative FBS team and conference crosswalk.

    The crosswalk contains only FBS teams. Its btb_team_short
    column provides the chart-facing team name.
    """

    _require_file(
        FBS_CROSSWALK_FILE,
        "The FBS team crosswalk",
    )

    crosswalk = pd.read_csv(
        FBS_CROSSWALK_FILE,
        dtype=str,
    )

    required_columns = {
        "btb_team_short",
        "conference",
    }

    missing_columns = required_columns.difference(
        crosswalk.columns
    )

    if missing_columns:
        raise ValueError(
            "The FBS crosswalk is missing required columns: "
            + ", ".join(
                sorted(missing_columns)
            )
        )

    crosswalk = crosswalk[
        [
            "btb_team_short",
            "conference",
        ]
    ].copy()

    crosswalk["btb_team_short"] = _normalize_text(
        crosswalk["btb_team_short"]
    )

    crosswalk["conference"] = _normalize_text(
        crosswalk["conference"]
    )

    crosswalk = crosswalk[
        crosswalk["btb_team_short"] != ""
    ].copy()

    crosswalk["team_key"] = _normalize_team_key(
        crosswalk["btb_team_short"]
    )

    crosswalk = crosswalk.drop_duplicates(
        subset=["team_key"],
        keep="first",
    )

    crosswalk = crosswalk.rename(
        columns={
            "btb_team_short": "team",
        }
    )

    return crosswalk[
        [
            "team_key",
            "team",
            "conference",
        ]
    ].reset_index(
        drop=True
    )


def get_fbs_team_names() -> list[str]:
    """Return all FBS teams listed in the crosswalk."""

    crosswalk = get_fbs_crosswalk()

    return sorted(
        crosswalk["team"]
        .dropna()
        .astype(str)
        .str.strip()
        .loc[
            lambda values:
            values != ""
        ]
        .unique()
        .tolist()
    )


def filter_to_fbs(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    Retain only teams found in the FBS crosswalk.

    The crosswalk's team and conference values become authoritative.
    """

    if dataframe.empty:
        result = dataframe.copy()

        if "conference" not in result.columns:
            result["conference"] = pd.Series(
                dtype="object"
            )

        return result

    if "team" not in dataframe.columns:
        raise ValueError(
            "The dataframe does not contain a team column."
        )

    crosswalk = get_fbs_crosswalk()

    filtered = dataframe.copy()

    filtered["team"] = _normalize_text(
        filtered["team"]
    )

    filtered["team_key"] = _normalize_team_key(
        filtered["team"]
    )

    # The crosswalk is the authoritative conference source.
    if "conference" in filtered.columns:
        filtered = filtered.drop(
            columns=["conference"]
        )

    filtered = filtered.merge(
        crosswalk,
        how="inner",
        on="team_key",
        suffixes=("_source", ""),
        validate="many_to_one",
    )

    if "team_source" in filtered.columns:
        filtered = filtered.drop(
            columns=["team_source"]
        )

    filtered = filtered.drop(
        columns=["team_key"]
    )

    return filtered.reset_index(
        drop=True
    )


# =============================================================================
# Metadata functions
# =============================================================================

def get_situational_metadata() -> dict[str, Any]:
    """Return metadata for the situational dataset."""

    if not SITUATIONAL_METADATA_FILE.exists():
        return {
            "file_exists": False,
            "file": str(
                SITUATIONAL_METADATA_FILE
            ),
        }

    try:
        with SITUATIONAL_METADATA_FILE.open(
            "r",
            encoding="utf-8",
        ) as metadata_file:
            metadata = json.load(
                metadata_file
            )

    except (
        OSError,
        json.JSONDecodeError,
    ) as error:
        return {
            "file_exists": True,
            "file": str(
                SITUATIONAL_METADATA_FILE
            ),
            "error": str(error),
        }

    if not isinstance(metadata, dict):
        return {
            "file_exists": True,
            "file": str(
                SITUATIONAL_METADATA_FILE
            ),
            "metadata": metadata,
        }

    result = dict(metadata)

    result["file_exists"] = True
    result["file"] = str(
        SITUATIONAL_METADATA_FILE
    )

    return result


def get_available_chart_seasons() -> list[int]:
    """Return seasons available in the situational dataset."""

    if not SITUATIONAL_FILE.exists():
        return []

    connection = duckdb.connect(
        database=":memory:"
    )

    try:
        rows = connection.execute(
            """
            SELECT DISTINCT
                CAST(season AS INTEGER) AS season
            FROM read_parquet(?)
            WHERE season IS NOT NULL
            ORDER BY season DESC
            """,
            [str(SITUATIONAL_FILE)],
        ).fetchall()
    finally:
        connection.close()

    return [
        int(row[0])
        for row in rows
        if row[0] is not None
    ]


def get_available_chart_teams() -> list[str]:
    """
    Return FBS teams found in the situational dataset.

    A team can appear as an offense, an opponent, or both.
    Known parquet aliases are normalized before the FBS join.
    """

    if not SITUATIONAL_FILE.exists():
        return []

    connection = duckdb.connect(
        database=":memory:"
    )

    try:
        parquet_teams = connection.execute(
            """
            SELECT DISTINCT team
            FROM (
                SELECT
                    trim(CAST(team AS VARCHAR)) AS team
                FROM read_parquet(?)
                WHERE team IS NOT NULL

                UNION

                SELECT
                    trim(CAST(opponent AS VARCHAR)) AS team
                FROM read_parquet(?)
                WHERE opponent IS NOT NULL
            )
            WHERE team <> ''
            ORDER BY team
            """,
            [
                str(SITUATIONAL_FILE),
                str(SITUATIONAL_FILE),
            ],
        ).df()
    finally:
        connection.close()

    if parquet_teams.empty:
        return []

    fbs_teams = filter_to_fbs(
        parquet_teams
    )

    return sorted(
        fbs_teams["team"]
        .dropna()
        .astype(str)
        .str.strip()
        .loc[
            lambda values:
            values != ""
        ]
        .unique()
        .tolist()
    )


def get_available_conferences() -> list[str]:
    """Return conferences from the FBS crosswalk only."""

    crosswalk = get_fbs_crosswalk()

    return sorted(
        crosswalk["conference"]
        .dropna()
        .astype(str)
        .str.strip()
        .loc[
            lambda values:
            values != ""
        ]
        .unique()
        .tolist()
    )


# =============================================================================
# Situational row query
# =============================================================================

def _load_filtered_rows(
    *,
    season: int,
    week_start: int,
    week_end: int,
    play_type: str,
    downs: list[int],
    periods: list[int],
    exclude_garbage_time: bool,
    red_zone_only: bool,
    goal_to_go_only: bool,
    season_type: str | None,
) -> pd.DataFrame:
    """Load raw situational rows using the requested filters."""

    _require_file(
        SITUATIONAL_FILE,
        "The historical situational statistics file",
    )

    available_columns = set(
        _get_parquet_columns()
    )

    required_columns = {
        "season",
        "week",
        "team",
        "opponent",
        "period",
        "down",
        "play_type",
        "red_zone",
        "goal_to_go",
        "garbage_time",
        "plays",
        "epa_total",
        "successes",
        "yards_total",
        "explosive_plays",
    }

    missing_columns = required_columns.difference(
        available_columns
    )

    if missing_columns:
        raise ValueError(
            "The situational dataset is missing required columns: "
            + ", ".join(
                sorted(missing_columns)
            )
        )

    conditions = [
        "CAST(season AS INTEGER) = ?",
        "CAST(week AS INTEGER) BETWEEN ? AND ?",
    ]

    parameters: list[Any] = [
        int(season),
        int(week_start),
        int(week_end),
    ]

    if play_type != "all":
        conditions.append(
            """
            lower(
                trim(
                    CAST(play_type AS VARCHAR)
                )
            ) = ?
            """
        )

        parameters.append(
            play_type.lower().strip()
        )

    if downs:
        down_placeholders = ", ".join(
            "?"
            for _ in downs
        )

        conditions.append(
            f"""
            CAST(down AS INTEGER)
            IN ({down_placeholders})
            """
        )

        parameters.extend(
            int(value)
            for value in downs
        )

    if periods:
        period_placeholders = ", ".join(
            "?"
            for _ in periods
        )

        conditions.append(
            f"""
            CAST(period AS INTEGER)
            IN ({period_placeholders})
            """
        )

        parameters.extend(
            int(value)
            for value in periods
        )

    if exclude_garbage_time:
        conditions.append(
            """
            coalesce(
                try_cast(
                    garbage_time AS BOOLEAN
                ),
                FALSE
            ) = FALSE
            """
        )

    if red_zone_only:
        conditions.append(
            """
            coalesce(
                try_cast(
                    red_zone AS BOOLEAN
                ),
                FALSE
            ) = TRUE
            """
        )

    if goal_to_go_only:
        conditions.append(
            """
            coalesce(
                try_cast(
                    goal_to_go AS BOOLEAN
                ),
                FALSE
            ) = TRUE
            """
        )

    if season_type:
        if "season_type" not in available_columns:
            raise ValueError(
                "The situational dataset does not contain "
                "a season_type column."
            )

        conditions.append(
            """
            lower(
                trim(
                    CAST(season_type AS VARCHAR)
                )
            ) = ?
            """
        )

        parameters.append(
            season_type.lower().strip()
        )

    where_clause = "\nAND ".join(
        conditions
    )

    connection = duckdb.connect(
        database=":memory:"
    )

    try:
        dataframe = connection.execute(
            f"""
            SELECT
                season,
                week,
                game_id,
                trim(CAST(team AS VARCHAR)) AS team,
                trim(CAST(opponent AS VARCHAR)) AS opponent,
                offense_conference,
                defense_conference,
                season_type,
                period,
                down,
                play_type,
                red_zone,
                goal_to_go,
                garbage_time,
                plays,
                epa_total,
                successes,
                yards_total,
                explosive_plays
            FROM read_parquet(?)
            WHERE {where_clause}
            """,
            [
                str(SITUATIONAL_FILE),
                *parameters,
            ],
        ).df()
    finally:
        connection.close()

    return dataframe


# =============================================================================
# Offense and defense aggregation
# =============================================================================

def _aggregate_team_rows(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    Create one chart row per team.

    Raw data is offense-oriented:

    - team is the offensive team;
    - opponent is the defensive team;
    - totals describe the offensive team's performance.

    Offensive statistics are grouped by team.
    Defensive statistics allowed are grouped by opponent.
    """

    if dataframe.empty:
        return pd.DataFrame(
            columns=TEAM_TIERS_OUTPUT_COLUMNS
        )

    required_columns = {
        "team",
        "opponent",
        "plays",
        "epa_total",
        "successes",
        "yards_total",
        "explosive_plays",
    }

    missing_columns = required_columns.difference(
        dataframe.columns
    )

    if missing_columns:
        raise ValueError(
            "The situational data is missing required "
            "aggregation columns: "
            + ", ".join(
                sorted(missing_columns)
            )
        )

    working = dataframe.copy()

    working["team"] = _normalize_text(
        working["team"]
    )

    working["opponent"] = _normalize_text(
        working["opponent"]
    )

    numeric_columns = [
        "plays",
        "epa_total",
        "successes",
        "yards_total",
        "explosive_plays",
    ]

    for column in numeric_columns:
        working[column] = pd.to_numeric(
            working[column],
            errors="coerce",
        ).fillna(0)

    working = working[
        (working["team"] != "")
        & (working["opponent"] != "")
        & (working["plays"] > 0)
    ].copy()

    if working.empty:
        return pd.DataFrame(
            columns=TEAM_TIERS_OUTPUT_COLUMNS
        )

    # -------------------------------------------------------------------------
    # Offensive statistics
    # -------------------------------------------------------------------------

    offense = (
        working.groupby(
            "team",
            as_index=False,
            sort=True,
        )
        .agg(
            offensive_plays=(
                "plays",
                "sum",
            ),
            offensive_epa_total=(
                "epa_total",
                "sum",
            ),
            offensive_successes=(
                "successes",
                "sum",
            ),
            offensive_yards_total=(
                "yards_total",
                "sum",
            ),
            offensive_explosive_plays=(
                "explosive_plays",
                "sum",
            ),
        )
    )

    offense["off_epa_per_play"] = _safe_rate(
        offense["offensive_epa_total"],
        offense["offensive_plays"],
    )

    offense["off_success_rate"] = _safe_rate(
        offense["offensive_successes"],
        offense["offensive_plays"],
    )

    offense["off_yards_per_play"] = _safe_rate(
        offense["offensive_yards_total"],
        offense["offensive_plays"],
    )

    offense["off_explosive_rate"] = _safe_rate(
        offense["offensive_explosive_plays"],
        offense["offensive_plays"],
    )

    offense = offense[
        [
            "team",
            "off_epa_per_play",
            "off_success_rate",
            "off_yards_per_play",
            "off_explosive_rate",
            "offensive_plays",
        ]
    ].copy()

    # -------------------------------------------------------------------------
    # Defensive statistics allowed
    # -------------------------------------------------------------------------

    defense = (
        working.groupby(
            "opponent",
            as_index=False,
            sort=True,
        )
        .agg(
            defensive_plays=(
                "plays",
                "sum",
            ),
            defensive_epa_allowed_total=(
                "epa_total",
                "sum",
            ),
            defensive_successes_allowed=(
                "successes",
                "sum",
            ),
            defensive_yards_allowed_total=(
                "yards_total",
                "sum",
            ),
            defensive_explosive_plays_allowed=(
                "explosive_plays",
                "sum",
            ),
        )
        .rename(
            columns={
                "opponent": "team",
            }
        )
    )

    defense["def_epa_allowed_per_play"] = _safe_rate(
        defense["defensive_epa_allowed_total"],
        defense["defensive_plays"],
    )

    defense["def_success_rate_allowed"] = _safe_rate(
        defense["defensive_successes_allowed"],
        defense["defensive_plays"],
    )

    defense["def_yards_allowed_per_play"] = _safe_rate(
        defense["defensive_yards_allowed_total"],
        defense["defensive_plays"],
    )

    defense["def_explosive_rate_allowed"] = _safe_rate(
        defense["defensive_explosive_plays_allowed"],
        defense["defensive_plays"],
    )

    defense = defense[
        [
            "team",
            "def_epa_allowed_per_play",
            "def_success_rate_allowed",
            "def_yards_allowed_per_play",
            "def_explosive_rate_allowed",
            "defensive_plays",
        ]
    ].copy()

    # -------------------------------------------------------------------------
    # Merge offense and defense
    # -------------------------------------------------------------------------

    combined = offense.merge(
        defense,
        how="outer",
        on="team",
        validate="one_to_one",
    )

    combined["team"] = _normalize_text(
        combined["team"]
    )

    combined = combined[
        combined["team"] != ""
    ].copy()

    for column in [
        "offensive_plays",
        "defensive_plays",
    ]:
        combined[column] = (
            pd.to_numeric(
                combined[column],
                errors="coerce",
            )
            .fillna(0)
            .round()
            .astype(int)
        )

    return combined.reset_index(
        drop=True
    )


# =============================================================================
# Public chart-data function
# =============================================================================

def get_team_tiers_data(
    *,
    season: int,
    week_start: int,
    week_end: int,
    play_type: str,
    downs: list[int],
    periods: list[int],
    exclude_garbage_time: bool,
    minimum_plays: int,
    conference: str | None = None,
    red_zone_only: bool = False,
    goal_to_go_only: bool = False,
    season_type: str | None = None,
) -> pd.DataFrame:
    """
    Return one offense-versus-defense row per qualifying FBS team.

    Raw games against FCS opponents remain part of each FBS team's
    statistics. Only the final chart population is restricted to FBS
    teams using the crosswalk.
    """

    if week_start > week_end:
        raise ValueError(
            "week_start cannot be greater than week_end."
        )

    if play_type not in {
        "all",
        "rush",
        "pass",
    }:
        raise ValueError(
            "play_type must be all, rush, or pass."
        )

    if not downs:
        raise ValueError(
            "At least one down must be selected."
        )

    if not periods:
        raise ValueError(
            "At least one period must be selected."
        )

    if minimum_plays < 1:
        raise ValueError(
            "minimum_plays must be at least 1."
        )

    raw_rows = _load_filtered_rows(
        season=season,
        week_start=week_start,
        week_end=week_end,
        play_type=play_type,
        downs=downs,
        periods=periods,
        exclude_garbage_time=exclude_garbage_time,
        red_zone_only=red_zone_only,
        goal_to_go_only=goal_to_go_only,
        season_type=season_type,
    )

    aggregated = _aggregate_team_rows(
        raw_rows
    )

    # Known aliases are normalized inside filter_to_fbs().
    # This join removes FCS teams from the chart population.
    fbs_data = filter_to_fbs(
        aggregated
    )

    fbs_data = fbs_data[
        (
            fbs_data["offensive_plays"]
            >= int(minimum_plays)
        )
        & (
            fbs_data["defensive_plays"]
            >= int(minimum_plays)
        )
    ].copy()

    if conference:
        conference_value = str(
            conference
        ).strip()

        fbs_data = fbs_data[
            fbs_data["conference"]
            == conference_value
        ].copy()

    for column in TEAM_TIERS_OUTPUT_COLUMNS:
        if column not in fbs_data.columns:
            fbs_data[column] = np.nan

    fbs_data = fbs_data[
        TEAM_TIERS_OUTPUT_COLUMNS
    ].copy()

    return (
        fbs_data
        .sort_values(
            by="team",
            ascending=True,
        )
        .reset_index(
            drop=True
        )
    )


# =============================================================================
# JSON conversion
# =============================================================================

def dataframe_to_records(
    dataframe: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Convert a DataFrame into JSON-safe dictionaries."""

    if dataframe.empty:
        return []

    records: list[dict[str, Any]] = []

    for raw_record in dataframe.to_dict(
        orient="records"
    ):
        record: dict[str, Any] = {}

        for key, value in raw_record.items():
            if value is None:
                record[key] = None

            elif isinstance(
                value,
                np.integer,
            ):
                record[key] = int(value)

            elif isinstance(
                value,
                np.floating,
            ):
                numeric_value = float(value)

                record[key] = (
                    numeric_value
                    if np.isfinite(numeric_value)
                    else None
                )

            elif isinstance(
                value,
                float,
            ):
                record[key] = (
                    value
                    if np.isfinite(value)
                    else None
                )

            elif isinstance(
                value,
                pd.Timestamp,
            ):
                record[key] = value.isoformat()

            elif pd.isna(value):
                record[key] = None

            else:
                record[key] = value

        records.append(
            record
        )

    return records
