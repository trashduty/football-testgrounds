from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from PIL import Image


BASE_DIR = Path(__file__).resolve().parent.parent

LOGO_MAP_FILE = (
    BASE_DIR
    / "data"
    / "processed"
    / "team_logo_map.csv"
)

DEFAULT_CHROME_PATH = Path(
    "/home/codespace/.local/share/choreographer/"
    "deps/chrome-linux64/chrome"
)

if (
    not os.environ.get("BROWSER_PATH")
    and DEFAULT_CHROME_PATH.exists()
):
    os.environ["BROWSER_PATH"] = str(
        DEFAULT_CHROME_PATH
    )


ImageFormat = Literal["png", "svg", "pdf"]


@dataclass(frozen=True)
class TeamTiersChartOptions:
    season: int
    week_start: int
    week_end: int
    play_type: str
    downs: list[int]
    periods: list[int]
    exclude_garbage_time: bool
    minimum_plays: int
    conference: str | None = None
    red_zone_only: bool = False
    goal_to_go_only: bool = False
    season_type: str | None = None


def _format_list(
    values: list[int],
    prefix: str,
) -> str:
    sorted_values = sorted(set(values))

    if (
        prefix == "Downs"
        and sorted_values == [1, 2, 3, 4]
    ):
        return "All downs"

    if (
        prefix == "Quarters"
        and sorted_values == [1, 2, 3, 4]
    ):
        return "Regulation"

    joined = ", ".join(
        str(value)
        for value in sorted_values
    )

    return f"{prefix}: {joined}"


def _build_subtitle(
    options: TeamTiersChartOptions,
) -> str:
    parts = [
        (
            f"Weeks {options.week_start}–"
            f"{options.week_end}"
            if options.week_start != options.week_end
            else f"Week {options.week_start}"
        ),
        (
            "All plays"
            if options.play_type == "all"
            else f"{options.play_type.title()} plays"
        ),
        _format_list(
            options.downs,
            "Downs",
        ),
        _format_list(
            options.periods,
            "Quarters",
        ),
        (
            "Competitive plays only"
            if options.exclude_garbage_time
            else "Includes extreme win-probability plays"
        ),
        (
            f"Minimum {options.minimum_plays} "
            "plays per unit"
        ),
    ]

    if options.conference:
        parts.append(options.conference)

    if options.red_zone_only:
        parts.append("Red zone only")

    if options.goal_to_go_only:
        parts.append("Goal-to-go only")

    if options.season_type:
        parts.append(
            f"{options.season_type.title()} season"
        )

    return " | ".join(parts)


def _load_logo_map() -> dict[str, Path]:
    """
    Load chart/PBP team names and their local logo paths.

    Expected columns:
        team
        logo_path
    """

    if not LOGO_MAP_FILE.exists():
        return {}

    mapping = pd.read_csv(
        LOGO_MAP_FILE,
        dtype=str,
    )

    required_columns = {
        "team",
        "logo_path",
    }

    missing = required_columns.difference(
        mapping.columns
    )

    if missing:
        raise ValueError(
            "Logo map is missing required columns: "
            + ", ".join(sorted(missing))
        )

    logo_map: dict[str, Path] = {}

    for _, row in mapping.iterrows():
        team_value = row.get("team")
        path_value = row.get("logo_path")

        if pd.isna(team_value) or pd.isna(path_value):
            continue

        team = str(team_value).strip()
        logo_path_raw = str(path_value).strip()

        if (
            not team
            or team.lower() == "nan"
            or not logo_path_raw
            or logo_path_raw.lower() == "nan"
        ):
            continue

        logo_path = Path(logo_path_raw)

        if not logo_path.is_absolute():
            logo_path = BASE_DIR / logo_path

        if logo_path.exists() and logo_path.is_file():
            logo_map[team] = logo_path

    return logo_map


def _image_to_data_uri(
    image_path: Path,
) -> str:
    """
    Embed a local PNG in the Plotly figure.

    Data URIs are more reliable than local filesystem URLs when
    Kaleido launches its separate Chrome process.
    """

    with Image.open(image_path) as image:
        image = image.convert("RGBA")

        buffer = BytesIO()

        image.save(
            buffer,
            format="PNG",
            optimize=True,
        )

    encoded = base64.b64encode(
        buffer.getvalue()
    ).decode("ascii")

    return f"data:image/png;base64,{encoded}"


