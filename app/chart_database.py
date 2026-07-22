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
# Expected output columns
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

OFFENSIVE_METRIC_COLUMNS = [
    "off_epa_per_play",
    "off_success_rate",
    "off_yards_per_play",
    "off_explosive_rate",
]

DEFENSIVE_METRIC_COLUMNS = [
    "def_epa_allowed_per_play",
    "def_success_rate_allowed",
    "def_yards_allowed_per_play",
    "def_explosive_rate_allowed",
]


# =============================================================================
# General helpers
# =============================================================================

def _normalize_text_series(
    series: pd.Series,
) -> pd.Series:
    """Normalize a text column without changing capitalization."""

    return (
        series
        .fillna("")
        .astype(str)
        .str.strip()
    )


def _normalize_key_series(
    series: pd.Series,
) -> pd.Series:
    """
    Build a normalized matching key.

    This allows harmless differences in capitalization and spacing
    between the parquet data and the team crosswalk.
    """

    return (
        series
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
        .str.replace(
            r"\s+",
            " ",
            regex=True,
        )
    )


def _require_file(
    path: Path,
    description: str,
) -> None:
    """Raise a readable error when a required file is missing."""

    if not path.exists():
        raise FileNotFoundError(
            f"{description} was not found at {path}."
        )


def _get_parquet_columns() -> list[str]:
    """Return the columns present in the situational parquet file."""

    _require_file(
        SITUATIONAL_FILE,
        "The historical situational statistics file",
    )

    connection = duckdb.connect(
        database=":memory:"
    )

    try:
        description = connection.execute(
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
        for row in description
    ]


def _first_existing_column(
    available_columns: set[str],
    candidates: list[str],
) -> str | None:
    """Return the first candidate present in a dataset."""

    for candidate in candidates:
        if candidate in available_columns:
            return candidate

    return None


def _coerce_numeric_columns(
    dataframe: pd.DataFrame,
    columns: list[str],
) -> pd.DataFrame:
    """Convert requested columns to numeric values."""

    result = dataframe.copy()

    for column in columns:
        if column in result.columns:
            result[column] = pd.to_numeric(
                result[column],
                errors="coerce",
            )

    return result


def _weighted_average(
    values: pd.Series,
    weights: pd.Series,
) -> float:
    """Calculate a weighted mean while safely handling missing data."""

    numeric_values = pd.to_numeric(
        values,
        errors="coerce",
    )

    numeric_weights = pd.to_numeric(
        weights,
        errors="coerce",
    )

    valid = (
        numeric_values.notna()
        & numeric_weights.notna()
        & (numeric_weights > 0)
    )

    if valid.any():
        return float(
            np.average(
                numeric_values.loc[valid],
                weights=numeric_weights.loc[valid],
            )
        )

    valid_values = numeric_values.dropna()

    if valid_values.empty:
        return float("nan")

    return float(
        valid_values.mean()
    )


# =============================================================================
# FBS crosswalk
# =============================================================================

