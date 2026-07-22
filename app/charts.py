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
    os.environ["BROWSER_PATH"] = str(DEFAULT_CHROME_PATH)


ImageFormat = Literal["png", "svg", "pdf"]


METRIC_CONFIG = {
    "epa": {
        "label": "EPA per Play",
        "offense_column": "off_epa_per_play",
        "defense_column": "def_epa_allowed_per_play",
        "offense_axis": "Offensive EPA per play → better offense",
        "defense_axis": (
            "Better defense ← defensive EPA allowed per play"
        ),
        "hover_offense": "Offensive EPA/play",
        "hover_defense": "Defensive EPA allowed/play",
        "tick_format": ".3f",
        "hover_format": ".3f",
        "percentage": False,
    },
    "success_rate": {
        "label": "Success Rate",
        "offense_column": "off_success_rate",
        "defense_column": "def_success_rate_allowed",
        "offense_axis": "Offensive success rate → better offense",
        "defense_axis": (
            "Better defense ← defensive success rate allowed"
        ),
        "hover_offense": "Offensive success rate",
        "hover_defense": "Defensive success rate allowed",
        "tick_format": ".0%",
        "hover_format": ".1%",
        "percentage": True,
    },
    "yards_per_play": {
        "label": "Yards per Play",
        "offense_column": "off_yards_per_play",
        "defense_column": "def_yards_per_play_allowed",
        "offense_axis": "Offensive yards per play → better offense",
        "defense_axis": (
            "Better defense ← defensive yards per play allowed"
        ),
        "hover_offense": "Offensive yards/play",
        "hover_defense": "Defensive yards/play allowed",
        "tick_format": ".1f",
        "hover_format": ".2f",
        "percentage": False,
    },
    "explosive_rate": {
        "label": "Explosive-Play Rate",
        "offense_column": "off_explosive_rate",
        "defense_column": "def_explosive_rate_allowed",
        "offense_axis": "Offensive explosive-play rate → better offense",
        "defense_axis": (
            "Better defense ← explosive-play rate allowed"
        ),
        "hover_offense": "Offensive explosive-play rate",
        "hover_defense": "Explosive-play rate allowed",
        "tick_format": ".0%",
        "hover_format": ".1%",
        "percentage": True,
    },
}


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

    metric: str = "epa"
    conference: str | None = None
    selected_teams: list[str] | None = None
    logo_size: str = "auto"

    red_zone_only: bool = False
    goal_to_go_only: bool = False
    season_type: str | None = None


def _get_metric_config(metric: str) -> dict[str, object]:
    try:
        return METRIC_CONFIG[metric]
    except KeyError as error:
        raise ValueError(
            f"Unsupported metric: {metric}"
        ) from error


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
    display_count: int,
    benchmark_count: int,
) -> str:
    parts = [
        (
            f"Weeks {options.week_start}–{options.week_end}"
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
            else "Includes garbage time"
        ),
        f"Minimum {options.minimum_plays} plays",
        (
            f"{display_count} displayed / "
            f"{benchmark_count} FBS benchmark teams"
        ),
    ]

    if options.selected_teams:
        parts.append("Selected-team comparison")
    elif options.conference:
        parts.append(f"{options.conference} displayed")

    if options.red_zone_only:
        parts.append("Red zone only")

    if options.goal_to_go_only:
        parts.append("Goal-to-go only")

    return " | ".join(parts)


