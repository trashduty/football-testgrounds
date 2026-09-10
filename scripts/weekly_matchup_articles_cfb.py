#!/usr/bin/env python3
"""
Generate weekly College Football matchup articles.

Outputs:
- Markdown article
- Branded Squarespace-ready HTML article
- Source/audit JSON

Sources:
- spreads_odds.csv
- CFBD rolling team stats
- optional team media guides

Betting logic remains deterministic.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os

from datetime import (
    UTC,
    datetime,
)

from io import StringIO
from pathlib import Path
from typing import (
    Dict,
    List,
    Optional,
    Tuple,
)

from zoneinfo import ZoneInfo

import markdown
import pandas as pd
import requests

import cfb_stats
import cfb_game_guides


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

TRASH_SCHEDULE_OWNER = "trashduty"
TRASH_SCHEDULE_REPO = "trash-schedule"
TRASH_SCHEDULE_REF = "main"

TRASH_SCHEDULE_SPREADS_PATH = (
    "CFB_Odds/Data/spreads_odds.csv"
)

TRASH_SCHEDULE_CROSSWALK_PATH = (
    "CFB_Odds/Data/"
    "CFB Teams Full Crosswalk.csv"
)

FULL_BET_THRESHOLD = 0.03

ET = ZoneInfo(
    "America/New_York"
)

REQUEST_TIMEOUT = 30

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
# Args
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--output-dir",
        default=(
            "outputs/"
            "matchup_articles"
        ),
    )

    parser.add_argument(
        "--guide-dir",
        default="game_guides",
    )

    parser.add_argument(
        "--week",
        type=int,
    )

    parser.add_argument(
        "--season",
        type=int,
    )

    parser.add_argument(
        "--teams",
        nargs="*",
    )

    parser.add_argument(
        "--crosswalk",
        default=None,
    )

    parser.add_argument(
        "--trash-schedule-dir",
    )

    parser.add_argument(
        "--trash-schedule-owner",
        default=(
            TRASH_SCHEDULE_OWNER
        ),
    )

    parser.add_argument(
        "--trash-schedule-repo",
        default=(
            TRASH_SCHEDULE_REPO
        ),
    )

    parser.add_argument(
        "--trash-schedule-ref",
        default=(
            TRASH_SCHEDULE_REF
        ),
    )

    parser.add_argument(
        "--disable-guides",
        action="store_true",
    )

    parser.add_argument(
        "--openai-model",
        default=os.getenv(
            "OPENAI_ARTICLE_MODEL",
            "gpt-5.6",
        ),
    )

    return parser.parse_args()


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def safe_mkdir(
    path: Path,
) -> None:

    path.mkdir(
        parents=True,
        exist_ok=True,
    )


def slugify_game(
    game: str,
) -> str:

    return (
        game
        .lower()
        .replace(
            "@",
            "_at_",
        )
        .replace(
            " ",
            "_",
        )
    )


def parse_percent(
    value: object,
) -> float:

    if (
        value is None
        or pd.isna(value)
    ):
        return float(
            "nan"
        )

    if isinstance(
        value,
        str,
    ):

        stripped = (
            value.strip()
        )

        if stripped.endswith(
            "%"
        ):

            return (
                float(
                    stripped.rstrip(
                        "%"
                    )
                )
                / 100.0
            )

        value = stripped

    numeric = float(
        value
    )

    return (
        numeric / 100.0
        if numeric > 1
        else numeric
    )


def display_percent(
    value: object,
    digits: int = 1,
) -> str:

    if (
        value is None
        or pd.isna(value)
    ):
        return "N/A"

    numeric = float(
        value
    )

    if (
        abs(numeric)
        <= 1
    ):
        numeric *= 100

    return (
        f"{numeric:.{digits}f}%"
    )


def format_line(
    value: object,
) -> str:

    if (
        value is None
        or pd.isna(value)
    ):
        return "N/A"

    f = float(
        value
    )

    return (
        f"+{f:.1f}"
        if f > 0
        else f"{f:.1f}"
    )


def format_projection(
    value: object,
) -> str:

    if (
        value is None
        or pd.isna(value)
    ):
        return "N/A"

    f = float(
        value
    )

    output = (
        f"+{f:.1f}"
        if f > 0
        else f"{f:.1f}"
    )

    return (
        output
        .rstrip("0")
        .rstrip(".")
    )


def _price(
    value: object,
) -> str:

    if (
        value is None
        or pd.isna(value)
    ):
        return "-110"

    f = float(
        value
    )

    return (
        f"{f:+.0f}"
        if f > 0
        else f"{f:.0f}"
    )


def safe_float(
    value: object,
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


def resolve_edge_numeric(
    row: pd.Series,
) -> Optional[float]:

    edge = row.get(
        "best_edge"
    )

    if (
        edge is None
        or pd.isna(edge)
    ):

        edge = row.get(
            "edge"
        )

    if (
        edge is None
        or pd.isna(edge)
    ):

        return None

    return float(
        parse_percent(
            edge
        )
    )


def _side_facts(
    row: pd.Series,
) -> Dict[str, object]:

    cover = row.get(
        "best_cover_probability"
    )

    if (
        cover is None
        or pd.isna(cover)
    ):

        cover = row.get(
            "cover_probability"
        )

    edge = row.get(
        "best_edge"
    )

    if (
        edge is None
        or pd.isna(edge)
    ):

        edge = row.get(
            "edge"
        )

    return {
        "cover":
            cover,

        "edge":
            edge,

        "line":
            row.get(
                "best_line"
            ),

        "price":
            row.get(
                "best_price"
            ),
    }


def matchup_call_label(
    edge: Optional[float],
) -> str:

    if (
        edge is None
        or edge < 0.01
    ):

        return "No Bet"

    if (
        edge
        < FULL_BET_THRESHOLD
    ):

        return (
            "Lean – doesn't meet "
            "our edge criteria "
            "to fully bet"
        )

    return "Bet"


def advice_badge_html(
    edge: Optional[float],
) -> str:

    label = matchup_call_label(
        edge
    )

    if label == "Bet":

        badge_class = (
            "btb-advice-pill "
            "btb-advice-bet"
        )

    elif label == "No Bet":

        badge_class = (
            "btb-advice-pill "
            "btb-advice-no-bet"
        )

    else:

        badge_class = (
            "btb-advice-pill "
            "btb-advice-lean"
        )

    return (
        f'<span class="{badge_class}">'
        f"{html.escape(label)}"
        f"</span>"
    )


def model_vs_market_sentence(
    team_short: str,
    model_prediction: Optional[float],
    market_line: Optional[float],
) -> str:

    if (
        model_prediction is not None
        and market_line is not None
    ):

        return (
            f"We make **{team_short} "
            f"{format_projection(model_prediction)}**, "
            f"compared with a market line of "
            f"{format_projection(market_line)}."
        )

    if model_prediction is not None:

        return (
            f"We make **{team_short} "
            f"{format_projection(model_prediction)}**."
        )

    if market_line is not None:

        return (
            f"Our model favors **{team_short}** "
            f"against a market line of "
            f"{format_projection(market_line)}."
        )

    return (
        f"Our model favors "
        f"**{team_short}**."
    )


def format_kickoff_date(
    commence_time: object,
) -> str:

    if (
        commence_time is None
        or pd.isna(
            commence_time
        )
    ):

        return "N/A"

    ts = pd.Timestamp(
        commence_time
    )

    if ts.tzinfo is None:

        ts = (
            ts.tz_localize(
                "UTC"
            )
        )

    return (
        ts
        .tz_convert(
            ET
        )
        .strftime(
            "%m/%d/%Y"
        )
    )


# --------------------------------------------------------------------------- #
# Repository fetch
# --------------------------------------------------------------------------- #

def fetch_text(
    path: str,
    *,
    local_root: Optional[Path],
    owner: str,
    repo: str,
    ref: str,
    session: requests.Session,
) -> str:

    if (
        local_root
        is not None
    ):

        return (
            local_root
            / path
        ).read_text(
            encoding="utf-8"
        )

    raw_url = (
        "https://raw.githubusercontent.com/"
        f"{owner}/{repo}/{ref}/{path}"
    )

    response = session.get(
        raw_url,
        timeout=REQUEST_TIMEOUT,
    )

    if response.ok:
        return response.text

    token = os.getenv(
        "GITHUB_TOKEN"
    )

    if token:

        api_url = (
            "https://api.github.com/"
            "repos/"
            f"{owner}/{repo}/"
            "contents/"
            f"{path}"
            f"?ref={ref}"
        )

        api_response = (
            session.get(
                api_url,
                headers={
                    "Accept":
                        "application/"
                        "vnd.github+json",

                    "Authorization":
                        f"token {token}",
                },
                timeout=REQUEST_TIMEOUT,
            )
        )

        api_response.raise_for_status()

        return (
            base64.b64decode(
                api_response
                .json()[
                    "content"
                ]
            )
            .decode(
                "utf-8"
            )
        )

    response.raise_for_status()

    return response.text


def load_spreads(
    args: argparse.Namespace,
    session: requests.Session,
) -> Tuple[
    pd.DataFrame,
    int,
    int,
]:

    local_root = (
        Path(
            args.trash_schedule_dir
        ).resolve()
        if args.trash_schedule_dir
        else None
    )

    raw = fetch_text(
        TRASH_SCHEDULE_SPREADS_PATH,
        local_root=local_root,
        owner=args.trash_schedule_owner,
        repo=args.trash_schedule_repo,
        ref=args.trash_schedule_ref,
        session=session,
    )

    spreads = pd.read_csv(
        StringIO(raw)
    )

    spreads.columns = (
        spreads.columns
        .str.strip()
        .str.lower()
    )

    spreads[
        "week"
    ] = (
        spreads[
            "week"
        ]
        .astype(int)
    )

    spreads[
        "commence_time"
    ] = pd.to_datetime(
        spreads[
            "commence_time"
        ],
        errors="coerce",
        utc=True,
    )

    spreads[
        "team_id"
    ] = (
        spreads[
            "logo"
        ]
        .map(
            cfb_stats
            .team_id_from_logo
        )
        .astype(
            "Int64"
        )
    )

    week = (
        args.week
        if (
            args.week
            is not None
        )
        else int(
            spreads[
                "week"
            ].max()
        )
    )

    week_spreads = (
        spreads[
            spreads[
                "week"
            ]
            == week
        ]
        .copy()
    )

    if week_spreads.empty:

        raise ValueError(
            f"No spreads rows "
            f"for week {week}"
        )

    season = (
        args.season
        or int(
            week_spreads[
                "commence_time"
            ]
            .dt.year
            .mode()
            .iloc[0]
        )
    )

    return (
        week_spreads,
        week,
        season,
    )


def load_crosswalk(
    args: argparse.Namespace,
    session: requests.Session,
) -> pd.DataFrame:

    if args.crosswalk:

        cw = pd.read_csv(
            args.crosswalk
        )

    else:

        local_root = (
            Path(
                args.trash_schedule_dir
            ).resolve()
            if args.trash_schedule_dir
            else None
        )

        raw = fetch_text(
            TRASH_SCHEDULE_CROSSWALK_PATH,
            local_root=local_root,
            owner=args.trash_schedule_owner,
            repo=args.trash_schedule_repo,
            ref=args.trash_schedule_ref,
            session=session,
        )

        cw = pd.read_csv(
            StringIO(raw)
        )

    cw.columns = (
        cw.columns
        .str.strip()
    )

    if (
        "team_id"
        in cw.columns
    ):

        cw[
            "team_id"
        ] = pd.to_numeric(
            cw[
                "team_id"
            ],
            errors="coerce",
        ).astype(
            "Int64"
        )

    return cw


# --------------------------------------------------------------------------- #
# Our Take
# --------------------------------------------------------------------------- #

def build_our_take_opening(
    *,
    away_name: str,
    home_name: str,
    stadium_name: Optional[str],
    bet_short: str,
    bet_line: str,
    bet_facts: Dict[str, object],
    has_bet: bool,
    model_prediction: Optional[float],
    market_line: Optional[float],
) -> List[str]:

    stadium = (
        stadium_name
        or "the home venue"
    )

    raw_edge = (
        bet_facts.get(
            "edge"
        )
    )

    edge = (
        parse_percent(
            raw_edge
        )
        if (
            raw_edge
            is not None
            and not pd.isna(
                raw_edge
            )
        )
        else 0.0
    )

    edge_pct = (
        f"{edge * 100:.1f}%"
    )

    price = (
        bet_facts.get(
            "price"
        )
    )

    price_str = (
        _price(
            price
        )
        if (
            price is not None
            and not pd.isna(
                price
            )
        )
        else "N/A"
    )

    cover = (
        bet_facts.get(
            "cover"
        )
    )

    cover_str = (
        display_percent(
            cover,
            1,
        )
        if (
            cover is not None
            and not pd.isna(
                cover
            )
        )
        else "N/A"
    )

    model_sentence = (
        model_vs_market_sentence(
            bet_short,
            model_prediction,
            market_line,
        )
    )

    intro = (
        f"The {away_name} visit the "
        f"{home_name} at {stadium}. "
        f"{model_sentence}"
    )

    if has_bet:

        wager_sentence = (
            f"The best number we found is "
            f"{bet_short} {bet_line} at "
            f"{price_str}. "
            f"We give {bet_short} a "
            f"{cover_str} chance to cover, "
            f"which creates an "
            f"{edge_pct} edge for us. "
            f"That clears our 3% threshold, "
            f"so {bet_short} is a bet."
        )

    else:

        wager_sentence = (
            f"The best number we found is "
            f"{bet_short} {bet_line} at "
            f"{price_str}. "
            f"We see a {edge_pct} edge there, "
            f"but that does not clear our "
            f"3% threshold, so we are passing."
        )

    return [
        intro,
        "",
        wager_sentence,
    ]


# --------------------------------------------------------------------------- #
# Guide enrichment
# --------------------------------------------------------------------------- #

def build_guide_enrichment(
    *,
    args: argparse.Namespace,
    away_id: int,
    home_id: int,
    away_name: str,
    home_name: str,
    away_short: str,
    home_short: str,
    bet_id: int,
    opp_id: int,
    bet_name: str,
    bet_short: str,
    opp_name: str,
    opp_short: str,
    verdict_row: pd.Series,
    bet_facts: Dict[str, object],
    has_bet: bool,
    season: int,
    week: int,
    crosswalk: pd.DataFrame,
    ranked_stats: pd.DataFrame,
) -> Dict[str, object]:

    default = {
        "enabled":
            False,

        "narrative":
            "",

        "matchup_to_watch":
            "",

        "guide_status":
            [],

        "guide_extractions":
            [],

        "matchup_angles":
            [],

        "used_sources":
            [],

        "model_context":
            {},
    }

    if args.disable_guides:

        default[
            "guide_status"
        ] = [
            "Guide enrichment disabled"
        ]

        return default

    guide_root = (
        Path(
            args.guide_dir
        ).resolve()
    )

    (
        guide_results,
        guide_status,
    ) = (
        cfb_game_guides
        .load_game_guides(
            away_id=away_id,
            home_id=home_id,
            away_name=away_name,
            home_name=home_name,
            season=season,
            week=week,
            crosswalk=crosswalk,
            guide_root=guide_root,
            model=args.openai_model,
        )
    )

    default[
        "guide_status"
    ] = guide_status

    default[
        "guide_extractions"
    ] = guide_results

    if not guide_results:
        return default

    matchup_angles = (
        cfb_game_guides
        .identify_matchup_angles(
            bet_id=bet_id,
            opp_id=opp_id,
            ranked_stats=ranked_stats,
            crosswalk=crosswalk,
        )
    )

    model_prediction = (
        safe_float(
            verdict_row.get(
                "model_prediction"
            )
        )
    )

    market_line = (
        safe_float(
            verdict_row.get(
                "market_line"
            )
        )
    )

    best_line = (
        safe_float(
            verdict_row.get(
                "best_line"
            )
        )
    )

    model_context = (
        cfb_game_guides
        .build_model_context(
            bet_name=bet_name,
            bet_short=bet_short,
            opponent_name=opp_name,
            opponent_short=opp_short,
            model_prediction=model_prediction,
            market_line=market_line,
            best_line=best_line,
            best_price=verdict_row.get(
                "best_price"
            ),
            cover_probability=bet_facts.get(
                "cover"
            ),
            edge=bet_facts.get(
                "edge"
            ),
            has_bet=has_bet,
        )
    )

    default[
        "model_context"
    ] = model_context

    default[
        "matchup_angles"
    ] = matchup_angles

    try:

        narrative = (
            cfb_game_guides
            .generate_matchup_narrative(
                away_name=away_name,
                home_name=home_name,
                away_short=away_short,
                home_short=home_short,
                model_context=model_context,
                matchup_angles=matchup_angles,
                guide_results=guide_results,
                model=args.openai_model,
            )
        )

    except Exception as exc:

        default[
            "guide_status"
        ].append(
            "Narrative generation failed: "
            f"{exc}"
        )

        return default

    default[
        "enabled"
    ] = bool(
        narrative.get(
            "used_guides"
        )
    )

    default[
        "narrative"
    ] = (
        narrative.get(
            "narrative",
            "",
        )
    )

    default[
        "matchup_to_watch"
    ] = (
        narrative.get(
            "matchup_to_watch",
            "",
        )
    )

    default[
        "used_sources"
    ] = (
        narrative.get(
            "used_sources",
            [],
        )
    )

    return default


# --------------------------------------------------------------------------- #
# Markdown article
# --------------------------------------------------------------------------- #

def build_article(
    game: str,
    game_rows: pd.DataFrame,
    week: int,
    season: int,
    crosswalk: pd.DataFrame,
    ranked_stats: pd.DataFrame,
    venue_lookup: Dict,
    edge_game_count: int,
    args: argparse.Namespace,
) -> Tuple[
    str,
    Dict[str, object],
]:

    (
        away_team,
        home_team,
    ) = game.split(
        "@"
    )

    rows_by_team = {
        row[
            "team"
        ]: row
        for _, row
        in game_rows.iterrows()
    }

    away_row = (
        rows_by_team[
            away_team
        ]
    )

    home_row = (
        rows_by_team[
            home_team
        ]
    )

    clean_cw = (
        crosswalk
        .dropna(
            subset=[
                "team_id"
            ]
        )
        .drop_duplicates(
            subset=[
                "team_id"
            ]
        )
    )

    id_to_btb = (
        clean_cw
        .dropna(
            subset=[
                "btb_team"
            ]
        )
        .set_index(
            "team_id"
        )[
            "btb_team"
        ]
        .to_dict()
    )

    id_to_short = (
        clean_cw
        .dropna(
            subset=[
                "btb_team_short"
            ]
        )
        .set_index(
            "team_id"
        )[
            "btb_team_short"
        ]
        .to_dict()
    )

    away_id = (
        cfb_stats
        .team_id_from_logo(
            away_row.get(
                "logo"
            )
        )
    )

    home_id = (
        cfb_stats
        .team_id_from_logo(
            home_row.get(
                "logo"
            )
        )
    )

    away_name = (
        id_to_btb.get(
            away_id,
            away_team,
        )
    )

    home_name = (
        id_to_btb.get(
            home_id,
            home_team,
        )
    )

    away_short = (
        id_to_short.get(
            away_id,
            away_name,
        )
    )

    home_short = (
        id_to_short.get(
            home_id,
            home_name,
        )
    )

    kickoff_title = (
        format_kickoff_date(
            away_row.get(
                "commence_time"
            )
        )
    )

    stadium_name = None

    if (
        away_id is not None
        and home_id is not None
    ):

        stadium_name = (
            venue_lookup.get(
                (
                    frozenset(
                        [
                            away_id,
                            home_id,
                        ]
                    ),
                    week,
                )
            )
        )

    sorted_rows = (
        game_rows
        .sort_values(
            "market_line"
        )
        .reset_index(
            drop=True
        )
    )

    favorite_row = (
        sorted_rows.iloc[0]
    )

    dog_row = (
        sorted_rows.iloc[-1]
    )

    fav_edge = (
        resolve_edge_numeric(
            favorite_row
        )
    )

    dog_edge = (
        resolve_edge_numeric(
            dog_row
        )
    )

    verdict_is_favorite = (
        fav_edge is not None
        and (
            dog_edge is None
            or fav_edge
            >= dog_edge
        )
    )

    verdict_row = (
        favorite_row
        if verdict_is_favorite
        else dog_row
    )

    other_row = (
        dog_row
        if verdict_is_favorite
        else favorite_row
    )

    verdict_edge = (
        resolve_edge_numeric(
            verdict_row
        )
        or 0.0
    )

    has_bet = (
        verdict_edge
        >= FULL_BET_THRESHOLD
    )

    bet_id = (
        cfb_stats
        .team_id_from_logo(
            verdict_row.get(
                "logo"
            )
        )
    )

    opp_id = (
        cfb_stats
        .team_id_from_logo(
            other_row.get(
                "logo"
            )
        )
    )

    bet_name = (
        id_to_btb.get(
            bet_id,
            verdict_row[
                "team"
            ],
        )
    )

    opp_name = (
        id_to_btb.get(
            opp_id,
            other_row[
                "team"
            ],
        )
    )

    bet_short = (
        id_to_short.get(
            bet_id,
            bet_name,
        )
    )

    opp_short = (
        id_to_short.get(
            opp_id,
            opp_name,
        )
    )

    bet_facts = (
        _side_facts(
            verdict_row
        )
    )

    bet_line = (
        format_line(
            verdict_row.get(
                "best_line"
            )
        )
    )

    model_prediction = (
        safe_float(
            verdict_row.get(
                "model_prediction"
            )
        )
    )

    market_line = (
        safe_float(
            verdict_row.get(
                "market_line"
            )
        )
    )

    enrichment = (
        build_guide_enrichment(
            args=args,
            away_id=away_id,
            home_id=home_id,
            away_name=away_name,
            home_name=home_name,
            away_short=away_short,
            home_short=home_short,
            bet_id=bet_id,
            opp_id=opp_id,
            bet_name=bet_name,
            bet_short=bet_short,
            opp_name=opp_name,
            opp_short=opp_short,
            verdict_row=verdict_row,
            bet_facts=bet_facts,
            has_bet=has_bet,
            season=season,
            week=week,
            crosswalk=crosswalk,
            ranked_stats=ranked_stats,
        )
    )

    sections: List[str] = [
        (
            f"# {away_name} vs "
            f"{home_name} Prediction "
            f"For {kickoff_title}"
        ),
        "",
    ]

    away_logo = (
        away_row.get(
            "logo"
        )
    )

    home_logo = (
        home_row.get(
            "logo"
        )
    )

    if (
        away_logo
        and home_logo
    ):

        sections.append(
            f'<p align="center">'
            f'<img src="{away_logo}" '
            f'alt="{away_name}" '
            f'width="224" /> '
            f'<strong>vs</strong> '
            f'<img src="{home_logo}" '
            f'alt="{home_name}" '
            f'width="224" />'
            f'</p>'
        )

        sections.append(
            ""
        )

    sections.append(
        (
            "<p align='center'>"
            f"<img src='{BTB_LOGO_URL}' "
            "alt='BTB Analytics' "
            "width='100' />"
            "<br/>"
            "<em>Brought to you by "
            "BTB Analytics</em>"
            "</p>"
        )
    )

    sections.append(
        ""
    )

    # ------------------------------------------------------------------
    # Model table
    # ------------------------------------------------------------------

    sections.extend(
        [
            (
                "| Team name | "
                "Best Spread/Odds | "
                "Best Book | "
                "Cover Probability | "
                "BTB Advice |"
            ),
            "|---|---|---|---|---|",
        ]
    )

    for (
        row,
        team_name,
    ) in (
        (
            away_row,
            away_name,
        ),
        (
            home_row,
            home_name,
        ),
    ):

        edge = (
            resolve_edge_numeric(
                row
            )
        )

        cover = row.get(
            "best_cover_probability"
        )

        if (
            cover is None
            or pd.isna(
                cover
            )
        ):

            cover = row.get(
                "cover_probability"
            )

        sections.append(
            f"| {team_name} | "
            f"{format_line(row.get('best_line'))} "
            f"({_price(row.get('best_price'))}) "
            f"| {row.get('best_book') or 'N/A'} "
            f"| {display_percent(cover, 1)} "
            f"| {advice_badge_html(edge)} |"
        )

    # ------------------------------------------------------------------
    # Our Take
    # ------------------------------------------------------------------

    sections.extend(
        [
            "",
            "## Our Take",
            "",
        ]
    )

    sections.extend(
        build_our_take_opening(
            away_name=away_name,
            home_name=home_name,
            stadium_name=stadium_name,
            bet_short=bet_short,
            bet_line=bet_line,
            bet_facts=bet_facts,
            has_bet=has_bet,
            model_prediction=model_prediction,
            market_line=market_line,
        )
    )

    if enrichment[
        "narrative"
    ]:

        sections.extend(
            [
                "",
                enrichment[
                    "narrative"
                ].strip(),
            ]
        )

    else:

        sections.extend(
            [
                "",
                (
                    "We are not trying to predict "
                    "this game from one recent result. "
                    "For us, the question is whether "
                    "our overall expectation differs "
                    "enough from the market price to "
                    "create value."
                ),
            ]
        )

    # ------------------------------------------------------------------
    # Numbers
    # ------------------------------------------------------------------

    sections.extend(
        [
            "",
            "### What The Numbers Say",
            "",
            (
                "Rather than treating any single "
                "metric as the answer, we use these "
                "numbers to see where the strengths "
                "and weaknesses of the matchup "
                "actually line up."
            ),
            "",
        ]
    )

    sections.extend(
        cfb_stats
        .build_cfb_tale_of_tape(
            bet_id,
            opp_id,
            ranked_stats,
            crosswalk,
        )
    )

    sections.extend(
        [
            "",
            (
                "These ranks are across unique FBS "
                "teams over each team's last 10 games. "
                "Eckel rate measures the share of "
                "drives that score or reach a first "
                "down inside the opponent's "
                "40-yard line."
            ),
        ]
    )

    # ------------------------------------------------------------------
    # Matchup to watch
    # ------------------------------------------------------------------

    if enrichment[
        "matchup_to_watch"
    ]:

        sections.extend(
            [
                "",
                "### The Matchup To Watch",
                "",
                enrichment[
                    "matchup_to_watch"
                ].strip(),
            ]
        )

    # ------------------------------------------------------------------
    # CTA
    # ------------------------------------------------------------------

    sections.extend(
        [
            "",
            "## Best Bets Of The Week",
            "",
        ]
    )

    if (
        edge_game_count
        > 0
    ):

        plural = (
            "games"
            if edge_game_count != 1
            else "game"
        )

        sections.append(
            f"Our model found edges of "
            f"at least 3% on "
            f"**{edge_game_count} "
            f"{plural}** this week."
        )

        sections.extend(
            [
                "",
                (
                    "Want this same view for every "
                    "matchup? Members get our projected "
                    "line, cover probability, edge, and "
                    "best available sportsbook price "
                    "across the full CFB and NFL slate "
                    "at btb-analytics.com/member-access."
                ),
            ]
        )

    sections.extend(
        [
            "",
            (
                "<p align='center'><em>"
                "Built from our model. "
                "We target a 55-57% win rate "
                "and publish every result, "
                "wins and losses."
                "</em></p>"
            ),
        ]
    )

    payload = {
        "game":
            game,

        "away_team":
            away_team,

        "home_team":
            home_team,

        "away_name":
            away_name,

        "home_name":
            home_name,

        "away_short":
            away_short,

        "home_short":
            home_short,

        "away_logo":
            away_logo,

        "home_logo":
            home_logo,

        "away_id":
            away_id,

        "home_id":
            home_id,

        "bet_id":
            bet_id,

        "opp_id":
            opp_id,

        "bet_name":
            bet_name,

        "bet_short":
            bet_short,

        "opp_name":
            opp_name,

        "opp_short":
            opp_short,

        "model_prediction":
            model_prediction,

        "market_line":
            market_line,

        "best_line":
            safe_float(
                verdict_row.get(
                    "best_line"
                )
            ),

        "best_price":
            verdict_row.get(
                "best_price"
            ),

        "best_book":
            verdict_row.get(
                "best_book"
            ),

        "cover_probability":
            bet_facts.get(
                "cover"
            ),

        "edge":
            bet_facts.get(
                "edge"
            ),

        "has_bet":
            has_bet,

        "guide_enrichment":
            enrichment,
    }

    return (
        "\n".join(
            sections
        )
        + "\n",
        payload,
    )


# --------------------------------------------------------------------------- #
# Branded Squarespace HTML
# --------------------------------------------------------------------------- #

def render_btb_html(
    article_markdown: str,
) -> str:

    body = markdown.markdown(
        article_markdown,
        extensions=[
            "tables",
            "extra",
        ],
    )

    # ------------------------------------------------------------------
    # Wrap Our Take section
    # ------------------------------------------------------------------

    our_take_heading = (
        "<h2>Our Take</h2>"
    )

    numbers_heading = (
        "<h3>What The Numbers Say</h3>"
    )

    if (
        our_take_heading in body
        and numbers_heading in body
    ):

        before, remainder = body.split(
            our_take_heading,
            1,
        )

        take_content, after = (
            remainder.split(
                numbers_heading,
                1,
            )
        )

        body = (
            before
            + '<section class="btb-take">'
            + '<h2>Our Take</h2>'
            + take_content
            + "</section>"
            + numbers_heading
            + after
        )

    # ------------------------------------------------------------------
    # Wrap CTA
    # ------------------------------------------------------------------

    cta_heading = (
        "<h2>Best Bets Of The Week</h2>"
    )

    if cta_heading in body:

        before, cta_content = (
            body.split(
                cta_heading,
                1,
            )
        )

        body = (
            before
            + '<section class="btb-cta">'
            + "<h2>Best Bets Of The Week</h2>"
            + cta_content
            + (
                f'<a class="btb-button" '
                f'href="{MEMBER_URL}">'
                f"View Member Access"
                f"</a>"
            )
            + "</section>"
        )

    # ------------------------------------------------------------------
    # Identify the stats table
    #
    # build_cfb_tale_of_tape() should already output
    # class="btb-stats-table", but this provides a fallback
    # if Markdown strips/reworks it.
    # ------------------------------------------------------------------

    body = body.replace(
        '<table class="btb-stats-table">',
        '<table class="btb-stats-table">',
    )

    return f"""
