from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.database import (
    RANKINGS_FILE,
    get_team_metric,
)
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
    if not RANKINGS_FILE.exists():
        return []

    connection = duckdb.connect()

    try:
        rows = connection.execute(
            """
            SELECT DISTINCT team
            FROM read_parquet(?)
            WHERE team IS NOT NULL
            ORDER BY team
            """,
            [str(RANKINGS_FILE)],
        ).fetchall()
    finally:
        connection.close()

    return [row[0] for row in rows]


def get_latest_season() -> int:
    if not RANKINGS_FILE.exists():
        return 2025

    connection = duckdb.connect()

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
    labels = {
        "off_epa_per_play": "EPA per play",
        "off_epa_per_rush": "EPA per rush",
        "off_epa_per_pass": "EPA per pass",
        "off_success_rate": "success rate",
        "off_rush_success_rate": "rushing success rate",
        "off_pass_success_rate": "passing success rate",
    }

    return labels.get(metric, metric.replace("_", " "))


def format_answer(result: dict) -> str:
    metric_label = humanize_metric(result["metric"])

    value = result["value"]
    average = result["league_average"]
    difference = result["difference_from_average"]

    if "success_rate" in result["metric"]:
        value_text = f"{value:.1%}"
        average_text = f"{average:.1%}"
        difference_text = f"{difference:+.1%}"
    else:
        value_text = f"{value:.3f}"
        average_text = f"{average:.3f}"
        difference_text = f"{difference:+.3f}"

    return (
        f"{result['team']} posted {value_text} {metric_label} "
        f"in {result['season']}. That ranked "
        f"{result['rank']} of {result['teams_ranked']} teams. "
        f"The FBS average was {average_text}, putting "
        f"{result['team']} {difference_text} above the average. "
        f"The calculation included {result['sample_size']} qualifying plays."
    )


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    if not INDEX_FILE.exists():
        return """
        <html>
            <body>
                <h1>CFB Statistics Query</h1>
                <p>The API is running.</p>
            </body>
        </html>
        """

    return INDEX_FILE.read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "rankings_file_exists": RANKINGS_FILE.exists(),
        "latest_season": get_latest_season(),
    }


@app.get("/api/team-stat")
def team_stat(
    team: str = Query(..., min_length=2),
    metric: str = Query(...),
    season: int | None = Query(default=None),
) -> dict:
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

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No matching team statistic was found.",
        )

    result["answer"] = format_answer(result)
    return result


@app.get("/api/search")
def search(
    q: str = Query(..., min_length=3),
) -> dict:
    teams = get_available_teams()
    latest_season = get_latest_season()

    parsed = parse_query(
        query=q,
        team_names=teams,
        default_season=latest_season,
    )

    if parsed["team"] is None:
        raise HTTPException(
            status_code=400,
            detail="I could not identify a team in that question.",
        )

    if parsed["metric"] is None:
        raise HTTPException(
            status_code=400,
            detail="I could not identify a supported statistic.",
        )

    result = get_team_metric(
        team=parsed["team"],
        season=parsed["season"],
        metric=parsed["metric"],
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No matching statistic was found.",
        )

    return {
        "query": q,
        "parsed": parsed,
        "result": result,
        "answer": format_answer(result),
    }
