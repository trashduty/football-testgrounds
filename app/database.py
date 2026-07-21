from pathlib import Path
from typing import Any

import duckdb


BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"

RANKINGS_FILE = PROCESSED_DIR / "combined_team_rankings.parquet"
TEAM_SEASON_FILE = PROCESSED_DIR / "combined_team_season_stats.parquet"
TEAM_GAME_FILE = PROCESSED_DIR / "combined_team_game_stats.parquet"


ALLOWED_METRICS = {
    "off_epa_per_play",
    "off_epa_per_rush",
    "off_epa_per_pass",
    "off_success_rate",
    "off_rush_success_rate",
    "off_pass_success_rate",
    "def_epa_allowed_per_play",
    "def_epa_allowed_per_rush",
    "def_epa_allowed_per_pass",
    "def_success_rate_allowed",
    "def_rush_success_rate_allowed",
    "def_pass_success_rate_allowed",
}


def get_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:", read_only=False)


def get_team_metric(
    team: str,
    season: int,
    metric: str,
) -> dict[str, Any] | None:
    if metric not in ALLOWED_METRICS:
        raise ValueError(f"Unsupported metric: {metric}")

    if not RANKINGS_FILE.exists():
        raise FileNotFoundError(
            f"Rankings file not found: {RANKINGS_FILE}"
        )

    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT
                season,
                team,
                metric,
                value,
                rank,
                teams_ranked,
                percentile,
                league_average,
                difference_from_average,
                sample_size
            FROM read_parquet(?)
            WHERE lower(team) = lower(?)
              AND season = ?
              AND metric = ?
            LIMIT 1
            """,
            [
                str(RANKINGS_FILE),
                team.strip(),
                season,
                metric,
            ],
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    return {
        "season": row[0],
        "team": row[1],
        "metric": row[2],
        "value": row[3],
        "rank": row[4],
        "teams_ranked": row[5],
        "percentile": row[6],
        "league_average": row[7],
        "difference_from_average": row[8],
        "sample_size": row[9],
    }