<style>
.btb-matchup-article {{
    --btb-bg: #050505;
    --btb-card: #101010;
    --btb-card-raised: #151515;
    --btb-border: #2a2a2a;
    --btb-text: #f4f4f4;
    --btb-muted: #b7b7b7;
    --btb-green: #27e26f;
    --btb-pink: #ff7cb8;
    --btb-yellow: #ffd24d;

    width: 100%;
    max-width: 1180px;
    margin: 0 auto;
    padding: 42px 42px 50px;
    box-sizing: border-box;

    background: var(--btb-bg);
    color: var(--btb-text);

    font-family:
        Inter,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        Arial,
        sans-serif;

    line-height: 1.7;
}}

.btb-matchup-article * {{
    box-sizing: border-box;
}}

.btb-matchup-article h1 {{
    margin: 0 auto 28px;
    max-width: 940px;

    color: #ffffff;

    font-size: clamp(31px, 4vw, 48px);
    line-height: 1.12;
    letter-spacing: -0.025em;
    font-weight: 800;

    text-align: center;
}}

.btb-matchup-article h2 {{
    margin: 46px 0 18px;

    color: var(--btb-green);

    font-size: 28px;
    line-height: 1.2;
    font-weight: 800;
    letter-spacing: -0.01em;
}}

.btb-matchup-article h3 {{
    margin: 42px 0 16px;

    color: #ffffff;

    font-size: 23px;
    line-height: 1.3;
    font-weight: 800;
}}

