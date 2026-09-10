#!/usr/bin/env python3
"""
Generate X-ready social assets for BTB Analytics CFB matchup articles.

Outputs:
1. Model-vs-market hero graphic
2. Matchup stats graphic
3. Ready-to-paste X caption

Designed for 1200x675 landscape images.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Dict, Optional

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
    "https://www.btb-analytics.com/member-access"
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
        return float(value)

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
        return f"+{value:g}"

    return f"{value:g}"


def _format_price(
    value,
) -> str:

    value = _safe_float(
        value
    )

    if value is None:
        return ""

    if value > 0:
        return f"+{value:.0f}"

    return f"{value:.0f}"


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

    return f"{value:.{digits}f}%"


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
            f"Social graphic image download failed "
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
        (0, 0),
        text,
        font=font,
    )

    return (
        box[2]
        - box[0]
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
            (WIDTH - width) / 2,
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


def _draw_btb_brand(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    session: requests.Session,
) -> None:
    """
    Draw compact BTB branding in the upper-right corner.

    Keeping the branding at the top prevents it from interfering
    with footnotes/captions at the bottom of social graphics.
    """

    logo = _download_image(
        BTB_LOGO_URL,
        session,
    )

    # Compact logo in upper-right
    if logo is not None:

        logo = _fit_image(
            logo,
            76,
            52,
        )

        logo_x = (
            WIDTH
            - logo.width
            - 42
        )

        logo_y = 28

        canvas.alpha_composite(
            logo,
            (
                logo_x,
                logo_y,
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

    # ---------------------------------------------------------
    # Header
    # ---------------------------------------------------------

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

    # ---------------------------------------------------------
    # Team logos
    # ---------------------------------------------------------

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

    # ---------------------------------------------------------
    # Main comparison cards
    # ---------------------------------------------------------

    card_y1 = 185
    card_y2 = 365

    _draw_round_rect(
        draw,
        (
            45,
            card_y1,
            575,
            card_y2,
        ),
    )

    _draw_round_rect(
        draw,
        (
            625,
            card_y1,
            1155,
            card_y2,
        ),
    )

    draw.text(
        (
            75,
            210,
        ),
        "MARKET",
        font=_font(
            20,
            bold=True,
        ),
        fill=MUTED,
    )

    draw.text(
        (
            655,
            210,
        ),
        "OUR MODEL",
        font=_font(
            20,
            bold=True,
        ),
        fill=BTB_GREEN,
    )

    market_text = (
        f"{bet_short} "
        f"{_format_line(market_line)}"
    )

    model_text = (
        f"{bet_short} "
        f"{_format_line(model_prediction)}"
    )

    draw.text(
        (
            75,
            255,
        ),
        market_text,
        font=_font(
            43,
            bold=True,
        ),
        fill=WHITE,
    )

    draw.text(
        (
            655,
            255,
        ),
        model_text,
        font=_font(
            43,
            bold=True,
        ),
        fill=BTB_GREEN,
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

    # ---------------------------------------------------------
    # Metrics
    # ---------------------------------------------------------

    metric_y1 = 435
    metric_y2 = 555

    metric_width = 260
    gap_width = 25

    starts = [
        45,
        45 + metric_width + gap_width,
        45 + (
            metric_width
            + gap_width
        ) * 2,
        45 + (
            metric_width
            + gap_width
        ) * 3,
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

        if labels[index] == "OUR CALL":

            value_color = (
                BTB_GREEN
                if has_bet
                else BET_PINK
            )

        else:

            value_color = WHITE

        draw.text(
            (
                x + 18,
                metric_y1 + 50,
            ),
            values[index],
            font=_font(
                27,
                bold=True,
            ),
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
        ids == int(team_id)
    ]

    if row.empty:
        return None

    value = row.iloc[0].get(
        column
    )

    if (
        value is None
        or pd.isna(value)
    ):
        return None

    return int(value)


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

    left_x = 45
    stat_x = 405
    right_x = 930

    header_y = 125

    # ---------------------------------------------------------
    # Logos
    # ---------------------------------------------------------

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

    bet_name_width = _text_width(
        draw,
        bet_short,
        _font(
            22,
            bold=True,
        ),
    )

    draw.text(
        (
            185 - bet_name_width / 2,
            178,
        ),
        bet_short,
        font=_font(
            22,
            bold=True,
        ),
        fill=WHITE,
    )

    opp_name_width = _text_width(
        draw,
        opp_short,
        _font(
            22,
            bold=True,
        ),
    )

    draw.text(
        (
            1045 - opp_name_width / 2,
            178,
        ),
        opp_short,
        font=_font(
            22,
            bold=True,
        ),
        fill=WHITE,
    )

    # ---------------------------------------------------------
    # Rows
    # ---------------------------------------------------------

    row_top = 225
    row_height = 56

    for index, (
        label,
        column,
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
                or bet_rank < opp_rank
            )
        )

        opp_better = (
            opp_rank is not None
            and (
                bet_rank is None
                or opp_rank < bet_rank
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
                185 - bet_width / 2,
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
                1045 - opp_width / 2,
                y1 + 13,
            ),
            opp_text,
            font=opp_font,
            fill=opp_color,
        )

    draw.text(
        (
            45,
            578,
        ),
        (
            "Lower rank is better. "
            "Green highlights the stronger side "
            "in each category."
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

    model_text = (
        _format_line(
            model_prediction
        )
    )

    market_text = (
        _format_line(
            market_line
        )
    )

    best_text = (
        _format_line(
            best_line
        )
    )

    price_text = (
        _format_price(
            best_price
        )
    )

    cover_text = (
        _format_percent(
            cover_probability
        )
    )

    edge_text = (
        _format_edge(
            edge
        )
    )

    model_float = (
        _safe_float(
            model_prediction
        )
    )

    market_float = (
        _safe_float(
            market_line
        )
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
            f"Our model is {gap:g} points away "
            f"from the market on "
            f"{away_short}-{home_short}."
        )

    else:

        opening = (
            f"Our model disagrees with the market "
            f"on {away_short}-{home_short}."
        )

    if has_bet:

        middle = (
            f"\n\nWe make {bet_short} {model_text}. "
            f"The market is {market_text}."
            f"\n\nBest number: "
            f"{bet_short} {best_text} "
            f"{price_text}. "
            f"We give it a {cover_text} chance "
            f"to cover with a {edge_text} edge."
            f"\n\nWe broke down what is driving "
            f"the difference — including the "
            f"matchup that concerns us most."
        )

    else:

        middle = (
            f"\n\nWe make {bet_short} {model_text}. "
            f"The market is {market_text}."
            f"\n\nWe see some disagreement, but "
            f"the available price does not clear "
            f"our threshold, so we are passing."
            f"\n\nWe broke down why our model "
            f"differs and what would matter most "
            f"in the matchup."
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
# Master function
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

    return {
        "x_model_graphic":
            model_path.name,

        "x_stats_graphic":
            stats_path.name,

        "x_caption":
            caption_path.name,
    }
