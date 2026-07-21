from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.database import RANKINGS_FILE, get_team_metric
from app.query_parser import parse_query


app = FastAPI(
    title="CFB Statistics Query",
    version="1.0.0",
)

BASE_DIR = Path(__file__).resolve().parent.parent
INDEX_FILE = BASE_DIR / "app" / "templates" / "index.html"
STATIC_DIR = BASE_DIR / "app" / "static"

app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static",
)


def get_available_teams() -> list[str]:
    """
    Return all distinct team names available in the rankings file.
    """
    if not RANKINGS_FILE.exists():
        return []

    connection = duckdb.connect(database=":memory:")

    try:
        rows = connection.execute(
            """
            SELECT DISTINCT team
            FROM read_parquet(?)
            WHERE team IS NOT NULL
              AND trim(team) <> ''
            ORDER BY team
            """,
            [str(RANKINGS_FILE)],
        ).fetchall()
    finally:
        connection.close()

    return [str(row[0]) for row in rows]


def get_latest_season() -> int:
    """
    Return the latest season available in the rankings file.
    Falls back to 2025 if the file is unavailable or empty.
    """
    if not RANKINGS_FILE.exists():
        return 2025

    connection = duckdb.connect(database=":memory:")

    try:
        result = connection.execute(
            """
            SELECT MAX(season)
            FROM read_parquet(?)
            """,
            [str(RANKINGS_FILE)],
        ).fetchone()
    finally:
        connection.close()

    if not result or result[0] is None:
        return 2025

    return int(result[0])


def humanize_metric(metric: str) -> str:
    """
    Convert internal metric names into user-facing labels.
    """
    labels = {
        "off_epa_per_play": "EPA per play",
        "off_epa_per_rush": "EPA per rush",
        "off_epa_per_pass": "EPA per pass",
        "off_success_rate": "success rate",
        "off_rush_success_rate": "rushing success rate",
        "off_pass_success_rate": "passing success rate",
        "def_epa_allowed_per_play": "defensive EPA allowed per play",
        "def_epa_allowed_per_rush": "defensive EPA allowed per rush",
        "def_epa_allowed_per_pass": "defensive EPA allowed per pass",
        "def_success_rate_allowed": "success rate allowed",
        "def_rush_success_rate_allowed": "rushing success rate allowed",
        "def_pass_success_rate_allowed": "passing success rate allowed",
    }

    return labels.get(metric, metric.replace("_", " "))


def format_answer(result: dict) -> str:
    """
    Build a readable answer from a statistics result.
    """
    metric = str(result["metric"])
    metric_label = humanize_metric(metric)

    value = float(result["value"])
    average = float(result["league_average"])
    difference = float(result["difference_from_average"])

    if "success_rate" in metric:
        value_text = f"{value:.1%}"
        average_text = f"{average:.1%}"
        absolute_difference_text = f"{abs(difference):.1%}"
    else:
        value_text = f"{value:.3f}"
        average_text = f"{average:.3f}"
        absolute_difference_text = f"{abs(difference):.3f}"

    if difference > 0:
        comparison_text = (
            f"{absolute_difference_text} above the FBS average"
        )
    elif difference < 0:
        comparison_text = (
            f"{absolute_difference_text} below the FBS average"
        )
    else:
        comparison_text = "equal to the FBS average"

    sample_size = result.get("sample_size")
    sample_text = (
        f"{int(sample_size):,}"
        if sample_size is not None
        else "an unavailable number of"
    )

    return (
        f"{result['team']} posted {value_text} {metric_label} "
        f"in {result['season']}. "
        f"That ranked {result['rank']} of "
        f"{result['teams_ranked']} teams. "
        f"The FBS average was {average_text}, putting "
        f"{result['team']} {comparison_text}. "
        f"The calculation included {sample_text} qualifying plays."
    )


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    """
    Serve the main search webpage.
    """
    if not INDEX_FILE.exists():
        return """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta
                name="viewport"
                content="width=device-width, initial-scale=1.0"
            >
            <title>CFB Statistics Query</title>
        </head>
        <body>
            <h1>CFB Statistics Query</h1>
            <p>The API is running, but index.html was not found.</p>
        </body>
        </html>
        """

    return INDEX_FILE.read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict:
    """
    Return a simple application and data-file health check.
    """
    return {
        "status": "ok",
        "rankings_file_exists": RANKINGS_FILE.exists(),
        "rankings_file": str(RANKINGS_FILE),
        "latest_season": get_latest_season(),
        "team_count": len(get_available_teams()),
    }


@app.get("/api/teams")
def teams() -> dict:
    """
    Return the list of available teams.
    """
    available_teams = get_available_teams()

    return {
        "count": len(available_teams),
        "teams": available_teams,
    }


@app.get("/api/team-stat")
def team_stat(
    team: str = Query(..., min_length=2),
    metric: str = Query(..., min_length=3),
    season: int | None = Query(default=None, ge=2014, le=2030),
) -> dict:
    """
    Retrieve a structured team statistic.
    """
    selected_season = season or get_latest_season()

    try:
        result = get_team_metric(
            team=team,
            season=selected_season,
            metric=metric,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No matching team statistic was found for "
                f"{team}, {selected_season}, and {metric}."
            ),
        )

    result["answer"] = format_answer(result)

    return result


@app.get("/api/search")
def search(
    q: str = Query(..., min_length=3),
) -> dict:
    """
    Parse a natural-language question and return a team statistic.
    """
    available_teams = get_available_teams()

    if not available_teams:
        raise HTTPException(
            status_code=503,
            detail=(
                "No teams are available. Confirm that "
                "combined_team_rankings.parquet exists."
            ),
        )

    latest_season = get_latest_season()

    parsed = parse_query(
        query=q,
        team_names=available_teams,
        default_season=latest_season,
    )

    if parsed["team"] is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "I could not identify a team in that question. "
                "Try including the full team name."
            ),
        )

    if parsed["metric"] is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "I could not identify a supported statistic. "
                "Try EPA per rush, EPA per pass, EPA per play, "
                "or success rate."
            ),
        )

    try:
        result = get_team_metric(
            team=parsed["team"],
            season=parsed["season"],
            metric=parsed["metric"],
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No matching statistic was found for "
                f"{parsed['team']} in {parsed['season']}."
            ),
        )

    return {
        "query": q,
        "parsed": parsed,
        "result": result,
        "answer": format_answer(result),
    }