.btb-matchup-article p {{
    margin: 0 0 20px;

    color: var(--btb-text);

    font-size: 17px;
}}

.btb-matchup-article strong {{
    color: #ffffff;
    font-weight: 800;
}}

.btb-matchup-article em {{
    color: var(--btb-muted);
}}

.btb-matchup-article a {{
    color: var(--btb-green);
}}

.btb-matchup-article img {{
    max-width: 100%;
    height: auto;
}}

.btb-matchup-article p[align="center"] {{
    text-align: center;
}}


/* -------------------------------------------------------- */
/* Advice pills                                             */
/* -------------------------------------------------------- */

.btb-matchup-article .btb-advice-pill {{
    display: inline-block;

    padding: 6px 10px;

    border-radius: 999px;

    font-size: 12px;
    line-height: 1.1;
    font-weight: 800;
    letter-spacing: 0.01em;

    white-space: nowrap;
}}

.btb-matchup-article .btb-advice-bet {{
    background:
        rgba(39, 226, 111, 0.16);

    color:
        var(--btb-green);

    border:
        1px solid
        rgba(39, 226, 111, 0.38);
}}

.btb-matchup-article .btb-advice-no-bet {{
    background:
        rgba(255, 124, 184, 0.15);

    color:
        var(--btb-pink);

    border:
        1px solid
        rgba(255, 124, 184, 0.38);
}}

