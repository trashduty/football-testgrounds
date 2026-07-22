from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import duckdb
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.chart_database import (
    SITUATIONAL_FILE,
    dataframe_to_records,
    get_available_chart_seasons,
    get_available_conferences,
    get_situational_metadata,
    get_team_tiers_data,
)
from app.charts import (
    TeamTiersChartOptions,
    render_team_tiers_image,
)
from app.database import RANKINGS_FILE, get_team_metric
from app.query_parser import parse_query


app = FastAPI(
    title="CFB Statistics Query",
    version="1.2.0",
)

BASE_DIR = Path(__file__).resolve().parent.parent
INDEX_FILE = BASE_DIR / "app" / "templates" / "index.html"
STATIC_DIR = BASE_DIR / "app" / "static"

LOGO_DIRECTORY = BASE_DIR / "assets" / "team_logos"
LOGO_MAP_FILE = (
    BASE_DIR
    / "data"
    / "processed"
    / "team_logo_map.csv"
)

app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static",
)


def get_available_teams() -> list[str]:
    """Return all teams available in the rankings file."""

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
    """Return the latest season in the rankings file."""

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


def get_logo_metadata() -> dict[str, Any]:
    """Return local team-logo asset information."""

    png_count = 0

    if LOGO_DIRECTORY.exists():
        png_count = sum(
            1
            for path in LOGO_DIRECTORY.glob("*.png")
            if path.is_file()
        )

    mapping_rows = 0
    mapped_teams: list[str] = []

    if LOGO_MAP_FILE.exists():
        try:
            mapping = pd.read_csv(LOGO_MAP_FILE)

            mapping_rows = len(mapping)

            if "cfbfastr_team" in mapping.columns:
                mapped_teams = sorted(
                    mapping["cfbfastr_team"]
                    .dropna()
                    .astype(str)
                    .str.strip()
                    .loc[lambda values: values != ""]
                    .unique()
                    .tolist()
                )
        except Exception:
            mapping_rows = 0
            mapped_teams = []

    return {
        "logo_directory_exists": LOGO_DIRECTORY.exists(),
        "logo_directory": str(LOGO_DIRECTORY),
        "logo_png_count": png_count,
        "logo_map_exists": LOGO_MAP_FILE.exists(),
        "logo_map_file": str(LOGO_MAP_FILE),
        "logo_map_rows": mapping_rows,
        "mapped_team_count": len(mapped_teams),
        "mapped_teams": mapped_teams,
    }


def humanize_metric(metric: str) -> str:
    """Convert internal metric names to readable labels."""

    labels = {
        "off_epa_per_play": "EPA per play",
        "off_epa_per_rush": "EPA per rush",
        "off_epa_per_pass": "EPA per pass",
        "off_success_rate": "success rate",
        "off_rush_success_rate": (
            "rushing success rate"
        ),
        "off_pass_success_rate": (
            "passing success rate"
        ),
        "def_epa_allowed_per_play": (
            "defensive EPA allowed per play"
        ),
        "def_epa_allowed_per_rush": (
            "defensive EPA allowed per rush"
        ),
        "def_epa_allowed_per_pass": (
            "defensive EPA allowed per pass"
        ),
        "def_success_rate_allowed": (
            "success rate allowed"
        ),
        "def_rush_success_rate_allowed": (
            "rushing success rate allowed"
        ),
        "def_pass_success_rate_allowed": (
            "passing success rate allowed"
        ),
    }

    return labels.get(
        metric,
        metric.replace("_", " "),
    )


def format_answer(result: dict[str, Any]) -> str:
    """Create a readable answer from a team metric."""

    metric = str(result["metric"])
    metric_label = humanize_metric(metric)

    value = float(result["value"])
    average = float(result["league_average"])

    difference = float(
        result["difference_from_average"]
    )

    if "success_rate" in metric:
        value_text = f"{value:.1%}"
        average_text = f"{average:.1%}"
        difference_text = f"{abs(difference):.1%}"
    else:
        value_text = f"{value:.3f}"
        average_text = f"{average:.3f}"
        difference_text = f"{abs(difference):.3f}"

    if difference > 0:
        comparison_text = (
            f"{difference_text} above the FBS average"
        )
    elif difference < 0:
        comparison_text = (
            f"{difference_text} below the FBS average"
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
        f"{result['team']} posted {value_text} "
        f"{metric_label} in {result['season']}. "
        f"That ranked {result['rank']} of "
        f"{result['teams_ranked']} teams. "
        f"The FBS average was {average_text}, putting "
        f"{result['team']} {comparison_text}. "
        f"The calculation included {sample_text} "
        f"qualifying plays."
    )


def parse_integer_csv(
    raw_value: str,
    *,
    field_name: str,
) -> list[int]:
    """Parse comma-separated integers from a query parameter."""

    try:
        values = [
            int(part.strip())
            for part in raw_value.split(",")
            if part.strip()
        ]
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{field_name} must contain "
                "comma-separated integers."
            ),
        ) from error

    if not values:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} cannot be empty.",
        )

    return values


