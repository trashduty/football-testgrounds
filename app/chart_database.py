from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import duckdb
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"

SITUATIONAL_FILE = (
    PROCESSED_DIR / "historical_situational_stats.parquet"
)

PlayType = Literal["all", "rush", "pass"]


def get_connection() -> duckdb.DuckDBPyConnection:
    """Create a short-lived in-memory DuckDB connection."""
    return duckdb.connect(database=":memory:")


def _validate_integer_list(
    values: list[int],
    allowed_values: set[int],
    field_name: str,
) -> list[int]:
    """
    Validate and deduplicate an integer filter.

    The values are later passed to DuckDB as parameters rather than inserted
    directly from untrusted request text.
    """
    cleaned = sorted(set(int(value) for value in values))

    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty.")

    invalid = [
        value
        for value in cleaned
        if value not in allowed_values
    ]

    if invalid:
        raise ValueError(
            f"Unsupported {field_name}: {invalid}. "
            f"Allowed values are {sorted(allowed_values)}."
        )

    return cleaned


def _placeholders(count: int) -> str:
    """Return a comma-separated parameter placeholder string."""
    if count < 1:
        raise ValueError("At least one SQL placeholder is required.")

    return ", ".join(["?"] * count)


def situational_file_exists() -> bool:
    """Return whether the historical situational Parquet file exists."""
    return SITUATIONAL_FILE.exists()


def get_situational_metadata() -> dict[str, Any]:
    """
    Return basic metadata for the situational dataset.
    """
    if not SITUATIONAL_FILE.exists():
        return {
            "file_exists": False,
            "file": str(SITUATIONAL_FILE),
        }

    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS row_count,
                MIN(season) AS min_season,
                MAX(season) AS max_season,
                COUNT(DISTINCT team) AS team_count,
                COUNT(DISTINCT game_id) AS game_count,
                SUM(plays) AS play_count
            FROM read_parquet(?)
            """,
            [str(SITUATIONAL_FILE)],
        ).fetchone()
    finally:
        connection.close()

    return {
        "file_exists": True,
        "file": str(SITUATIONAL_FILE),
        "file_size_bytes": SITUATIONAL_FILE.stat().st_size,
        "row_count": int(row[0] or 0),
        "min_season": int(row[1]) if row[1] is not None else None,
        "max_season": int(row[2]) if row[2] is not None else None,
        "team_count": int(row[3] or 0),
        "game_count": int(row[4] or 0),
        "play_count": int(row[5] or 0),
    }


def get_available_chart_seasons() -> list[int]:
    """Return seasons available in the situational dataset."""
    if not SITUATIONAL_FILE.exists():
        return []

    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT DISTINCT season
            FROM read_parquet(?)
            WHERE season IS NOT NULL
            ORDER BY season DESC
            """,
            [str(SITUATIONAL_FILE)],
        ).fetchall()
    finally:
        connection.close()

    return [int(row[0]) for row in rows]

def get_available_chart_teams() -> list[str]:
    """Return all teams available in the situational dataset."""

    if not SITUATIONAL_FILE.exists():
        return []

    connection = duckdb.connect(database=":memory:")

    try:
        rows = connection.execute(
            """
            SELECT DISTINCT trim(team) AS team
            FROM read_parquet(?)
            WHERE team IS NOT NULL
              AND trim(team) <> ''
            ORDER BY team
            """,
            [str(SITUATIONAL_FILE)],
        ).fetchall()
    finally:
        connection.close()

    return [
        str(row[0])
        for row in rows
    ]

def get_available_conferences(
    season: int | None = None,
) -> list[str]:
    """Return offense conferences available for chart filtering."""
    if not SITUATIONAL_FILE.exists():
        return []

    params: list[Any] = [str(SITUATIONAL_FILE)]
    season_clause = ""

    if season is not None:
        season_clause = "AND season = ?"
        params.append(int(season))

    connection = get_connection()

    try:
        rows = connection.execute(
            f"""
            SELECT DISTINCT offense_conference
            FROM read_parquet(?)
            WHERE offense_conference IS NOT NULL
              AND trim(offense_conference) <> ''
              {season_clause}
            ORDER BY offense_conference
            """,
            params,
        ).fetchall()
    finally:
        connection.close()

    return [str(row[0]) for row in rows]