def get_fbs_crosswalk() -> pd.DataFrame:
    """
    Load the authoritative FBS crosswalk.

    The file contains only FBS teams. Its btb_team_short value is
    matched to the team value used by the situational dataset.
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

    selected_columns = [
        "btb_team_short",
        "conference",
    ]

    optional_columns = [
        "team_id",
        "btb_team",
        "cfbfastr_team",
        "api_team",
        "mascot",
        "logo",
    ]

    for column in optional_columns:
        if column in crosswalk.columns:
            selected_columns.append(column)

    crosswalk = crosswalk[
        selected_columns
    ].copy()

    crosswalk["btb_team_short"] = (
        _normalize_text_series(
            crosswalk["btb_team_short"]
        )
    )

    crosswalk["conference"] = (
        _normalize_text_series(
            crosswalk["conference"]
        )
    )

    crosswalk = crosswalk[
        crosswalk["btb_team_short"] != ""
    ].copy()

    crosswalk["team_key"] = (
        _normalize_key_series(
            crosswalk["btb_team_short"]
        )
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

    return crosswalk.reset_index(
        drop=True
    )


def get_fbs_team_names() -> list[str]:
    """Return every team in the authoritative FBS crosswalk."""

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
    Restrict a dataframe to teams in the FBS crosswalk.

    Conference values from the crosswalk replace conference values
    from the play-by-play source. This ensures FCS teams cannot affect
    displayed teams, benchmarks, axis ranges, or average intercepts.
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
            "The chart dataframe does not contain a team column."
        )

    crosswalk = get_fbs_crosswalk()

    filtered = dataframe.copy()

    filtered["team"] = (
        _normalize_text_series(
            filtered["team"]
        )
    )

    filtered["team_key"] = (
        _normalize_key_series(
            filtered["team"]
        )
    )

    # The crosswalk is the authoritative source of conference names.
    if "conference" in filtered.columns:
        filtered = filtered.drop(
            columns=["conference"]
        )

    crosswalk_columns = [
        "team_key",
        "team",
        "conference",
    ]

    filtered = filtered.merge(
        crosswalk[crosswalk_columns],
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
# Metadata
# =============================================================================

def get_situational_metadata() -> dict[str, Any]:
    """Load metadata created with the situational parquet dataset."""

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
        json.JSONDecodeError,
        OSError,
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

    metadata = dict(metadata)

    metadata["file_exists"] = True
    metadata["file"] = str(
        SITUATIONAL_METADATA_FILE
    )

    return metadata


def get_available_chart_seasons() -> list[int]:
    """Return seasons available in the situational parquet file."""

    if not SITUATIONAL_FILE.exists():
        return []

    available_columns = set(
        _get_parquet_columns()
    )

    season_column = _first_existing_column(
        available_columns,
        [
            "season",
            "year",
        ],
    )

    if season_column is None:
        return []

    connection = duckdb.connect(
        database=":memory:"
    )

    try:
        rows = connection.execute(
            f"""
            SELECT DISTINCT
                CAST("{season_column}" AS INTEGER) AS season
            FROM read_parquet(?)
            WHERE "{season_column}" IS NOT NULL
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

    FCS teams in the parquet file are excluded because only teams
    found in the crosswalk are returned.
    """

    if not SITUATIONAL_FILE.exists():
        return []

    available_columns = set(
        _get_parquet_columns()
    )

    team_column = _first_existing_column(
        available_columns,
        [
            "team",
            "school",
        ],
    )

    if team_column is None:
        return []

    connection = duckdb.connect(
        database=":memory:"
    )

    try:
        parquet_teams = connection.execute(
            f"""
            SELECT DISTINCT
                trim(CAST("{team_column}" AS VARCHAR)) AS team
            FROM read_parquet(?)
            WHERE "{team_column}" IS NOT NULL
              AND trim(
                    CAST("{team_column}" AS VARCHAR)
                  ) <> ''
            ORDER BY team
            """,
            [str(SITUATIONAL_FILE)],
        ).df()
    finally:
        connection.close()

    if parquet_teams.empty:
        return []

    filtered = filter_to_fbs(
        parquet_teams
    )

    return sorted(
        filtered["team"]
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
    """Return FBS conferences from the authoritative crosswalk."""

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
# Query construction
# =============================================================================

def _build_where_clause(
    *,
    available_columns: set[str],
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
) -> tuple[str, list[Any]]:
    """
    Build a filter clause using the column names present in the parquet.

    Several likely aliases are supported so the function remains tolerant
    of small naming differences between generated parquet versions.
    """

    conditions: list[str] = []
    parameters: list[Any] = []

    season_column = _first_existing_column(
        available_columns,
        [
            "season",
            "year",
        ],
    )

    if season_column is None:
        raise ValueError(
            "The situational dataset does not contain a season column."
        )

    conditions.append(
        f'CAST("{season_column}" AS INTEGER) = ?'
    )

    parameters.append(
        int(season)
    )

    week_column = _first_existing_column(
        available_columns,
        [
            "week",
            "week_number",
        ],
    )

    if week_column is not None:
        conditions.append(
            f'CAST("{week_column}" AS INTEGER) BETWEEN ? AND ?'
        )

        parameters.extend(
            [
                int(week_start),
                int(week_end),
            ]
        )

    play_type_column = _first_existing_column(
        available_columns,
        [
            "play_type",
            "play_category",
        ],
    )

    if (
        play_type != "all"
        and play_type_column is not None
    ):
        conditions.append(
            f'lower(trim(CAST("{play_type_column}" AS VARCHAR))) = ?'
        )

        parameters.append(
            play_type.lower()
        )

    down_column = _first_existing_column(
        available_columns,
        [
            "down",
            "downs",
        ],
    )

    if down_column is not None and downs:
        placeholders = ", ".join(
            "?"
            for _ in downs
        )

        conditions.append(
            f'CAST("{down_column}" AS INTEGER) IN ({placeholders})'
        )

        parameters.extend(
            int(value)
            for value in downs
        )

    period_column = _first_existing_column(
        available_columns,
        [
            "period",
            "quarter",
            "qtr",
        ],
    )

    if period_column is not None and periods:
        placeholders = ", ".join(
            "?"
            for _ in periods
        )

        conditions.append(
            f'CAST("{period_column}" AS INTEGER) IN ({placeholders})'
        )

        parameters.extend(
            int(value)
            for value in periods
        )

    garbage_column = _first_existing_column(
        available_columns,
        [
            "garbage_time",
            "is_garbage_time",
        ],
    )

    if (
        exclude_garbage_time
        and garbage_column is not None
    ):
        conditions.append(
            f"""
            coalesce(
                try_cast(
                    "{garbage_column}" AS BOOLEAN
                ),
                FALSE
            ) = FALSE
            """
        )

    red_zone_column = _first_existing_column(
        available_columns,
        [
            "red_zone",
            "is_red_zone",
        ],
    )

    if red_zone_only and red_zone_column is not None:
        conditions.append(
            f"""
            coalesce(
                try_cast(
                    "{red_zone_column}" AS BOOLEAN
                ),
                FALSE
            ) = TRUE
            """
        )

    goal_to_go_column = _first_existing_column(
        available_columns,
        [
            "goal_to_go",
            "is_goal_to_go",
        ],
    )

    if (
        goal_to_go_only
        and goal_to_go_column is not None
    ):
        conditions.append(
            f"""
            coalesce(
                try_cast(
                    "{goal_to_go_column}" AS BOOLEAN
                ),
                FALSE
            ) = TRUE
            """
        )

    season_type_column = _first_existing_column(
        available_columns,
        [
            "season_type",
        ],
    )

    if (
        season_type
        and season_type_column is not None
    ):
        conditions.append(
            f'lower(trim(CAST("{season_type_column}" AS VARCHAR))) = ?'
        )

        parameters.append(
            season_type.lower().strip()
        )

    return (
        "\nAND ".join(conditions),
        parameters,
    )


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
    """Load situational rows after applying requested game filters."""

    _require_file(
        SITUATIONAL_FILE,
        "The historical situational statistics file",
    )

    available_columns = set(
        _get_parquet_columns()
    )

    where_clause, parameters = _build_where_clause(
        available_columns=available_columns,
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

    connection = duckdb.connect(
        database=":memory:"
    )

    try:
        dataframe = connection.execute(
            f"""
            SELECT *
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
# Team aggregation
# =============================================================================

