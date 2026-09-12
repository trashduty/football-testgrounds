#!/usr/bin/env python3
"""
Generate social and SEO assets for BTB Analytics CFB matchup articles.

Outputs per matchup:
1. X model-vs-market hero graphic
2. X matchup-stats graphic
3. Ready-to-paste X caption
4. Fact-grounded SEO title + description

Designed for 1200x675 landscape images.
"""

from __future__ import annotations

import io

from pathlib import Path
from typing import (
    Dict,
    Optional,
)

import pandas as pd
import requests

from PIL import (
    Image,
    ImageDraw,
    ImageFont,
)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

WIDTH = 1200
HEIGHT = 675

BG = "#050505"
CARD = "#111111"
CARD_ALT = "#171717"
BORDER = "#2A2A2A"

WHITE = "#F7F7F7"
MUTED = "#B5B5B5"

BTB_GREEN = "#27E26F"
BET_PINK = "#FF7CB8"
YELLOW = "#FFD24D"

REQUEST_TIMEOUT = 20

BTB_LOGO_URL = (
    "https://raw.githubusercontent.com/"
    "trashduty/football-testgrounds/main/"
    "BTB%20Analytics%20.png.png"
)

MEMBER_URL = (
    "https://www.btb-analytics.com/"
    "member-access"
)


# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #

FONT_REGULAR_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]

FONT_BOLD_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]


def _font(
    size: int,
    bold: bool = False,
) -> ImageFont.FreeTypeFont:

    candidates = (
        FONT_BOLD_PATHS
        if bold
        else FONT_REGULAR_PATHS
    )

    for path in candidates:

        if Path(path).exists():

            return ImageFont.truetype(
                path,
                size=size,
            )

    return ImageFont.load_default()


# --------------------------------------------------------------------------- #
# General helpers
# --------------------------------------------------------------------------- #

def _safe_float(
    value,
) -> Optional[float]:

    if (
        value is None
        or pd.isna(value)
    ):
        return None

    try:

        return float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):

        return None


def _format_line(
    value,
) -> str:

    value = _safe_float(
        value
    )

    if value is None:
        return "N/A"

    if value > 0:

        return (
            f"+{value:g}"
        )

    return (
        f"{value:g}"
    )


def _format_price(
    value,
) -> str:

    value = _safe_float(
        value
    )

    if value is None:
        return ""

    if value > 0:

        return (
            f"+{value:.0f}"
        )

    return (
        f"{value:.0f}"
    )


def _format_percent(
    value,
    digits: int = 1,
) -> str:

    value = _safe_float(
        value
    )

    if value is None:
        return "N/A"

    if abs(value) <= 1:

        value *= 100

    return (
        f"{value:.{digits}f}%"
    )


def _format_edge(
    value,
) -> str:

    return _format_percent(
        value,
        digits=1,
    )


def _download_image(
    url: Optional[str],
    session: requests.Session,
) -> Optional[Image.Image]:

    if not url:
        return None

    try:

        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        image = Image.open(
            io.BytesIO(
                response.content
            )
        )

        return image.convert(
            "RGBA"
        )

    except Exception as exc:

        print(
            "Social graphic image download failed "
            f"for {url}: {exc}"
        )

        return None


def _fit_image(
    image: Image.Image,
    max_width: int,
    max_height: int,
) -> Image.Image:

    image = image.copy()

    image.thumbnail(
        (
            max_width,
            max_height,
        ),
        Image.LANCZOS,
    )

    return image


def _paste_centered(
    canvas: Image.Image,
    image: Image.Image,
    center_x: int,
    center_y: int,
) -> None:

    x = int(
        center_x
        - image.width / 2
    )

    y = int(
        center_y
        - image.height / 2
    )

    canvas.alpha_composite(
        image,
        (
            x,
            y,
        ),
    )


def _text_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
) -> float:

    box = draw.textbbox(
        (
            0,
            0,
        ),
        text,
        font=font,
    )

    return (
        box[2]
        - box[0]
    )


def _text_height(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
) -> float:

    box = draw.textbbox(
        (
            0,
            0,
        ),
        text,
        font=font,
    )

    return (
        box[3]
        - box[1]
    )


def _center_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font,
    fill: str,
) -> None:

    width = _text_width(
        draw,
        text,
        font,
    )

    draw.text(
        (
            (
                WIDTH
                - width
            )
            / 2,
            y,
        ),
        text,
        font=font,
        fill=fill,
    )


