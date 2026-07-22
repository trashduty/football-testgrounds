from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Literal

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio


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


def _format_list(values: list[int], prefix: str) -> str:
    """
    Format selected integer filters for the chart subtitle.
    """
    sorted_values = sorted(set(values))

    if prefix == "Downs" and sorted_values == [1, 2, 3, 4]:
        return "All downs"

    if prefix == "Quarters" and sorted_values == [1, 2, 3, 4]:
        return "Regulation"

    joined = ", ".join(str(value) for value in sorted_values)
    return f"{prefix}: {joined}"


def _build_subtitle(options: TeamTiersChartOptions) -> str:
    """Build a readable description of all active chart filters."""
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
        _format_list(options.downs, "Downs"),
        _format_list(options.periods, "Quarters"),
        (
            "Competitive plays only"
            if options.exclude_garbage_time
            else "Includes extreme win-probability plays"
        ),
        f"Minimum {options.minimum_plays} plays per unit",
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


def _choose_label_positions(
    dataframe: pd.DataFrame,
) -> list[str]:
    """
    Alternate label positions to reduce direct overlap.

    This is intentionally simple for the first version. Later, team logos or
    selective labels can replace full-text labels.
    """
    positions = [
        "top center",
        "bottom center",
        "middle right",
        "middle left",
        "top right",
        "bottom left",
        "top left",
        "bottom right",
    ]

    return [
        positions[index % len(positions)]
        for index in range(len(dataframe))
    ]


def build_team_tiers_figure(
    dataframe: pd.DataFrame,
    options: TeamTiersChartOptions,
) -> go.Figure:
    """
    Create the Plotly team-tiers scatterplot.

    Lower defensive EPA allowed is better. The y-axis is reversed so that
    stronger defenses appear higher on the graphic.
    """
    required_columns = {
        "team",
        "off_epa_per_play",
        "def_epa_allowed_per_play",
        "offensive_plays",
        "defensive_plays",
    }

    missing_columns = required_columns.difference(
        dataframe.columns
    )

    if missing_columns:
        raise ValueError(
            "Chart data is missing required columns: "
            + ", ".join(sorted(missing_columns))
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

    offense_average = float(
        chart_data["off_epa_per_play"].mean()
    )

    defense_average = float(
        chart_data["def_epa_allowed_per_play"].mean()
    )

    chart_data["label_position"] = _choose_label_positions(
        chart_data
    )

    chart_data["hover_text"] = chart_data.apply(
        lambda row: (
            f"<b>{row['team']}</b><br>"
            f"Conference: {row.get('conference') or 'Unknown'}<br>"
            f"Offensive EPA/play: "
            f"{row['off_epa_per_play']:.3f}<br>"
            f"Defensive EPA allowed/play: "
            f"{row['def_epa_allowed_per_play']:.3f}<br>"
            f"Offensive plays: "
            f"{int(row['offensive_plays']):,}<br>"
            f"Defensive plays: "
            f"{int(row['defensive_plays']):,}"
        ),
        axis=1,
    )

    marker_sizes = (
        (
            chart_data["offensive_plays"]
            + chart_data["defensive_plays"]
        )
        .clip(lower=1)
        .pow(0.5)
        .clip(lower=10, upper=22)
    )

    figure = go.Figure()

    figure.add_trace(
        go.Scatter(
            x=chart_data["off_epa_per_play"],
            y=chart_data["def_epa_allowed_per_play"],
            mode="markers+text",
            text=chart_data["team"],
            textposition=chart_data["label_position"],
            customdata=chart_data["hover_text"],
            hovertemplate="%{customdata}<extra></extra>",
            marker={
                "size": marker_sizes,
                "opacity": 0.82,
                "line": {
                    "width": 1,
                    "color": "rgba(20, 30, 50, 0.55)",
                },
            },
            textfont={
                "size": 10,
            },
            name="Teams",
        )
    )

    # Average lines create the four performance quadrants.
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

    x_min = float(chart_data["off_epa_per_play"].min())
    x_max = float(chart_data["off_epa_per_play"].max())
    y_min = float(
        chart_data["def_epa_allowed_per_play"].min()
    )
    y_max = float(
        chart_data["def_epa_allowed_per_play"].max()
    )

    x_padding = max((x_max - x_min) * 0.10, 0.015)
    y_padding = max((y_max - y_min) * 0.10, 0.015)

    subtitle = _build_subtitle(options)

    figure.update_layout(
        title={
            "text": (
                f"<b>{options.season} CFB Team Tiers</b>"
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
        zerolinecolor="rgba(80, 90, 110, 0.35)",
        gridcolor="rgba(120, 130, 150, 0.16)",
        tickformat=".3f",
        tickfont={
            "size": 12,
        },
    )

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
        # Lower defensive EPA allowed is better, so reverse the y-axis.
        range=[
            y_max + y_padding,
            y_min - y_padding,
        ],
        zeroline=True,
        zerolinewidth=1,
        zerolinecolor="rgba(80, 90, 110, 0.35)",
        gridcolor="rgba(120, 130, 150, 0.16)",
        tickformat=".3f",
        tickfont={
            "size": 12,
        },
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

    figure.add_annotation(
        x=0.99,
        y=0.99,
        xref="paper",
        yref="paper",
        text="Strong offense / strong defense",
        xanchor="right",
        yanchor="top",
        showarrow=False,
        font={
            "size": 12,
            "color": "rgb(70, 80, 100)",
        },
        bgcolor="rgba(255,255,255,0.72)",
        borderpad=5,
    )

    figure.add_annotation(
        x=0.01,
        y=0.99,
        xref="paper",
        yref="paper",
        text="Weak offense / strong defense",
        xanchor="left",
        yanchor="top",
        showarrow=False,
        font={
            "size": 12,
            "color": "rgb(70, 80, 100)",
        },
        bgcolor="rgba(255,255,255,0.72)",
        borderpad=5,
    )

    figure.add_annotation(
        x=0.99,
        y=0.01,
        xref="paper",
        yref="paper",
        text="Strong offense / weak defense",
        xanchor="right",
        yanchor="bottom",
        showarrow=False,
        font={
            "size": 12,
            "color": "rgb(70, 80, 100)",
        },
        bgcolor="rgba(255,255,255,0.72)",
        borderpad=5,
    )

    figure.add_annotation(
        x=0.01,
        y=0.01,
        xref="paper",
        yref="paper",
        text="Weak offense / weak defense",
        xanchor="left",
        yanchor="bottom",
        showarrow=False,
        font={
            "size": 12,
            "color": "rgb(70, 80, 100)",
        },
        bgcolor="rgba(255,255,255,0.72)",
        borderpad=5,
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
    """
    Render a Plotly figure to static image bytes through Kaleido.
    """
    if image_format not in {"png", "svg", "pdf"}:
        raise ValueError(
            "image_format must be png, svg, or pdf."
        )

    if width < 400 or width > 4000:
        raise ValueError("width must be between 400 and 4000.")

    if height < 300 or height > 4000:
        raise ValueError("height must be between 300 and 4000.")

    if scale <= 0 or scale > 4:
        raise ValueError("scale must be greater than 0 and at most 4.")

    image = pio.to_image(
        figure,
        format=image_format,
        width=width,
        height=height,
        scale=scale,
        engine="kaleido",
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
    """Build and render the complete team-tiers graphic."""
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