def get_team_tiers_data(
    *,
    season: int,
    week_start: int = 1,
    week_end: int = 20,
    play_type: PlayType = "all",
    downs: list[int] | None = None,
    periods: list[int] | None = None,
    exclude_garbage_time: bool = True,
    minimum_plays: int = 100,
    conference: str | None = None,
    red_zone_only: bool = False,
    goal_to_go_only: bool = False,
    season_type: str | None = None,
) -> pd.DataFrame:
    """
    Return one row per team for a team-tiers scatterplot.

    X axis:
        Offensive EPA per play

    Y axis:
        Defensive EPA allowed per play

    Defensive statistics are calculated by grouping offensive production
    against each value of `opponent`.
    """
    if not SITUATIONAL_FILE.exists():
        raise FileNotFoundError(
            "Situational statistics file was not found: "
            f"{SITUATIONAL_FILE}"
        )

    if season < 2014 or season > 2030:
        raise ValueError("Season must be between 2014 and 2030.")

    if week_start < 0 or week_end < 0:
        raise ValueError("Weeks cannot be negative.")

    if week_start > week_end:
        raise ValueError(
            "week_start cannot be greater than week_end."
        )

    if play_type not in {"all", "rush", "pass"}:
        raise ValueError(
            "play_type must be one of: all, rush, pass."
        )

    if minimum_plays < 1:
        raise ValueError("minimum_plays must be at least 1.")

    if minimum_plays > 5000:
        raise ValueError("minimum_plays is unreasonably large.")

    selected_downs = _validate_integer_list(
        downs or [1, 2, 3, 4],
        {1, 2, 3, 4},
        "downs",
    )

    selected_periods = _validate_integer_list(
        periods or [1, 2, 3, 4],
        {1, 2, 3, 4, 5},
        "periods",
    )

    where_clauses = [
        "season = ?",
        "week BETWEEN ? AND ?",
        f"down IN ({_placeholders(len(selected_downs))})",
        f"period IN ({_placeholders(len(selected_periods))})",
    ]

    parameters: list[Any] = [
        str(SITUATIONAL_FILE),
        int(season),
        int(week_start),
        int(week_end),
        *selected_downs,
        *selected_periods,
    ]

    if play_type != "all":
        where_clauses.append("play_type = ?")
        parameters.append(play_type)

    if exclude_garbage_time:
        where_clauses.append("garbage_time = FALSE")

    if conference:
        where_clauses.append("offense_conference = ?")
        parameters.append(conference.strip())

    if red_zone_only:
        where_clauses.append("red_zone = TRUE")

    if goal_to_go_only:
        where_clauses.append("goal_to_go = TRUE")

    if season_type:
        where_clauses.append("lower(season_type) = lower(?)")
        parameters.append(season_type.strip())

    where_sql = "\n              AND ".join(where_clauses)

    # The conference filter is applied to the offense in `filtered`.
    # To ensure both chart axes contain the same selected teams, the final
    # result also limits teams based on their offensive conference.
    sql = f"""
        WITH filtered AS (
            SELECT
                team,
                opponent,
                offense_conference,
                defense_conference,
                plays,
                epa_total,
                successes,
                yards_total,
                explosive_plays
            FROM read_parquet(?)
            WHERE {where_sql}
        ),

        offense AS (
            SELECT
                team,
                any_value(offense_conference)
                    AS conference,
                SUM(plays) AS offensive_plays,
                SUM(epa_total) AS offensive_epa_total,
                SUM(epa_total)
                    / NULLIF(SUM(plays), 0)
                    AS off_epa_per_play,
                SUM(successes)
                    / NULLIF(SUM(plays), 0)
                    AS off_success_rate,
                SUM(yards_total)
                    / NULLIF(SUM(plays), 0)
                    AS off_yards_per_play,
                SUM(explosive_plays)
                    / NULLIF(SUM(plays), 0)
                    AS off_explosive_rate
            FROM filtered
            GROUP BY team
        ),

        defense AS (
            SELECT
                opponent AS team,
                any_value(defense_conference)
                    AS conference,
                SUM(plays) AS defensive_plays,
                SUM(epa_total) AS defensive_epa_allowed_total,
                SUM(epa_total)
                    / NULLIF(SUM(plays), 0)
                    AS def_epa_allowed_per_play,
                SUM(successes)
                    / NULLIF(SUM(plays), 0)
                    AS def_success_rate_allowed,
                SUM(yards_total)
                    / NULLIF(SUM(plays), 0)
                    AS def_yards_allowed_per_play,
                SUM(explosive_plays)
                    / NULLIF(SUM(plays), 0)
                    AS def_explosive_rate_allowed
            FROM filtered
            GROUP BY opponent
        )

        SELECT
            offense.team,
            COALESCE(
                offense.conference,
                defense.conference
            ) AS conference,

            offense.off_epa_per_play,
            defense.def_epa_allowed_per_play,

            offense.off_success_rate,
            defense.def_success_rate_allowed,

            offense.off_yards_per_play,
            defense.def_yards_allowed_per_play,

            offense.off_explosive_rate,
            defense.def_explosive_rate_allowed,

            CAST(offense.offensive_plays AS BIGINT)
                AS offensive_plays,
            CAST(defense.defensive_plays AS BIGINT)
                AS defensive_plays
        FROM offense
        INNER JOIN defense
            ON offense.team = defense.team
        WHERE offense.offensive_plays >= ?
          AND defense.defensive_plays >= ?
        ORDER BY offense.team
    """

    parameters.extend(
        [
            int(minimum_plays),
            int(minimum_plays),
        ]
    )

    connection = get_connection()

    try:
        result = connection.execute(
            sql,
            parameters,
        ).fetchdf()
    finally:
        connection.close()

    if result.empty:
        return result

    numeric_columns = [
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

    for column in numeric_columns:
        if column in result.columns:
            result[column] = pd.to_numeric(
                result[column],
                errors="coerce",
            )

    return result


def dataframe_to_records(
    dataframe: pd.DataFrame,
) -> list[dict[str, Any]]:
    """
    Convert a DataFrame into JSON-safe records.

    Pandas NaN values are replaced with None.
    """
    if dataframe.empty:
        return []

    safe_frame = dataframe.astype(object).where(
        pd.notna(dataframe),
        None,
    )

    return safe_frame.to_dict(orient="records")