.btb-matchup-article .btb-advice-lean {{
    background:
        rgba(255, 210, 77, 0.14);

    color:
        var(--btb-yellow);

    border:
        1px solid
        rgba(255, 210, 77, 0.32);
}}


/* -------------------------------------------------------- */
/* Generic tables                                           */
/* -------------------------------------------------------- */

.btb-matchup-article table {{
    width: 100%;

    margin: 26px 0 34px;

    border:
        1px solid
        var(--btb-border);

    border-radius: 10px;

    border-spacing: 0;
    border-collapse: separate;

    overflow: hidden;

    background:
        var(--btb-card);

    font-size: 15px;
}}

.btb-matchup-article thead {{
    background: #171717;
}}

.btb-matchup-article th {{
    padding: 14px 16px;

    color:
        var(--btb-green);

    font-weight: 800;
    text-align: left;

    border-bottom:
        1px solid
        var(--btb-border);
}}

.btb-matchup-article td {{
    padding: 14px 16px;

    color:
        var(--btb-text);

    border-bottom:
        1px solid
        #222222;
}}

.btb-matchup-article tbody tr:last-child td {{
    border-bottom: 0;
}}


/* -------------------------------------------------------- */
/* Stats table                                              */
/* -------------------------------------------------------- */

.btb-matchup-article .btb-stats-table {{
    background:
        #0d0d0d;
}}