def _load_logo_map() -> dict[str, Path]:
    if not LOGO_MAP_FILE.exists():
        return {}

    mapping = pd.read_csv(
        LOGO_MAP_FILE,
        dtype=str,
    )

    required = {
        "team",
        "logo_path",
    }

    missing = required.difference(
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
        path_text = str(path_value).strip()

        if not team or not path_text:
            continue

        logo_path = Path(path_text)

        if not logo_path.is_absolute():
            logo_path = BASE_DIR / logo_path

        if logo_path.exists() and logo_path.is_file():
            logo_map[team] = logo_path

    return logo_map


def _image_to_data_uri(
    image_path: Path,
) -> str:
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
    metric: str,
) -> pd.DataFrame:
    config = _get_metric_config(metric)

    offense_column = str(
        config["offense_column"]
    )

    defense_column = str(
        config["defense_column"]
    )

    required = {
        "team",
        "offensive_plays",
        "defensive_plays",
        offense_column,
        defense_column,
    }

    missing = required.difference(
        dataframe.columns
    )

    if missing:
        raise ValueError(
            f"The {config['label']} chart requires missing columns: "
            + ", ".join(sorted(missing))
        )

    chart_data = dataframe.dropna(
        subset=[
            offense_column,
            defense_column,
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


def _get_display_data(
    benchmark_data: pd.DataFrame,
    options: TeamTiersChartOptions,
) -> pd.DataFrame:
    display_data = benchmark_data.copy()

    # Selected teams take precedence over conference filters so
    # cross-conference comparisons remain possible.
    if options.selected_teams:
        selected = {
            team.strip()
            for team in options.selected_teams
            if team.strip()
        }

        display_data = display_data[
            display_data["team"].isin(selected)
        ].copy()

    elif options.conference:
        if "conference" not in display_data.columns:
            raise ValueError(
                "Conference filtering requires a conference column."
            )

        display_data = display_data[
            display_data["conference"]
            == options.conference
        ].copy()

    if display_data.empty:
        raise ValueError(
            "No displayed teams met the selected filters "
            "and minimum-play requirement."
        )

    return display_data


def _logo_multiplier(
    display_count: int,
    requested_size: str,
) -> float:
    manual_sizes = {
        "standard": 1.0,
        "large": 1.5,
        "extra_large": 2.0,
    }

    if requested_size in manual_sizes:
        return manual_sizes[requested_size]

    if display_count <= 12:
        return 2.0

    if display_count <= 30:
        return 1.5

    return 1.0


def _calculate_logo_sizes(
    benchmark_data: pd.DataFrame,
    offense_column: str,
    defense_column: str,
    display_count: int,
    requested_size: str,
) -> tuple[float, float]:
    x_range = max(
        float(benchmark_data[offense_column].max())
        - float(benchmark_data[offense_column].min()),
        0.01,
    )

    y_range = max(
        float(benchmark_data[defense_column].max())
        - float(benchmark_data[defense_column].min()),
        0.01,
    )

    multiplier = _logo_multiplier(
        display_count,
        requested_size,
    )

    return (
        x_range * 0.038 * multiplier,
        y_range * 0.055 * multiplier,
    )


def _format_hover_value(
    value: float,
    format_string: str,
) -> str:
    return format(
        float(value),
        format_string,
    )


def _build_hover_text(
    row: pd.Series,
    config: dict[str, object],
) -> str:
    offense_column = str(
        config["offense_column"]
    )

    defense_column = str(
        config["defense_column"]
    )

    hover_format = str(
        config["hover_format"]
    )

    conference = row.get("conference")

    if pd.isna(conference) or not str(conference).strip():
        conference = "Unknown"

    offense_value = _format_hover_value(
        float(row[offense_column]),
        hover_format,
    )

    defense_value = _format_hover_value(
        float(row[defense_column]),
        hover_format,
    )

    return (
        f"<b>{row['team']}</b><br>"
        f"Conference: {conference}<br>"
        f"{config['hover_offense']}: {offense_value}<br>"
        f"{config['hover_defense']}: {defense_value}<br>"
        f"Offensive plays: {int(row['offensive_plays']):,}<br>"
        f"Defensive plays: {int(row['defensive_plays']):,}"
    )


def build_team_tiers_figure(
    dataframe: pd.DataFrame,
    options: TeamTiersChartOptions,
) -> go.Figure:
    config = _get_metric_config(
        options.metric
    )

    offense_column = str(
        config["offense_column"]
    )

    defense_column = str(
        config["defense_column"]
    )

    # This is always the complete qualifying FBS benchmark.
    benchmark_data = _prepare_chart_data(
        dataframe,
        options.metric,
    )

    # Conference and selected-team filters affect only displayed logos.
    display_data = _get_display_data(
        benchmark_data,
        options,
    )

    logo_map = _load_logo_map()

    offense_average = float(
        benchmark_data[offense_column].mean()
    )

    defense_average = float(
        benchmark_data[defense_column].mean()
    )

    x_min = float(
        benchmark_data[offense_column].min()
    )

    x_max = float(
        benchmark_data[offense_column].max()
    )

    y_min = float(
        benchmark_data[defense_column].min()
    )

    y_max = float(
        benchmark_data[defense_column].max()
    )

    x_padding = max(
        (x_max - x_min) * 0.10,
        0.015,
    )

    y_padding = max(
        (y_max - y_min) * 0.10,
        0.015,
    )

    logo_width, logo_height = _calculate_logo_sizes(
        benchmark_data,
        offense_column,
        defense_column,
        len(display_data),
        options.logo_size,
    )

    display_data["hover_text"] = display_data.apply(
        lambda row: _build_hover_text(
            row,
            config,
        ),
        axis=1,
    )

    figure = go.Figure()

    # Transparent scatter points preserve hover behavior.
    figure.add_trace(
        go.Scatter(
            x=display_data[offense_column],
            y=display_data[defense_column],
            mode="markers",
            customdata=display_data["hover_text"],
            hovertemplate=(
                "%{customdata}<extra></extra>"
            ),
            marker={
                "size": 55,
                "opacity": 0.001,
            },
            showlegend=False,
        )
    )

    missing_logo_teams: list[str] = []

    for _, row in display_data.iterrows():
        team = str(row["team"]).strip()

        logo_path = logo_map.get(team)

        if logo_path is None:
            missing_logo_teams.append(team)

            figure.add_trace(
                go.Scatter(
                    x=[
                        float(row[offense_column])
                    ],
                    y=[
                        float(row[defense_column])
                    ],
                    mode="markers+text",
                    text=[team],
                    textposition="top center",
                    marker={
                        "size": 22,
                        "opacity": 0.85,
                    },
                    textfont={
                        "size": 16,
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
                "x": float(row[offense_column]),
                "y": float(row[defense_column]),
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
        line_width=2.5,
        line_dash="dash",
        line_color="rgba(45, 55, 75, 0.75)",
    )

    figure.add_hline(
        y=defense_average,
        line_width=2.5,
        line_dash="dash",
        line_color="rgba(45, 55, 75, 0.75)",
    )

    subtitle = _build_subtitle(
        options,
        len(display_data),
        len(benchmark_data),
    )

    figure.update_layout(
        title={
            "text": (
                f"<b>{options.season} CFB "
                f"{config['label']} Team Tiers</b>"
                f"<br><sup>{subtitle}</sup>"
            ),
            "x": 0.03,
            "xanchor": "left",
            "y": 0.97,
            "yanchor": "top",
            "font": {
                "size": 38,
            },
        },
        width=1600,
        height=1000,
        margin={
            "l": 145,
            "r": 95,
            "t": 165,
            "b": 135,
        },
        paper_bgcolor="white",
        plot_bgcolor="rgb(248, 250, 253)",
        font={
            "family": (
                "Arial, Helvetica, sans-serif"
            ),
            "color": "rgb(25, 35, 55)",
            "size": 17,
        },
        showlegend=False,
        hoverlabel={
            "bgcolor": "white",
            "font_size": 17,
            "font_family": "Arial",
        },
    )

    figure.update_xaxes(
        title={
            "text": str(config["offense_axis"]),
            "font": {
                "size": 24,
            },
        },
        range=[
            x_min - x_padding,
            x_max + x_padding,
        ],
        zeroline=True,
        zerolinewidth=1.5,
        zerolinecolor=(
            "rgba(80, 90, 110, 0.35)"
        ),
        gridcolor=(
            "rgba(120, 130, 150, 0.16)"
        ),
        tickformat=str(config["tick_format"]),
        tickfont={
            "size": 17,
        },
    )

    # Lower defensive results are better for every supported metric.
    figure.update_yaxes(
        title={
            "text": str(config["defense_axis"]),
            "font": {
                "size": 24,
            },
        },
        range=[
            y_max + y_padding,
            y_min - y_padding,
        ],
        zeroline=True,
        zerolinewidth=1.5,
        zerolinecolor=(
            "rgba(80, 90, 110, 0.35)"
        ),
        gridcolor=(
            "rgba(120, 130, 150, 0.16)"
        ),
        tickformat=str(config["tick_format"]),
        tickfont={
            "size": 17,
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
                "size": 17,
                "color": "rgb(70, 80, 100)",
            },
            bgcolor="rgba(255,255,255,0.78)",
            borderpad=7,
        )

    figure.add_annotation(
        x=0.5,
        y=-0.16,
        xref="paper",
        yref="paper",
        text=(
            "Axes and dashed averages use every qualifying "
            "FBS team, even when only selected teams are displayed."
        ),
        showarrow=False,
        font={
            "size": 16,
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