def _draw_round_rect(
    draw: ImageDraw.ImageDraw,
    xy,
    radius: int = 18,
    fill: str = CARD,
    outline: str = BORDER,
    width: int = 2,
) -> None:

    draw.rounded_rectangle(
        xy,
        radius=radius,
        fill=fill,
        outline=outline,
        width=width,
    )


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_width: int,
) -> list[str]:

    words = text.split()

    if not words:
        return []

    lines = []
    current = words[0]

    for word in words[1:]:

        candidate = (
            f"{current} {word}"
        )

        if (
            _text_width(
                draw,
                candidate,
                font,
            )
            <= max_width
        ):

            current = candidate

        else:

            lines.append(
                current
            )

            current = word

    lines.append(
        current
    )

    return lines


def _truncate_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_width: int,
) -> str:

    if (
        _text_width(
            draw,
            text,
            font,
        )
        <= max_width
    ):
        return text

    ellipsis = "..."

    trimmed = text.strip()

    while trimmed:

        candidate = (
            trimmed.rstrip()
            + ellipsis
        )

        if (
            _text_width(
                draw,
                candidate,
                font,
            )
            <= max_width
        ):
            return candidate

        trimmed = trimmed[:-1]

    return ellipsis


def _fit_font_single_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    start_size: int,
    min_size: int,
    bold: bool = False,
):

    for size in range(
        start_size,
        min_size - 1,
        -1,
    ):

        font = _font(
            size,
            bold=bold,
        )

        if (
            _text_width(
                draw,
                text,
                font,
            )
            <= max_width
        ):
            return font

    return _font(
        min_size,
        bold=bold,
    )


def _fit_wrapped_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
    start_size: int,
    min_size: int,
    bold: bool = False,
    max_lines: int = 2,
):

    for size in range(
        start_size,
        min_size - 1,
        -1,
    ):

        font = _font(
            size,
            bold=bold,
        )

        lines = _wrap_text(
            draw,
            text,
            font,
            max_width,
        )

        if not lines:
            lines = [""]

        line_height = _text_height(
            draw,
            "Ag",
            font,
        )

        gap = 5

        total_height = (
            len(lines) * line_height
            + max(
                0,
                len(lines) - 1,
            )
            * gap
        )

        if (
            len(lines)
            <= max_lines
            and total_height
            <= max_height
        ):

            return (
                font,
                lines,
                line_height,
                gap,
            )

    font = _font(
        min_size,
        bold=bold,
    )

    lines = _wrap_text(
        draw,
        text,
        font,
        max_width,
    )

    if not lines:
        lines = [""]

    if len(lines) > max_lines:

        kept = lines[
            : max_lines - 1
        ]

        overflow = " ".join(
            lines[max_lines - 1 :]
        )

        kept.append(
            _truncate_text(
                draw,
                overflow,
                font,
                max_width,
            )
        )

        lines = kept

    line_height = _text_height(
        draw,
        "Ag",
        font,
    )

    gap = 5

    return (
        font,
        lines,
        line_height,
        gap,
    )


def _draw_model_side_card(
    draw: ImageDraw.ImageDraw,
    *,
    box,
    heading: str,
    team_name: str,
    spread_text: str,
    heading_fill: str,
    value_fill: str,
) -> None:
    """
    Draw one of the two large cards on the x_model graphic.

    Fixes overflow by:
    - wrapping / shrinking the team name
    - drawing the spread on its own line
    """

    x1, y1, x2, y2 = box

    padding_x = 30

    draw.text(
        (
            x1 + padding_x,
            y1 + 27,
        ),
        heading,
        font=_font(
            20,
            bold=True,
        ),
        fill=heading_fill,
    )

    content_width = (
        x2 - x1 - (padding_x * 2)
    )

    team_font, team_lines, team_line_height, team_gap = _fit_wrapped_block(
        draw,
        team_name,
        max_width=content_width,
        max_height=72,
        start_size=34,
        min_size=20,
        bold=True,
        max_lines=2,
    )

    start_y = y1 + 78
    current_y = start_y

    for line in team_lines:

        draw.text(
            (
                x1 + padding_x,
                current_y,
            ),
            line,
            font=team_font,
            fill=value_fill,
        )

        current_y += (
            team_line_height
            + team_gap
        )

    spread_font = _fit_font_single_line(
        draw,
        spread_text,
        max_width=content_width,
        start_size=48,
        min_size=28,
        bold=True,
    )

    draw.text(
        (
            x1 + padding_x,
            current_y + 3,
        ),
        spread_text,
        font=spread_font,
        fill=value_fill,
    )