.btb-matchup-article .btb-stats-table thead {{
    background:
        #161616;
}}

.btb-matchup-article .btb-stats-table tbody tr:nth-child(odd) {{
    background:
        #121212;
}}

.btb-matchup-article .btb-stats-table tbody tr:nth-child(even) {{
    background:
        #0b0b0b;
}}

.btb-matchup-article .btb-stats-table tbody tr:hover {{
    background:
        #191919;
}}

.btb-matchup-article .btb-stats-table .btb-stat-name {{
    font-weight: 800;
    color: #ffffff;
}}

.btb-matchup-article .btb-stats-table .btb-better {{
    font-weight: 900;
    color: var(--btb-green);
}}

.btb-matchup-article .btb-stats-table td:not(.btb-stat-name) {{
    text-align: center;
}}

.btb-matchup-article .btb-stats-table th:not(:first-child) {{
    text-align: center;
}}


/* -------------------------------------------------------- */
/* Our Take card                                            */
/* -------------------------------------------------------- */

.btb-matchup-article .btb-take {{
    margin: 38px 0 36px;

    padding:
        26px 28px 12px;

    background:
        linear-gradient(
            145deg,
            #111111,
            #0b0b0b
        );

    border:
        1px solid
        var(--btb-border);

    border-left:
        4px solid
        var(--btb-green);

    border-radius: 10px;
}}