def build_chart_options(
    *,
    season: int,
    week_start: int,
    week_end: int,
    play_type: str,
    downs: list[int],
    periods: list[int],
    exclude_garbage_time: bool,
    minimum_plays: int,
    conference: str | None,
    red_zone_only: bool,
    goal_to_go_only: bool,
    season_type: str | None,
) -> TeamTiersChartOptions:
    """Create chart-rendering options."""

    return TeamTiersChartOptions(
        season=season,
        week_start=week_start,
        week_end=week_end,
        play_type=play_type,
        downs=downs,
        periods=periods,
        exclude_garbage_time=exclude_garbage_time,
        minimum_plays=minimum_plays,
        conference=conference,
        red_zone_only=red_zone_only,
        goal_to_go_only=goal_to_go_only,
        season_type=season_type,
    )


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    """Serve the current statistics search interface."""

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
            <p>
                The API is running, but index.html was not found.
            </p>
        </body>
        </html>
        """

    return INDEX_FILE.read_text(
        encoding="utf-8"
    )


@app.get("/health")
def health() -> dict[str, Any]:
    """Return application, dataset, and logo health."""

    logo_metadata = get_logo_metadata()

    return {
        "status": "ok",
        "rankings_file_exists": (
            RANKINGS_FILE.exists()
        ),
        "rankings_file": str(RANKINGS_FILE),
        "situational_file_exists": (
            SITUATIONAL_FILE.exists()
        ),
        "situational_file": str(SITUATIONAL_FILE),
        "latest_season": get_latest_season(),
        "team_count": len(get_available_teams()),
        "logo_directory_exists": (
            logo_metadata["logo_directory_exists"]
        ),
        "logo_png_count": (
            logo_metadata["logo_png_count"]
        ),
        "logo_map_exists": (
            logo_metadata["logo_map_exists"]
        ),
        "logo_map_rows": (
            logo_metadata["logo_map_rows"]
        ),
    }


@app.get("/api/logos")
def logos() -> dict[str, Any]:
    """Return local team-logo metadata."""

    return get_logo_metadata()


@app.get("/api/teams")
def teams() -> dict[str, Any]:
    """Return teams available to the query app."""

    available_teams = get_available_teams()

    return {
        "count": len(available_teams),
        "teams": available_teams,
    }


@app.get("/api/team-stat")
def team_stat(
    team: str = Query(..., min_length=2),
    metric: str = Query(..., min_length=3),
    season: int | None = Query(
        default=None,
        ge=2014,
        le=2030,
    ),
) -> dict[str, Any]:
    """Return one structured team-season statistic."""

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
) -> dict[str, Any]:
    """Parse a natural-language statistics question."""

    available_teams = get_available_teams()

    if not available_teams:
        raise HTTPException(
            status_code=503,
            detail=(
                "No teams are available. Confirm that "
                "combined_team_rankings.parquet exists."
            ),
        )

    parsed = parse_query(
        query=q,
        team_names=available_teams,
        default_season=get_latest_season(),
    )

    if parsed["team"] is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "I could not identify a team in that "
                "question. Include the full team name."
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
                f"{parsed['team']} in "
                f"{parsed['season']}."
            ),
        )

    return {
        "query": q,
        "parsed": parsed,
        "result": result,
        "answer": format_answer(result),
    }


@app.get("/api/charts/metadata")
def chart_metadata() -> dict[str, Any]:
    """Return chart-data and filter metadata."""

    return {
        "dataset": get_situational_metadata(),
        "seasons": get_available_chart_seasons(),
        "conferences": get_available_conferences(),
        "logos": {
            key: value
            for key, value in get_logo_metadata().items()
            if key != "mapped_teams"
        },
        "supported_filters": {
            "play_types": [
                "all",
                "rush",
                "pass",
            ],
            "downs": [1, 2, 3, 4],
            "periods": [1, 2, 3, 4, 5],
            "garbage_time": True,
            "conference": True,
            "red_zone": True,
            "goal_to_go": True,
            "season_type": True,
        },
    }


@app.get("/api/charts/team-tiers/data")
def team_tiers_data(
    season: Annotated[
        int,
        Query(ge=2014, le=2030),
    ] = 2025,
    week_start: Annotated[
        int,
        Query(ge=0, le=30),
    ] = 1,
    week_end: Annotated[
        int,
        Query(ge=0, le=30),
    ] = 20,
    play_type: Annotated[
        str,
        Query(pattern="^(all|rush|pass)$"),
    ] = "all",
    downs: str = Query(default="1,2,3,4"),
    periods: str = Query(default="1,2,3,4"),
    exclude_garbage_time: bool = Query(
        default=True
    ),
    minimum_plays: Annotated[
        int,
        Query(ge=1, le=5000),
    ] = 100,
    conference: str | None = Query(default=None),
    red_zone_only: bool = Query(default=False),
    goal_to_go_only: bool = Query(default=False),
    season_type: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return team-tiers chart calculations as JSON."""

    selected_downs = parse_integer_csv(
        downs,
        field_name="downs",
    )

    selected_periods = parse_integer_csv(
        periods,
        field_name="periods",
    )

    try:
        dataframe = get_team_tiers_data(
            season=season,
            week_start=week_start,
            week_end=week_end,
            play_type=play_type,
            downs=selected_downs,
            periods=selected_periods,
            exclude_garbage_time=(
                exclude_garbage_time
            ),
            minimum_plays=minimum_plays,
            conference=conference,
            red_zone_only=red_zone_only,
            goal_to_go_only=goal_to_go_only,
            season_type=season_type,
        )
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    if dataframe.empty:
        raise HTTPException(
            status_code=404,
            detail=(
                "No teams met the selected chart filters "
                "and minimum-play requirement."
            ),
        )

    return {
        "chart": "team_tiers",
        "filters": {
            "season": season,
            "week_start": week_start,
            "week_end": week_end,
            "play_type": play_type,
            "downs": selected_downs,
            "periods": selected_periods,
            "exclude_garbage_time": (
                exclude_garbage_time
            ),
            "minimum_plays": minimum_plays,
            "conference": conference,
            "red_zone_only": red_zone_only,
            "goal_to_go_only": goal_to_go_only,
            "season_type": season_type,
        },
        "team_count": len(dataframe),
        "teams": dataframe_to_records(dataframe),
    }