def _prepare_chart_data(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    required_columns = {
        "team",
        "off_epa_per_play",
        "def_epa_allowed_per_play",
        "offensive_plays",
        "defensive_plays",
    }

    missing = required_columns.difference(
        dataframe.columns
    )

    if missing:
        raise ValueError(
            "Chart data is missing required columns: "
            + ", ".join(sorted(missing))
        )

    chart_data = dataframe.dropna(
        subset=[
            "off_epa_per_play",
            "def_epa_allowed_per_play",
        ]
    ).copy()

    if chart_data.empty:
        raise ValueError(
            "No teams met the selected chart filters."
        )

    chart_data["team"] = (
        chart_data["team"]
        .astype(str)
        .str.strip()
    )

    return chart_data


def _calculate_logo_sizes(
    chart_data: pd.DataFrame,
) -> tuple[float, float]:
    x_min = float(
        chart_data["off_epa_per_play"].min()
    )

    x_max = float(
        chart_data["off_epa_per_play"].max()
    )

    y_min = float(
        chart_data[
            "def_epa_allowed_per_play"
        ].min()
    )

    y_max = float(
        chart_data[
            "def_epa_allowed_per_play"
        ].max()
    )

    x_range = max(
        x_max - x_min,
        0.01,
    )

    y_range = max(
        y_max - y_min,
        0.01,
    )

    # Increase or decrease these percentages to adjust logo size.
    logo_width = x_range * 0.038
    logo_height = y_range * 0.055

    return logo_width, logo_height


def _build_hover_text(
    row: pd.Series,
) -> str:
    conference = row.get("conference")

    if pd.isna(conference) or not str(conference).strip():
        conference = "Unknown"

    return (
        f"<b>{row['team']}</b><br>"
        f"Conference: {conference}<br>"
        f"Offensive EPA/play: "
        f"{row['off_epa_per_play']:.3f}<br>"
        f"Defensive EPA allowed/play: "
        f"{row['def_epa_allowed_per_play']:.3f}<br>"
        f"Offensive plays: "
        f"{int(row['offensive_plays']):,}<br>"
        f"Defensive plays: "
        f"{int(row['defensive_plays']):,}"
    )


def build_team_tiers_figure(
    dataframe: pd.DataFrame,
    options: TeamTiersChartOptions,
) -> go.Figure:
    chart_data = _prepare_chart_data(
        dataframe
    )

    logo_map = _load_logo_map()

    offense_average = float(
        chart_data["off_epa_per_play"].mean()
    )

    defense_average = float(
        chart_data[
            "def_epa_allowed_per_play"
        ].mean()
    )

    x_min = float(
        chart_data["off_epa_per_play"].min()
    )

    x_max = float(
        chart_data["off_epa_per_play"].max()
    )

    y_min = float(
        chart_data[
            "def_epa_allowed_per_play"
        ].min()
    )

    y_max = float(
        chart_data[
            "def_epa_allowed_per_play"
        ].max()
    )

    x_padding = max(
        (x_max - x_min) * 0.10,
        0.015,
    )

    y_padding = max(
        (y_max - y_min) * 0.10,
        0.015,
    )

    logo_width, logo_height = (
        _calculate_logo_sizes(chart_data)
    )

    chart_data["hover_text"] = chart_data.apply(
        _build_hover_text,
        axis=1,
    )

    figure = go.Figure()

    # Transparent points retain interactive hover behavior.
    figure.add_trace(
        go.Scatter(
            x=chart_data["off_epa_per_play"],
            y=chart_data[
                "def_epa_allowed_per_play"
            ],
            mode="markers",
            customdata=chart_data["hover_text"],
            hovertemplate=(
                "%{customdata}<extra></extra>"
            ),
            marker={
                "size": 34,
                "opacity": 0.001,
            },
            showlegend=False,
            name="Teams",
        )
    )

    missing_logo_teams: list[str] = []

    for _, row in chart_data.iterrows():
        team = str(row["team"]).strip()
        logo_path = logo_map.get(team)

        if logo_path is None:
            missing_logo_teams.append(team)

            figure.add_trace(
                go.Scatter(
                    x=[
                        float(
                            row["off_epa_per_play"]
                        )
                    ],
                    y=[
                        float(
                            row[
                                "def_epa_allowed_per_play"
                            ]
                        )
                    ],
                    mode="markers+text",
                    text=[team],
                    textposition="top center",
                    marker={
                        "size": 11,
                        "opacity": 0.8,
                    },
                    textfont={
                        "size": 8,
                    },
                    hoverinfo="skip",
                    showlegend=False,
                )
            )

            continue

        try:
            source = _image_to_data_uri(
                logo_path
            )
        except Exception:
            missing_logo_teams.append(team)
            continue

        figure.add_layout_image(
            {
                "source": source,
                "xref": "x",
                "yref": "y",
                "x": float(
                    row["off_epa_per_play"]
                ),
                "y": float(
                    row[
                        "def_epa_allowed_per_play"
                    ]
                ),
                "sizex": logo_width,
                "sizey": logo_height,
                "xanchor": "center",
                "yanchor": "middle",
                "sizing": "contain",
                "opacity": 1.0,
                "layer": "above",
            }
        )

    figure.add_vline(
        x=offense_average,
        line_width=1.5,
        line_dash="dash",
        line_color="rgba(60, 70, 90, 0.65)",
    )

    figure.add_hline(
        y=defense_average,
        line_width=1.5,
        line_dash="dash",
        line_color="rgba(60, 70, 90, 0.65)",
    )

    subtitle = _build_subtitle(options)

    figure.update_layout(
        title={
            "text": (
                f"<b>{options.season} "
                "CFB Team Tiers</b>"
                f"<br><sup>{subtitle}</sup>"
            ),
            "x": 0.03,
            "xanchor": "left",
            "y": 0.97,
            "yanchor": "top",
            "font": {
                "size": 27,
            },
        },
        width=1600,
        height=1000,
        margin={
            "l": 110,
            "r": 80,
            "t": 130,
            "b": 105,
        },
        paper_bgcolor="white",
        plot_bgcolor="rgb(248, 250, 253)",
        font={
            "family": (
                "Arial, Helvetica, sans-serif"
            ),
            "color": "rgb(25, 35, 55)",
        },
        showlegend=False,
        hoverlabel={
            "bgcolor": "white",
            "font_size": 13,
            "font_family": "Arial",
        },
    )

    figure.update_xaxes(
        title={
            "text": (
                "Offensive EPA per play "
                "→ better offense"
            ),
            "font": {
                "size": 17,
            },
        },
        range=[
            x_min - x_padding,
            x_max + x_padding,
        ],
        zeroline=True,
        zerolinewidth=1,
        zerolinecolor=(
            "rgba(80, 90, 110, 0.35)"
        ),
        gridcolor=(
            "rgba(120, 130, 150, 0.16)"
        ),
        tickformat=".3f",
        tickfont={
            "size": 12,
        },
    )

    # Lower defensive EPA allowed is better, so the axis is reversed.
    figure.update_yaxes(
        title={
            "text": (
                "Better defense ← "
                "defensive EPA allowed per play"
            ),
            "font": {
                "size": 17,
            },
        },
        range=[
            y_max + y_padding,
            y_min - y_padding,
        ],
        zeroline=True,
        zerolinewidth=1,
        zerolinecolor=(
            "rgba(80, 90, 110, 0.35)"
        ),
        gridcolor=(
            "rgba(120, 130, 150, 0.16)"
        ),
        tickformat=".3f",
        tickfont={
            "size": 12,
        },
    )

    quadrant_annotations = [
        {
            "x": 0.99,
            "y": 0.99,
            "text": "Strong offense / strong defense",
            "xanchor": "right",
            "yanchor": "top",
        },
        {
            "x": 0.01,
            "y": 0.99,
            "text": "Weak offense / strong defense",
            "xanchor": "left",
            "yanchor": "top",
        },
        {
            "x": 0.99,
            "y": 0.01,
            "text": "Strong offense / weak defense",
            "xanchor": "right",
            "yanchor": "bottom",
        },
        {
            "x": 0.01,
            "y": 0.01,
            "text": "Weak offense / weak defense",
            "xanchor": "left",
            "yanchor": "bottom",
        },
    ]

    for annotation in quadrant_annotations:
        figure.add_annotation(
            x=annotation["x"],
            y=annotation["y"],
            xref="paper",
            yref="paper",
            text=annotation["text"],
            xanchor=annotation["xanchor"],
            yanchor=annotation["yanchor"],
            showarrow=False,
            font={
                "size": 12,
                "color": "rgb(70, 80, 100)",
            },
            bgcolor="rgba(255,255,255,0.72)",
            borderpad=5,
        )

    figure.add_annotation(
        x=0.5,
        y=-0.13,
        xref="paper",
        yref="paper",
        text=(
            "Source: CFBD historical play-by-play | "
            "Dashed lines represent displayed-team averages"
        ),
        showarrow=False,
        font={
            "size": 12,
            "color": "rgb(90, 100, 120)",
        },
    )

    if missing_logo_teams:
        print(
            "Missing or unreadable logos:",
            ", ".join(
                sorted(set(missing_logo_teams))
            ),
        )

    return figure


def render_figure_bytes(
    figure: go.Figure,
    *,
    image_format: ImageFormat = "png",
    width: int = 1600,
    height: int = 1000,
    scale: float = 1.0,
) -> bytes:
    if image_format not in {
        "png",
        "svg",
        "pdf",
    }:
        raise ValueError(
            "image_format must be png, svg, or pdf."
        )

    if width < 400 or width > 4000:
        raise ValueError(
            "width must be between 400 and 4000."
        )

    if height < 300 or height > 4000:
        raise ValueError(
            "height must be between 300 and 4000."
        )

    if scale <= 0 or scale > 4:
        raise ValueError(
            "scale must be greater than 0 and at most 4."
        )

    image = pio.to_image(
        figure,
        format=image_format,
        width=width,
        height=height,
        scale=scale,
    )

    if not image:
        raise RuntimeError(
            "Kaleido returned an empty image."
        )

    return image


def render_team_tiers_image(
    dataframe: pd.DataFrame,
    options: TeamTiersChartOptions,
    *,
    image_format: ImageFormat = "png",
    width: int = 1600,
    height: int = 1000,
    scale: float = 1.0,
) -> bytes:
    figure = build_team_tiers_figure(
        dataframe=dataframe,
        options=options,
    )

    return render_figure_bytes(
        figure,
        image_format=image_format,
        width=width,
        height=height,
        scale=scale,
    )