.btb-matchup-article .btb-take h2 {{
    margin-top: 0;
}}

.btb-matchup-article .btb-take p:first-of-type {{
    font-size: 18px;
}}


/* -------------------------------------------------------- */
/* CTA                                                      */
/* -------------------------------------------------------- */

.btb-matchup-article .btb-cta {{
    margin-top: 48px;

    padding: 28px;

    background:
        var(--btb-card-raised);

    border:
        1px solid
        var(--btb-border);

    border-radius: 12px;
}}

.btb-matchup-article .btb-cta h2 {{
    margin-top: 0;
}}

.btb-matchup-article .btb-button {{
    display: inline-block;

    margin-top: 6px;

    padding: 13px 21px;

    background:
        var(--btb-green);

    color:
        #050505 !important;

    border-radius: 7px;

    font-size: 15px;
    font-weight: 800;

    text-decoration:
        none !important;

    transition:
        transform .15s ease,
        opacity .15s ease;
}}

.btb-matchup-article .btb-button:hover {{
    opacity: .9;

    transform:
        translateY(-1px);
}}


/* -------------------------------------------------------- */
/* Mobile                                                   */
/* -------------------------------------------------------- */

@media (
    max-width: 760px
) {{

    .btb-matchup-article {{
        padding:
            28px 17px 40px;
    }}

    .btb-matchup-article h1 {{
        font-size: 31px;
    }}

    .btb-matchup-article h2 {{
        font-size: 25px;
    }}

    .btb-matchup-article h3 {{
        font-size: 21px;
    }}

    .btb-matchup-article p {{
        font-size: 16px;
    }}

    .btb-matchup-article .btb-take {{
        padding:
            21px 19px 8px;
    }}

    .btb-matchup-article .btb-cta {{
        padding:
            22px 20px;
    }}

    .btb-matchup-article table {{
        display: block;

        overflow-x: auto;

        white-space: nowrap;

        font-size: 14px;

        -webkit-overflow-scrolling:
            touch;
    }}

    .btb-matchup-article th,
    .btb-matchup-article td {{
        padding: 12px;
    }}

    .btb-matchup-article .btb-advice-pill {{
        padding:
            5px 8px;

        font-size: 11px;
    }}
}}
</style>