@app.get(
    "/api/charts/team-tiers.png",
    response_class=Response,
)
def team_tiers_png(
    season: Annotated[
        int,
        Query(ge=2014, le=2030),
    ] = 2025,
    week_start: Annotated[
        int,
        Query(ge=0, le=30),
    ] = 1,
    week_end: Annotated[
        int,
        Query(ge=0, le=30),
    ] = 20,
    play_type: Annotated[
        str,
        Query(pattern="^(all|rush|pass)$"),
    ] = "all",
    downs: str = Query(default="1,2,3,4"),
    periods: str = Query(default="1,2,3,4"),
    exclude_garbage_time: bool = Query(
        default=True
    ),
    minimum_plays: Annotated[
        int,
        Query(ge=1, le=5000),
    ] = 100,
    conference: str | None = Query(default=None),
    red_zone_only: bool = Query(default=False),
    goal_to_go_only: bool = Query(default=False),
    season_type: str | None = Query(default=None),
    width: Annotated[
        int,
        Query(ge=800, le=3000),
    ] = 1600,
    height: Annotated[
        int,
        Query(ge=600, le=2400),
    ] = 1000,
    scale: Annotated[
        float,
        Query(gt=0, le=3),
    ] = 1.0,
    download: bool = Query(default=False),
) -> Response:
    """Render the filtered team-tiers PNG."""

    selected_downs = parse_integer_csv(
        downs,
        field_name="downs",
    )

    selected_periods = parse_integer_csv(
        periods,
        field_name="periods",
    )

    try:
        dataframe = get_team_tiers_data(
            season=season,
            week_start=week_start,
            week_end=week_end,
            play_type=play_type,
            downs=selected_downs,
            periods=selected_periods,
            exclude_garbage_time=(
                exclude_garbage_time
            ),
            minimum_plays=minimum_plays,
            conference=conference,
            red_zone_only=red_zone_only,
            goal_to_go_only=goal_to_go_only,
            season_type=season_type,
        )

        if dataframe.empty:
            raise HTTPException(
                status_code=404,
                detail=(
                    "No teams met the selected chart filters "
                    "and minimum-play requirement."
                ),
            )

        options = build_chart_options(
            season=season,
            week_start=week_start,
            week_end=week_end,
            play_type=play_type,
            downs=selected_downs,
            periods=selected_periods,
            exclude_garbage_time=(
                exclude_garbage_time
            ),
            minimum_plays=minimum_plays,
            conference=conference,
            red_zone_only=red_zone_only,
            goal_to_go_only=goal_to_go_only,
            season_type=season_type,
        )

        image_bytes = render_team_tiers_image(
            dataframe=dataframe,
            options=options,
            image_format="png",
            width=width,
            height=height,
            scale=scale,
        )

    except HTTPException:
        raise
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=(
                "The chart could not be rendered. "
                f"Technical detail: {error}"
            ),
        ) from error

    disposition = (
        "attachment"
        if download
        else "inline"
    )

    filename = (
        f"cfb-team-tiers-{season}-"
        f"weeks-{week_start}-{week_end}.png"
    )

    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": (
                f'{disposition}; filename="{filename}"'
            ),
            "Cache-Control": "public, max-age=300",
        },
    )