def _aggregate_team_rows(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    Aggregate filtered situational rows to one row per team.

    Metric values are weighted by the appropriate offensive or
    defensive play count.
    """

    if dataframe.empty:
        return pd.DataFrame(
            columns=TEAM_TIERS_OUTPUT_COLUMNS
        )

    available_columns = set(
        dataframe.columns
    )

    team_column = _first_existing_column(
        available_columns,
        [
            "team",
            "school",
        ],
    )

    if team_column is None:
        raise ValueError(
            "The situational dataset does not contain a team column."
        )

    offensive_plays_column = _first_existing_column(
        available_columns,
        [
            "offensive_plays",
            "off_plays",
            "offense_plays",
        ],
    )

    defensive_plays_column = _first_existing_column(
        available_columns,
        [
            "defensive_plays",
            "def_plays",
            "defense_plays",
        ],
    )

    if offensive_plays_column is None:
        raise ValueError(
            "The situational dataset does not contain "
            "an offensive play-count column."
        )

    if defensive_plays_column is None:
        raise ValueError(
            "The situational dataset does not contain "
            "a defensive play-count column."
        )

    required_metrics = (
        OFFENSIVE_METRIC_COLUMNS
        + DEFENSIVE_METRIC_COLUMNS
    )

    missing_metrics = [
        column
        for column in required_metrics
        if column not in dataframe.columns
    ]

    if missing_metrics:
        raise ValueError(
            "The situational dataset is missing required "
            "chart metric columns: "
            + ", ".join(
                sorted(missing_metrics)
            )
        )

    working = dataframe.copy()

    working["team"] = (
        _normalize_text_series(
            working[team_column]
        )
    )

    working["offensive_plays"] = pd.to_numeric(
        working[offensive_plays_column],
        errors="coerce",
    ).fillna(0)

    working["defensive_plays"] = pd.to_numeric(
        working[defensive_plays_column],
        errors="coerce",
    ).fillna(0)

    working = _coerce_numeric_columns(
        working,
        required_metrics,
    )

    aggregated_rows: list[dict[str, Any]] = []

    for team, group in working.groupby(
        "team",
        sort=True,
        dropna=False,
    ):
        team_name = str(team).strip()

        if not team_name:
            continue

        row: dict[str, Any] = {
            "team": team_name,
            "offensive_plays": int(
                round(
                    float(
                        group[
                            "offensive_plays"
                        ].sum()
                    )
                )
            ),
            "defensive_plays": int(
                round(
                    float(
                        group[
                            "defensive_plays"
                        ].sum()
                    )
                )
            ),
        }

        for metric in OFFENSIVE_METRIC_COLUMNS:
            row[metric] = _weighted_average(
                group[metric],
                group["offensive_plays"],
            )

        for metric in DEFENSIVE_METRIC_COLUMNS:
            row[metric] = _weighted_average(
                group[metric],
                group["defensive_plays"],
            )

        aggregated_rows.append(
            row
        )

    if not aggregated_rows:
        return pd.DataFrame(
            columns=TEAM_TIERS_OUTPUT_COLUMNS
        )

    return pd.DataFrame(
        aggregated_rows
    )


# =============================================================================
# Public chart query
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
    Return one row per qualifying FBS team.

    FBS filtering occurs before the result is returned, ensuring FCS
    teams do not affect:

    - benchmark counts;
    - average intercepts;
    - axis ranges;
    - conference lists;
    - team-search options;
    - chart logos.

    The conference argument remains available for callers that want
    the database function itself to return one conference. The current
    chart endpoints pass conference=None so the renderer can preserve
    the full-FBS benchmark population.
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

    # This inner join is the critical FBS-only step.
    fbs_data = filter_to_fbs(
        aggregated
    )

    fbs_data = fbs_data[
        (
            fbs_data["offensive_plays"]
            >= minimum_plays
        )
        & (
            fbs_data["defensive_plays"]
            >= minimum_plays
        )
    ].copy()

    if conference:
        normalized_conference = (
            str(conference).strip()
        )

        fbs_data = fbs_data[
            fbs_data["conference"]
            == normalized_conference
        ].copy()

    for column in TEAM_TIERS_OUTPUT_COLUMNS:
        if column not in fbs_data.columns:
            fbs_data[column] = np.nan

    fbs_data = fbs_data[
        TEAM_TIERS_OUTPUT_COLUMNS
    ].copy()

    return fbs_data.sort_values(
        by="team",
        ascending=True,
    ).reset_index(
        drop=True
    )


# =============================================================================
# JSON conversion
# =============================================================================

def dataframe_to_records(
    dataframe: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Convert a DataFrame to JSON-safe records."""

    if dataframe.empty:
        return []

    safe_dataframe = dataframe.copy()

    safe_dataframe = safe_dataframe.replace(
        {
            np.nan: None,
            np.inf: None,
            -np.inf: None,
        }
    )

    records = safe_dataframe.to_dict(
        orient="records"
    )

    json_safe_records: list[dict[str, Any]] = []

    for record in records:
        safe_record: dict[str, Any] = {}

        for key, value in record.items():
            if isinstance(
                value,
                np.integer,
            ):
                safe_record[key] = int(value)

            elif isinstance(
                value,
                np.floating,
            ):
                numeric_value = float(value)

                safe_record[key] = (
                    numeric_value
                    if np.isfinite(numeric_value)
                    else None
                )

            elif isinstance(
                value,
                pd.Timestamp,
            ):
                safe_record[key] = (
                    value.isoformat()
                )

            elif pd.isna(value):
                safe_record[key] = None

            else:
                safe_record[key] = value

        json_safe_records.append(
            safe_record
        )

    return json_safe_records