<div class="btb-matchup-article">
{body}
</div>
""".strip()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:

    args = parse_args()

    output_root = (
        Path(
            args.output_dir
        ).resolve()
    )

    safe_mkdir(
        output_root
    )

    session = (
        requests.Session()
    )

    session.headers[
        "User-Agent"
    ] = (
        "football-testgrounds-"
        "cfb-articles/5.1"
    )

    (
        spreads,
        week,
        season,
    ) = load_spreads(
        args,
        session,
    )

    crosswalk = (
        load_crosswalk(
            args,
            session,
        )
    )

    stats_years = [
        season - 1,
        season,
    ]

    (
        ranked_stats,
        venue_lookup,
    ) = (
        cfb_stats
        .build_cfb_stats_and_venues(
            stats_years,
            crosswalk,
            api_key=os.getenv(
                "CFBD_API_KEY"
            ),
        )
    )

    unique_team_count = (
        ranked_stats[
            "team_id"
        ]
        .nunique()
    )

    print(
        "Article generator received "
        f"{unique_team_count} unique "
        "ranked FBS teams."
    )

    # ------------------------------------------------------------------
    # Count full-bet games
    # ------------------------------------------------------------------

    if (
        "best_edge"
        in spreads.columns
    ):

        edge_values = (
            spreads[
                "best_edge"
            ]
            .map(
                parse_percent
            )
        )

    else:

        edge_values = (
            spreads[
                "edge"
            ]
            .map(
                parse_percent
            )
        )

    edge_game_count = int(
        spreads[
            edge_values
            >= FULL_BET_THRESHOLD
        ][
            "game"
        ]
        .nunique()
    )

    merged = spreads

    # ------------------------------------------------------------------
    # Optional team filtering
    # ------------------------------------------------------------------

    if args.teams:

        requested = {
            team.strip()
            for team
            in args.teams
            if team.strip()
        }

        eligible = (
            merged[
                merged[
                    "team"
                ].isin(
                    requested
                )
            ][
                "game"
            ]
            .unique()
        )

        merged = (
            merged[
                merged[
                    "game"
                ].isin(
                    eligible
                )
            ]
            .copy()
        )

    weekly_dir = (
        output_root
        / f"week_{week}"
    )

    safe_mkdir(
        weekly_dir
    )

    combined: List[str] = []

    payload = {
        "generated_at_utc":
            datetime.now(
                UTC
            ).isoformat(),

        "season":
            season,

        "week":
            week,

        "articles":
            [],
    }

    for (
        game,
        game_rows,
    ) in merged.groupby(
        "game",
        sort=True,
    ):

        print(
            f"\nGenerating {game}..."
        )

        (
            article,
            article_payload,
        ) = build_article(
            game=game,
            game_rows=(
                game_rows.copy()
            ),
            week=week,
            season=season,
            crosswalk=crosswalk,
            ranked_stats=ranked_stats,
            venue_lookup=venue_lookup,
            edge_game_count=edge_game_count,
            args=args,
        )

        game_slug = (
            slugify_game(
                game
            )
        )

        # ------------------------------------------------------------------
        # Markdown
        # ------------------------------------------------------------------

        article_path = (
            weekly_dir
            / f"{game_slug}.md"
        )

        article_path.write_text(
            article,
            encoding="utf-8",
        )

        article_payload[
            "article_path"
        ] = (
            f"{game_slug}.md"
        )

        # ------------------------------------------------------------------
        # Branded HTML
        # ------------------------------------------------------------------

        html_article = (
            render_btb_html(
                article
            )
        )

        html_path = (
            weekly_dir
            / f"{game_slug}.html"
        )

        html_path.write_text(
            html_article,
            encoding="utf-8",
        )

        article_payload[
            "html_path"
        ] = (
            f"{game_slug}.html"
        )

        combined.append(
            article.rstrip()
        )

        # ------------------------------------------------------------------
        # Source audit
        # ------------------------------------------------------------------

        audit_payload = {
            "game":
                game,

            "season":
                season,

            "week":
                week,

            "generated_at_utc":
                datetime.now(
                    UTC
                ).isoformat(),

            "model_prediction":
                article_payload.get(
                    "model_prediction"
                ),

            "market_line":
                article_payload.get(
                    "market_line"
                ),

            "best_line":
                article_payload.get(
                    "best_line"
                ),

            "best_price":
                article_payload.get(
                    "best_price"
                ),

            "best_book":
                article_payload.get(
                    "best_book"
                ),

            "cover_probability":
                article_payload.get(
                    "cover_probability"
                ),

            "edge":
                article_payload.get(
                    "edge"
                ),

            "bet_short":
                article_payload.get(
                    "bet_short"
                ),

            "guide_enrichment":
                article_payload.get(
                    "guide_enrichment",
                    {},
                ),
        }

        audit_path = (
            weekly_dir
            / (
                f"{game_slug}"
                ".sources.json"
            )
        )

        audit_path.write_text(
            json.dumps(
                audit_payload,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

        article_payload[
            "source_audit_path"
        ] = (
            f"{game_slug}"
            ".sources.json"
        )

        payload[
            "articles"
        ].append(
            article_payload
        )

        guide_status = (
            article_payload
            .get(
                "guide_enrichment",
                {},
            )
            .get(
                "guide_status",
                [],
            )
        )

        for message in guide_status:

            print(
                f"  - {message}"
            )

    # ------------------------------------------------------------------
    # Weekly combined Markdown
    # ------------------------------------------------------------------

    (
        weekly_dir
        / "weekly_matchup_articles.md"
    ).write_text(
        "\n\n---\n\n".join(
            combined
        )
        + "\n",
        encoding="utf-8",
    )

    # ------------------------------------------------------------------
    # Weekly JSON
    # ------------------------------------------------------------------

    (
        weekly_dir
        / "weekly_matchup_articles.json"
    ).write_text(
        json.dumps(
            payload,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print(
        "\nGenerated "
        f"{len(combined)} "
        "CFB matchup article(s) "
        f"for week {week} "
        f"in {weekly_dir}"
    )


if __name__ == "__main__":
    main()