# --------------------------------------------------------------------------- #
# BTB logo helper
# --------------------------------------------------------------------------- #

def _draw_btb_brand(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    session: requests.Session,
    placement: str = "bottom-right",
) -> None:
    """
    Draw compact BTB branding.

    Supported placements:
    - top-right
    - bottom-right
    - bottom-center
    """

    logo = _download_image(
        BTB_LOGO_URL,
        session,
    )

    if logo is None:
        return

    logo = _fit_image(
        logo,
        90,
        55,
    )

    if placement == "top-right":

        x = (
            WIDTH
            - logo.width
            - 35
        )

        y = 24

    elif placement == "bottom-center":

        x = int(
            (
                WIDTH
                - logo.width
            )
            / 2
        )

        y = (
            HEIGHT
            - logo.height
            - 14
        )

    else:

        x = (
            WIDTH
            - logo.width
            - 35
        )

        y = (
            HEIGHT
            - logo.height
            - 20
        )

    canvas.alpha_composite(
        logo,
        (
            x,
            y,
        ),
    )


# --------------------------------------------------------------------------- #
# Graphic 1 — Model vs Market
# --------------------------------------------------------------------------- #

def build_model_graphic(
    *,
    output_path: Path,
    away_short: str,
    home_short: str,
    away_logo: Optional[str],
    home_logo: Optional[str],
    bet_short: str,
    model_prediction,
    market_line,
    best_line,
    best_price,
    cover_probability,
    edge,
    best_book: Optional[str],
    has_bet: bool,
) -> None:

    canvas = Image.new(
        "RGBA",
        (
            WIDTH,
            HEIGHT,
        ),
        BG,
    )

    draw = ImageDraw.Draw(
        canvas
    )

    session = requests.Session()

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    draw.text(
        (
            45,
            34,
        ),
        "COLLEGE FOOTBALL MODEL",
        font=_font(
            19,
            bold=True,
        ),
        fill=BTB_GREEN,
    )

    matchup = (
        f"{away_short} @ {home_short}"
    )

    matchup_font = _font(
        46,
        bold=True,
    )

    lines = _wrap_text(
        draw,
        matchup,
        matchup_font,
        800,
    )

    y = 66

    for line in lines:

        draw.text(
            (
                45,
                y,
            ),
            line,
            font=matchup_font,
            fill=WHITE,
        )

        y += 54

    # ------------------------------------------------------------------
    # Team logos
    # ------------------------------------------------------------------

    away_img = _download_image(
        away_logo,
        session,
    )

    home_img = _download_image(
        home_logo,
        session,
    )

    if away_img is not None:

        away_img = _fit_image(
            away_img,
            120,
            120,
        )

        _paste_centered(
            canvas,
            away_img,
            940,
            90,
        )

    if home_img is not None:

        home_img = _fit_image(
            home_img,
            120,
            120,
        )

        _paste_centered(
            canvas,
            home_img,
            1070,
            90,
        )

    # ------------------------------------------------------------------
    # Model vs market cards
    # ------------------------------------------------------------------

    left_box = (
        45,
        185,
        575,
        365,
    )

    right_box = (
        625,
        185,
        1155,
        365,
    )

    _draw_round_rect(
        draw,
        left_box,
    )

    _draw_round_rect(
        draw,
        right_box,
    )

    _draw_model_side_card(
        draw,
        box=left_box,
        heading="MARKET",
        team_name=bet_short,
        spread_text=_format_line(
            market_line
        ),
        heading_fill=MUTED,
        value_fill=WHITE,
    )

    _draw_model_side_card(
        draw,
        box=right_box,
        heading="OUR MODEL",
        team_name=bet_short,
        spread_text=_format_line(
            model_prediction
        ),
        heading_fill=BTB_GREEN,
        value_fill=BTB_GREEN,
    )

    model_float = _safe_float(
        model_prediction
    )

    market_float = _safe_float(
        market_line
    )

    if (
        model_float is not None
        and market_float is not None
    ):

        gap = abs(
            model_float
            - market_float
        )

        gap_text = (
            f"{gap:g}-POINT "
            f"MODEL / MARKET GAP"
        )

        _center_text(
            draw,
            gap_text,
            392,
            _font(
                22,
                bold=True,
            ),
            WHITE,
        )

    # ------------------------------------------------------------------
    # Metric cards
    # ------------------------------------------------------------------

    metric_y1 = 435
    metric_y2 = 555

    metric_width = 260
    gap_width = 25

    starts = [
        45,
        45
        + metric_width
        + gap_width,
        45
        + (
            metric_width
            + gap_width
        )
        * 2,
        45
        + (
            metric_width
            + gap_width
        )
        * 3,
    ]

    labels = [
        "BEST NUMBER",
        "COVER PROB.",
        "EDGE",
        "OUR CALL",
    ]

    best_number = (
        f"{_format_line(best_line)} "
        f"{_format_price(best_price)}"
    ).strip()

    call = (
        "BET"
        if has_bet
        else "NO BET"
    )

    values = [
        best_number,
        _format_percent(
            cover_probability
        ),
        _format_edge(
            edge
        ),
        call,
    ]

    for index, x in enumerate(
        starts
    ):

        _draw_round_rect(
            draw,
            (
                x,
                metric_y1,
                x + metric_width,
                metric_y2,
            ),
            radius=14,
            fill=CARD_ALT,
        )

        draw.text(
            (
                x + 18,
                metric_y1 + 17,
            ),
            labels[index],
            font=_font(
                14,
                bold=True,
            ),
            fill=MUTED,
        )

        if (
            labels[index]
            == "OUR CALL"
        ):

            value_color = (
                BTB_GREEN
                if has_bet
                else BET_PINK
            )

        else:

            value_color = WHITE

        value_font = _fit_font_single_line(
            draw,
            values[index],
            max_width=metric_width - 36,
            start_size=27,
            min_size=20,
            bold=True,
        )

        draw.text(
            (
                x + 18,
                metric_y1 + 50,
            ),
            values[index],
            font=value_font,
            fill=value_color,
        )

    if best_book:

        draw.text(
            (
                64,
                569,
            ),
            f"Best available at {best_book}",
            font=_font(
                15,
            ),
            fill=MUTED,
        )

    _draw_btb_brand(
        canvas,
        draw,
        session,
        placement="bottom-right",
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    canvas.convert(
        "RGB"
    ).save(
        output_path,
        "PNG",
        optimize=True,
    )


# --------------------------------------------------------------------------- #
# Graphic 2 — Matchup Stats
# --------------------------------------------------------------------------- #

def _rank(
    ranked_stats: pd.DataFrame,
    team_id: int,
    column: str,
) -> Optional[int]:

    ids = pd.to_numeric(
        ranked_stats[
            "team_id"
        ],
        errors="coerce",
    )

    row = ranked_stats[
        ids
        == int(team_id)
    ]

    if row.empty:
        return None

    value = (
        row.iloc[0]
        .get(
            column
        )
    )

    if (
        value is None
        or pd.isna(value)
    ):
        return None

    return int(
        value
    )


def _rank_text(
    value: Optional[int],
) -> str:

    if value is None:
        return "N/A"

    suffix = "th"

    if (
        value % 100
        not in (
            11,
            12,
            13,
        )
    ):

        if value % 10 == 1:
            suffix = "st"

        elif value % 10 == 2:
            suffix = "nd"

        elif value % 10 == 3:
            suffix = "rd"

    return (
        f"{value}{suffix}"
    )


def build_stats_graphic(
    *,
    output_path: Path,
    bet_id: int,
    opp_id: int,
    bet_short: str,
    opp_short: str,
    bet_logo: Optional[str],
    opp_logo: Optional[str],
    ranked_stats: pd.DataFrame,
) -> None:

    canvas = Image.new(
        "RGBA",
        (
            WIDTH,
            HEIGHT,
        ),
        BG,
    )

    draw = ImageDraw.Draw(
        canvas
    )

    session = requests.Session()

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    draw.text(
        (
            45,
            30,
        ),
        "WHAT THE NUMBERS SAY",
        font=_font(
            18,
            bold=True,
        ),
        fill=BTB_GREEN,
    )

    draw.text(
        (
            45,
            61,
        ),
        "MATCHUP COMPARISON",
        font=_font(
            38,
            bold=True,
        ),
        fill=WHITE,
    )

    metrics = [
        (
            "OFFENSIVE PASS EPA",
            "off_pass_epa_rank",
        ),
        (
            "OFFENSIVE RUSH EPA",
            "off_rush_epa_rank",
        ),
        (
            "DEFENSIVE PASS EPA",
            "def_pass_epa_rank",
        ),
        (
            "DEFENSIVE RUSH EPA",
            "def_rush_epa_rank",
        ),
        (
            "OFFENSIVE ECKEL RATE",
            "off_eckel_rank",
        ),
        (
            "DEFENSIVE ECKEL RATE",
            "def_eckel_rank",
        ),
    ]

    stat_x = 405

    # ------------------------------------------------------------------
    # Team logos
    # ------------------------------------------------------------------

    bet_img = _download_image(
        bet_logo,
        session,
    )

    opp_img = _download_image(
        opp_logo,
        session,
    )

    if bet_img is not None:

        bet_img = _fit_image(
            bet_img,
            62,
            62,
        )

        _paste_centered(
            canvas,
            bet_img,
            185,
            145,
        )

    if opp_img is not None:

        opp_img = _fit_image(
            opp_img,
            62,
            62,
        )

        _paste_centered(
            canvas,
            opp_img,
            1045,
            145,
        )

    bet_name_font = _font(
        22,
        bold=True,
    )

    bet_name_width = _text_width(
        draw,
        bet_short,
        bet_name_font,
    )

    draw.text(
        (
            185
            - bet_name_width
            / 2,
            178,
        ),
        bet_short,
        font=bet_name_font,
        fill=WHITE,
    )

    opp_name_font = _font(
        22,
        bold=True,
    )

    opp_name_width = _text_width(
        draw,
        opp_short,
        opp_name_font,
    )

    draw.text(
        (
            1045
            - opp_name_width
            / 2,
            178,
        ),
        opp_short,
        font=opp_name_font,
        fill=WHITE,
    )

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    row_top = 225
    row_height = 56

    for (
        index,
        (
            label,
            column,
        ),
    ) in enumerate(
        metrics
    ):

        y1 = (
            row_top
            + index
            * row_height
        )

        y2 = (
            y1
            + row_height
            - 4
        )

        row_fill = (
            CARD
            if index % 2 == 0
            else CARD_ALT
        )

        draw.rounded_rectangle(
            (
                45,
                y1,
                1155,
                y2,
            ),
            radius=8,
            fill=row_fill,
        )

        bet_rank = _rank(
            ranked_stats,
            bet_id,
            column,
        )

        opp_rank = _rank(
            ranked_stats,
            opp_id,
            column,
        )

        bet_better = (
            bet_rank is not None
            and (
                opp_rank is None
                or bet_rank
                < opp_rank
            )
        )

        opp_better = (
            opp_rank is not None
            and (
                bet_rank is None
                or opp_rank
                < bet_rank
            )
        )

        bet_color = (
            BTB_GREEN
            if bet_better
            else WHITE
        )

        opp_color = (
            BTB_GREEN
            if opp_better
            else WHITE
        )

        bet_font = _font(
            25,
            bold=bet_better,
        )

        opp_font = _font(
            25,
            bold=opp_better,
        )

        stat_font = _font(
            17,
            bold=True,
        )

        bet_text = _rank_text(
            bet_rank
        )

        opp_text = _rank_text(
            opp_rank
        )

        bet_width = _text_width(
            draw,
            bet_text,
            bet_font,
        )

        opp_width = _text_width(
            draw,
            opp_text,
            opp_font,
        )

        draw.text(
            (
                185
                - bet_width
                / 2,
                y1 + 13,
            ),
            bet_text,
            font=bet_font,
            fill=bet_color,
        )

        draw.text(
            (
                stat_x,
                y1 + 16,
            ),
            label,
            font=stat_font,
            fill=WHITE,
        )

        draw.text(
            (
                1045
                - opp_width
                / 2,
                y1 + 13,
            ),
            opp_text,
            font=opp_font,
            fill=opp_color,
        )

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------

    draw.text(
        (
            45,
            578,
        ),
        (
            "Lower rank is better. "
            "Green highlights the stronger "
            "side in each category."
        ),
        font=_font(
            15,
        ),
        fill=MUTED,
    )

    _draw_btb_brand(
        canvas,
        draw,
        session,
        placement="bottom-center",
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    canvas.convert(
        "RGB"
    ).save(
        output_path,
        "PNG",
        optimize=True,
    )


# --------------------------------------------------------------------------- #
# X caption
# --------------------------------------------------------------------------- #

def build_x_caption(
    *,
    away_short: str,
    home_short: str,
    bet_short: str,
    model_prediction,
    market_line,
    best_line,
    best_price,
    cover_probability,
    edge,
    has_bet: bool,
    article_url: Optional[str] = None,
) -> str:

    model_text = _format_line(
        model_prediction
    )

    market_text = _format_line(
        market_line
    )

    best_text = _format_line(
        best_line
    )

    price_text = _format_price(
        best_price
    )

    cover_text = _format_percent(
        cover_probability
    )

    edge_text = _format_edge(
        edge
    )

    model_float = _safe_float(
        model_prediction
    )

    market_float = _safe_float(
        market_line
    )

    if (
        model_float is not None
        and market_float is not None
    ):

        gap = abs(
            model_float
            - market_float
        )

        opening = (
            f"Our model is {gap:g} points "
            f"away from the market on "
            f"{away_short}-{home_short}."
        )

    else:

        opening = (
            "Our model disagrees with "
            f"the market on "
            f"{away_short}-{home_short}."
        )

    if has_bet:

        middle = (
            f"\n\nWe make "
            f"{bet_short} {model_text}. "
            f"The market is {market_text}."
            f"\n\nBest number: "
            f"{bet_short} {best_text} "
            f"{price_text}. "
            f"We give it a {cover_text} "
            f"chance to cover with a "
            f"{edge_text} edge."
            f"\n\nWe broke down what is "
            f"driving the difference — "
            f"including the matchup that "
            f"concerns us most."
        )

    else:

        middle = (
            f"\n\nWe make "
            f"{bet_short} {model_text}. "
            f"The market is {market_text}."
            f"\n\nWe see some disagreement, "
            f"but the available price does "
            f"not clear our threshold, "
            f"so we are passing."
            f"\n\nWe broke down why our "
            f"model differs and what would "
            f"matter most in the matchup."
        )

    if article_url:

        ending = (
            f"\n\nFull breakdown ↓\n"
            f"{article_url}"
        )

    else:

        ending = (
            "\n\nFull breakdown ↓"
        )

    return (
        opening
        + middle
        + ending
    )


# --------------------------------------------------------------------------- #
# SEO metadata
# --------------------------------------------------------------------------- #

def build_seo_metadata(
    *,
    away_short: str,
    home_short: str,
    bet_short: str,
    model_prediction,
    market_line,
    cover_probability,
    has_bet: bool,
) -> Dict[str, str]:
    """
    Build evidence-based SEO title and description.
    """

    model_text = _format_line(
        model_prediction
    )

    market_text = _format_line(
        market_line
    )

    cover_text = _format_percent(
        cover_probability
    )

    seo_title = (
        f"{away_short} vs {home_short} "
        f"Prediction, Model Spread & Analysis"
    )

    if (
        model_prediction is not None
        and market_line is not None
        and cover_probability is not None
    ):

        seo_description = (
            f"Our model makes "
            f"{bet_short} {model_text} "
            f"vs a market line of "
            f"{market_text}. "
            f"See the best available spread, "
            f"{cover_text} cover probability, "
            f"matchup stats, and our full "
            f"{away_short}-{home_short} analysis."
        )

    elif (
        model_prediction is not None
        and market_line is not None
    ):

        seo_description = (
            f"Our model makes "
            f"{bet_short} {model_text} "
            f"vs a market line of "
            f"{market_text}. "
            f"See our full "
            f"{away_short} vs {home_short} "
            f"prediction, matchup stats, "
            f"and model analysis."
        )

    else:

        seo_description = (
            f"See our "
            f"{away_short} vs {home_short} "
            f"college football prediction, "
            f"model view, cover probability, "
            f"matchup stats, and full game analysis."
        )

    return {
        "seo_title":
            seo_title,

        "seo_description":
            seo_description,
    }


# --------------------------------------------------------------------------- #
# Master generator
# --------------------------------------------------------------------------- #

def generate_social_assets(
    *,
    output_dir: Path,
    game_slug: str,
    article_payload: Dict,
    ranked_stats: pd.DataFrame,
    article_url: Optional[str] = None,
) -> Dict[str, str]:

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    away_id = int(
        article_payload[
            "away_id"
        ]
    )

    home_id = int(
        article_payload[
            "home_id"
        ]
    )

    bet_id = int(
        article_payload[
            "bet_id"
        ]
    )

    opp_id = int(
        article_payload[
            "opp_id"
        ]
    )

    away_short = str(
        article_payload[
            "away_short"
        ]
    )

    home_short = str(
        article_payload[
            "home_short"
        ]
    )

    bet_short = str(
        article_payload[
            "bet_short"
        ]
    )

    opp_short = str(
        article_payload[
            "opp_short"
        ]
    )

    away_logo = (
        article_payload.get(
            "away_logo"
        )
    )

    home_logo = (
        article_payload.get(
            "home_logo"
        )
    )

    if bet_id == away_id:

        bet_logo = away_logo
        opp_logo = home_logo

    else:

        bet_logo = home_logo
        opp_logo = away_logo

    model_path = (
        output_dir
        / f"{game_slug}_x_model.png"
    )

    stats_path = (
        output_dir
        / f"{game_slug}_x_stats.png"
    )

    caption_path = (
        output_dir
        / f"{game_slug}_x_caption.txt"
    )

    seo_path = (
        output_dir
        / f"{game_slug}_seo.txt"
    )

    build_model_graphic(
        output_path=model_path,
        away_short=away_short,
        home_short=home_short,
        away_logo=away_logo,
        home_logo=home_logo,
        bet_short=bet_short,
        model_prediction=(
            article_payload.get(
                "model_prediction"
            )
        ),
        market_line=(
            article_payload.get(
                "market_line"
            )
        ),
        best_line=(
            article_payload.get(
                "best_line"
            )
        ),
        best_price=(
            article_payload.get(
                "best_price"
            )
        ),
        cover_probability=(
            article_payload.get(
                "cover_probability"
            )
        ),
        edge=(
            article_payload.get(
                "edge"
            )
        ),
        best_book=(
            article_payload.get(
                "best_book"
            )
        ),
        has_bet=bool(
            article_payload.get(
                "has_bet"
            )
        ),
    )

    build_stats_graphic(
        output_path=stats_path,
        bet_id=bet_id,
        opp_id=opp_id,
        bet_short=bet_short,
        opp_short=opp_short,
        bet_logo=bet_logo,
        opp_logo=opp_logo,
        ranked_stats=ranked_stats,
    )

    caption = build_x_caption(
        away_short=away_short,
        home_short=home_short,
        bet_short=bet_short,
        model_prediction=(
            article_payload.get(
                "model_prediction"
            )
        ),
        market_line=(
            article_payload.get(
                "market_line"
            )
        ),
        best_line=(
            article_payload.get(
                "best_line"
            )
        ),
        best_price=(
            article_payload.get(
                "best_price"
            )
        ),
        cover_probability=(
            article_payload.get(
                "cover_probability"
            )
        ),
        edge=(
            article_payload.get(
                "edge"
            )
        ),
        has_bet=bool(
            article_payload.get(
                "has_bet"
            )
        ),
        article_url=article_url,
    )

    caption_path.write_text(
        caption,
        encoding="utf-8",
    )

    seo = build_seo_metadata(
        away_short=away_short,
        home_short=home_short,
        bet_short=bet_short,
        model_prediction=(
            article_payload.get(
                "model_prediction"
            )
        ),
        market_line=(
            article_payload.get(
                "market_line"
            )
        ),
        cover_probability=(
            article_payload.get(
                "cover_probability"
            )
        ),
        has_bet=bool(
            article_payload.get(
                "has_bet"
            )
        ),
    )

    seo_text = (
        "SEO Title:\n"
        f"{seo['seo_title']}\n\n"
        "SEO Description:\n"
        f"{seo['seo_description']}\n"
    )

    seo_path.write_text(
        seo_text,
        encoding="utf-8",
    )

    return {
        "x_model_graphic":
            model_path.name,

        "x_stats_graphic":
            stats_path.name,

        "x_caption":
            caption_path.name,

        "seo_file":
            seo_path.name,

        "seo_title":
            seo[
                "seo_title"
            ],

        "seo_description":
            seo[
                "seo_description"
            ],
    }
