#!/usr/bin/env python3
"""
Generate weekly College Football matchup articles from odds + CFBD stats +
optional team game guides.

Core betting logic remains deterministic.

Optional narrative enrichment:
- game_guides/{season}/{btb_team_short}.pdf
- cfb_game_guides.py extracts verified facts
- OpenAI generates BTB-style narrative from verified facts + deterministic
  model context
- missing guides NEVER prevent article generation

Requires:
- cfb_stats.py
- cfb_game_guides.py
- CFBD_API_KEY
- OPENAI_API_KEY for guide-enriched narrative
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import cfb_stats
import cfb_game_guides


TRASH_SCHEDULE_OWNER = "trashduty"
TRASH_SCHEDULE_REPO = "trash-schedule"
TRASH_SCHEDULE_REF = "main"

TRASH_SCHEDULE_SPREADS_PATH = (
    "CFB_Odds/Data/spreads_odds.csv"
)

TRASH_SCHEDULE_CROSSWALK_PATH = (
    "CFB_Odds/Data/CFB Teams Full Crosswalk.csv"
)

FULL_BET_THRESHOLD = 0.03

ET = ZoneInfo("America/New_York")

REQUEST_TIMEOUT = 30

BTB_LOGO = (
    "<p align='center'>"
    "<img src='https://raw.githubusercontent.com/trashduty/"
    "football-testgrounds/main/BTB%20Analytics%20.png.png' "
    "alt='BTB Analytics' width='100' />"
    "<br/><em>Brought to you by BTB Analytics</em>"
    "</p>"
)


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--output-dir",
        default="outputs/matchup_articles",
    )

    parser.add_argument(
        "--guide-dir",
        default="game_guides",
        help=(
            "Root folder containing "
            "game_guides/{season}/{btb_team_short}.pdf"
        ),
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
        help=(
            "Path to team crosswalk CSV. "
            "If omitted, fetch from trash-schedule."
        ),
    )

    parser.add_argument(
        "--trash-schedule-dir",
    )

    parser.add_argument(
        "--trash-schedule-owner",
        default=TRASH_SCHEDULE_OWNER,
    )

    parser.add_argument(
        "--trash-schedule-repo",
        default=TRASH_SCHEDULE_REPO,
    )

    parser.add_argument(
        "--trash-schedule-ref",
        default=TRASH_SCHEDULE_REF,
    )

    parser.add_argument(
        "--disable-guides",
        action="store_true",
        help="Generate deterministic article without AI guide enrichment.",
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

def safe_mkdir(path: Path) -> None:
    path.mkdir(
        parents=True,
        exist_ok=True,
    )


def slugify_game(game: str) -> str:
    return (
        game.lower()
        .replace("@", "_at_")
        .replace(" ", "_")
    )


def parse_percent(value: object) -> float:

    if value is None or pd.isna(value):
        return float("nan")

    if isinstance(value, str):
        stripped = value.strip()

        if stripped.endswith("%"):
            return (
                float(stripped.rstrip("%"))
                / 100.0
            )

        value = stripped

    numeric = float(value)

    return (
        numeric / 100.0
        if numeric > 1
        else numeric
    )


def display_percent(
    value: object,
    digits: int = 1,
) -> str:

    if value is None or pd.isna(value):
        return "N/A"

    numeric = float(value)

    if abs(numeric) <= 1:
        numeric *= 100

    return f"{numeric:.{digits}f}%"


def format_line(value: object) -> str:

    if value is None or pd.isna(value):
        return "N/A"

    f = float(value)

    return (
        f"+{f:.1f}"
        if f > 0
        else f"{f:.1f}"
    )


def _price(value: object) -> str:

    if value is None or pd.isna(value):
        return "-110"

    f = float(value)

    return (
        f"{f:+.0f}"
        if f > 0
        else f"{f:.0f}"
    )


def resolve_edge_numeric(
    row: pd.Series,
) -> Optional[float]:

    edge = row.get("best_edge")

    if edge is None or pd.isna(edge):
        edge = row.get("edge")

    if edge is None or pd.isna(edge):
        return None

    return float(
        parse_percent(edge)
    )


def _side_facts(
    row: pd.Series,
) -> Dict[str, object]:

    cover = row.get(
        "best_cover_probability"
    )

    if cover is None or pd.isna(cover):
        cover = row.get(
            "cover_probability"
        )

    edge = row.get("best_edge")

    if edge is None or pd.isna(edge):
        edge = row.get("edge")

    return {
        "cover": cover,
        "edge": edge,
        "line": row.get("best_line"),
        "price": row.get("best_price"),
    }


def edge_confidence_label(
    edge: Optional[float],
) -> str:

    if (
        edge is None
        or edge < FULL_BET_THRESHOLD
    ):
        return "Pass"

    if edge >= 0.07:
        return "Strong"

    return "Lean"


def matchup_call_label(
    edge: Optional[float],
) -> str:

    if edge is None or edge < 0.01:
        return "No Bet"

    if edge < FULL_BET_THRESHOLD:
        return (
            "Lean – doesn't meet our "
            "edge criteria to fully bet"
        )

    return "Bet"


def model_vs_market_lead(
    team_name: str,
    market_line: float,
    best_line: float,
) -> str:

    def fmt(v: float) -> str:
        s = (
            f"{v:+.1f}"
            if v >= 0
            else f"{v:.1f}"
        )

        return (
            s.rstrip("0")
            .rstrip(".")
        )

    return (
        f"the model favors **the {team_name}** "
        f"at {fmt(best_line)} "
        f"vs. the market at {fmt(market_line)}."
    )


def format_kickoff_date(
    commence_time: object,
) -> str:

    if (
        commence_time is None
        or pd.isna(commence_time)
    ):
        return "N/A"

    ts = pd.Timestamp(
        commence_time
    )

    if ts.tzinfo is None:
        ts = ts.tz_localize(
            "UTC"
        )

    return (
        ts.tz_convert(ET)
        .strftime("%m/%d/%Y")
    )


# --------------------------------------------------------------------------- #
# Repo fetch
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

    if local_root is not None:
        return (
            local_root / path
        ).read_text(
            encoding="utf-8"
        )

    raw_url = (
        f"https://raw.githubusercontent.com/"
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
            "https://api.github.com/repos/"
            f"{owner}/{repo}/contents/"
            f"{path}?ref={ref}"
        )

        api_response = session.get(
            api_url,
            headers={
                "Accept":
                    "application/vnd.github+json",
                "Authorization":
                    f"token {token}",
            },
            timeout=REQUEST_TIMEOUT,
        )

        api_response.raise_for_status()

        return (
            base64.b64decode(
                api_response.json()["content"]
            )
            .decode("utf-8")
        )

    response.raise_for_status()

    return response.text


def load_spreads(
    args: argparse.Namespace,
    session: requests.Session,
) -> Tuple[pd.DataFrame, int, int]:

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

    spreads["week"] = (
        spreads["week"]
        .astype(int)
    )

    spreads["commence_time"] = (
        pd.to_datetime(
            spreads["commence_time"],
            errors="coerce",
            utc=True,
        )
    )

    spreads["team_id"] = (
        spreads["logo"]
        .map(
            cfb_stats.team_id_from_logo
        )
        .astype("Int64")
    )

    week = (
        args.week
        if args.week is not None
        else int(
            spreads["week"].max()
        )
    )

    week_spreads = (
        spreads[
            spreads["week"] == week
        ]
        .copy()
    )

    if week_spreads.empty:
        raise ValueError(
            f"No spreads rows for week {week}"
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

    if "team_id" in cw.columns:
        cw["team_id"] = (
            pd.to_numeric(
                cw["team_id"],
                errors="coerce",
            )
            .astype("Int64")
        )

    return cw


# --------------------------------------------------------------------------- #
# Bottom line
# --------------------------------------------------------------------------- #

def build_bottom_line(
    away_name: str,
    home_name: str,
    stadium_name: Optional[str],
    bet_name: str,
    bet_line: str,
    bet_facts: Dict[str, object],
    has_bet: bool,
    model_lead: Optional[str],
) -> List[str]:

    stadium = (
        stadium_name
        or "their home stadium"
    )

    raw_edge = bet_facts.get(
        "edge"
    )

    edge = (
        float(raw_edge)
        if raw_edge is not None
        and not pd.isna(raw_edge)
        else 0.0
    )

    edge_pct = (
        f"{edge * 100:.2f}%"
    )

    price = bet_facts.get(
        "price"
    )

    price_str = (
        str(int(price))
        if price is not None
        and not pd.isna(price)
        else "N/A"
    )

    if has_bet:

        lead = (
            model_lead
            or (
                f"the model likes "
                f"{bet_name} "
                f"{bet_line}."
            )
        )

        if (
            lead
            and lead[0].isupper()
        ):
            lead = (
                lead[0].lower()
                + lead[1:]
            )

        intro = (
            f"The {away_name} take on "
            f"the {home_name} at "
            f"{stadium} and {lead}"
        )

        edge_line = (
            f"This puts the edge at "
            f"{edge_pct}, which at "
            f"{bet_line} for "
            f"{price_str} makes the "
            f"{bet_name} a bet."
        )

        return [
            "## The Bottom Line",
            intro,
            edge_line,
        ]

    text = (
        f"The {away_name} take on the "
        f"{home_name} at {stadium} and "
        f"the model sees a lean toward "
        f"{bet_name} {bet_line} with an "
        f"edge of {edge_pct}, but this "
        f"does not clear our 3% threshold "
        f"for a full bet, so we are "
        f"passing on this one."
    )

    return [
        "## The Bottom Line",
        text,
    ]


def build_cta(
    edge_game_count: int,
) -> List[str]:

    lines = [
        "",
        "## Best Bets Of The Week",
        "",
    ]

    if edge_game_count > 0:

        plural = (
            "games"
            if edge_game_count != 1
            else "game"
        )

        lines.append(
            f"Our model found edges of at "
            f"least 3% on **{edge_game_count} "
            f"{plural}** this week. "
            f"See the model output for every "
            f"NFL and CFB game at "
            f"btb-analytics.com/member-access."
        )

    lines.append("")

    lines.append(
        "<p align='center'><em>"
        "Built by the BTB model. "
        "We target a 55-57% win rate and "
        "publish every result, wins and losses."
        "</em></p>"
    )

    return lines


# --------------------------------------------------------------------------- #
# Guide integration helpers
# --------------------------------------------------------------------------- #

def safe_float(
    value: object,
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


def build_guide_enrichment(
    *,
    args: argparse.Namespace,
    away_id: int,
    home_id: int,
    away_name: str,
    home_name: str,
    bet_id: int,
    opp_id: int,
    bet_name: str,
    opp_name: str,
    verdict_row: pd.Series,
    bet_facts: Dict[str, object],
    has_bet: bool,
    season: int,
    week: int,
    crosswalk: pd.DataFrame,
    ranked_stats: pd.DataFrame,
) -> Dict[str, object]:

    default = {
        "enabled": False,
        "narrative": "",
        "matchup_to_watch": "",
        "guide_status": [],
        "guide_extractions": [],
        "matchup_angles": [],
        "used_sources": [],
    }

    if args.disable_guides:
        default[
            "guide_status"
        ] = [
            "Guide enrichment disabled"
        ]
        return default

    guide_root = Path(
        args.guide_dir
    ).resolve()

    guide_results, guide_status = (
        cfb_game_guides.load_game_guides(
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

    model_context = (
        cfb_game_guides
        .build_model_context(
            bet_name=bet_name,
            opponent_name=opp_name,
            market_line=(
                safe_float(
                    verdict_row.get(
                        "market_line"
                    )
                )
                or 0.0
            ),
            best_line=(
                safe_float(
                    verdict_row.get(
                        "best_line"
                    )
                )
                or 0.0
            ),
            best_price=(
                verdict_row.get(
                    "best_price"
                )
            ),
            cover_probability=(
                bet_facts.get(
                    "cover"
                )
            ),
            edge=(
                bet_facts.get(
                    "edge"
                )
            ),
            has_bet=has_bet,
        )
    )

    try:
        narrative = (
            cfb_game_guides
            .generate_matchup_narrative(
                away_name=away_name,
                home_name=home_name,
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

    default[
        "matchup_angles"
    ] = matchup_angles

    default[
        "model_context"
    ] = model_context

    return default


# --------------------------------------------------------------------------- #
# Article
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

    away_team, home_team = (
        game.split("@")
    )

    rows_by_team = {
        row["team"]: row
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

    id_to_btb = (
        crosswalk
        .dropna(
            subset=["team_id"]
        )
        .set_index("team_id")[
            "btb_team"
        ]
        .to_dict()
    )

    away_id = (
        cfb_stats
        .team_id_from_logo(
            away_row.get("logo")
        )
    )

    home_id = (
        cfb_stats
        .team_id_from_logo(
            home_row.get("logo")
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

    kickoff_title = (
        format_kickoff_date(
            away_row.get(
                "commence_time"
            )
        )
    )

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
        if away_id
        and home_id
        else None
    )

    favorite_row = (
        game_rows
        .sort_values(
            "market_line"
        )
        .iloc[0]
    )

    dog_row = (
        game_rows
        .sort_values(
            "market_line"
        )
        .iloc[-1]
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

    verdict_row = (
        favorite_row
        if (
            fav_edge is not None
            and (
                dog_edge is None
                or fav_edge >= dog_edge
            )
        )
        else dog_row
    )

    other_row = (
        dog_row
        if verdict_row is favorite_row
        else favorite_row
    )

    has_bet = (
        (
            resolve_edge_numeric(
                verdict_row
            )
            or 0
        )
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
            verdict_row["team"],
        )
    )

    opp_name = (
        id_to_btb.get(
            opp_id,
            other_row["team"],
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

        sections.append("")

    sections.append(
        BTB_LOGO
    )

    sections.append("")

    # -------------------------------------------------
    # Summary table
    # -------------------------------------------------

    sections.extend(
        [
            (
                "| Team name | "
                "Best Spread/Odds | "
                "Best Book | "
                "Cover Probability | "
                "BTB Advice |"
            ),
            (
                "|---|---|---|---|---|"
            ),
        ]
    )

    for row, team_name in (
        (away_row, away_name),
        (home_row, home_name),
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
            or pd.isna(cover)
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
            f"| {matchup_call_label(edge)} |"
        )

    if not has_bet:

        sections.extend(
            [
                "",
                (
                    "The model sees a lean here, "
                    "but the edge does not clear "
                    "our 3% threshold, so there "
                    "is no play."
                ),
            ]
        )

    market_line = (
        safe_float(
            verdict_row.get(
                "market_line"
            )
        )
        or 0.0
    )

    best_line = (
        safe_float(
            verdict_row.get(
                "best_line"
            )
        )
        or 0.0
    )

    model_lead = (
        model_vs_market_lead(
            bet_name,
            market_line,
            best_line,
        )
    )

    sections.extend(
        [""]
        + build_bottom_line(
            away_name,
            home_name,
            stadium_name,
            bet_name,
            bet_line,
            bet_facts,
            has_bet,
            model_lead,
        )
    )

    # -------------------------------------------------
    # NEW: guide enrichment
    # -------------------------------------------------

    enrichment = (
        build_guide_enrichment(
            args=args,
            away_id=away_id,
            home_id=home_id,
            away_name=away_name,
            home_name=home_name,
            bet_id=bet_id,
            opp_id=opp_id,
            bet_name=bet_name,
            opp_name=opp_name,
            verdict_row=verdict_row,
            bet_facts=bet_facts,
            has_bet=has_bet,
            season=season,
            week=week,
            crosswalk=crosswalk,
            ranked_stats=ranked_stats,
        )
    )

    sections.extend(
        [
            "",
            "## Why The Pick",
            "",
        ]
    )

    if enrichment[
        "narrative"
    ]:

        sections.append(
            enrichment[
                "narrative"
            ].strip()
        )

        sections.append("")

    else:

        sections.append(
            (
                "The model is less interested "
                "in the final score from one "
                "Saturday than in whether the "
                "underlying matchup creates a "
                "difference between our "
                "expectation and the number "
                "being offered by the market."
            )
        )

        sections.append("")

    # -------------------------------------------------
    # Existing deterministic data table
    # -------------------------------------------------

    sections.extend(
        [
            "### What The Numbers Say",
            "",
            (
                "Rather than treating any "
                "single metric as the answer, "
                "we use these numbers to see "
                "where the strengths and "
                "weaknesses of the matchup "
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
                "Ranks are across FBS over each "
                "team's last 10 games. Eckel "
                "rate measures the share of "
                "drives that score or reach a "
                "first down inside the "
                "opponent's 40-yard line."
            ),
        ]
    )

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

    sections.extend(
        build_cta(
            edge_game_count
        )
    )

    payload = {
        "game": game,
        "away_team": away_team,
        "home_team": home_team,
        "away_id": away_id,
        "home_id": home_id,
        "bet_id": bet_id,
        "opp_id": opp_id,
        "bet_name": bet_name,
        "opp_name": opp_name,
        "has_bet": has_bet,
        "guide_enrichment": enrichment,
    }

    return (
        "\n".join(sections)
        + "\n",
        payload,
    )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:

    args = parse_args()

    output_root = Path(
        args.output_dir
    ).resolve()

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
        "cfb-articles/2.0"
    )

    spreads, week, season = (
        load_spreads(
            args,
            session,
        )
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

    ranked_stats, venue_lookup = (
        cfb_stats
        .build_cfb_stats_and_venues(
            stats_years,
            crosswalk,
            api_key=os.getenv(
                "CFBD_API_KEY"
            ),
        )
    )

    edge_game_count = int(
        spreads[
            spreads[
                "best_edge"
            ].map(
                parse_percent
            )
            >= FULL_BET_THRESHOLD
        ][
            "game"
        ].nunique()
    )

    merged = spreads

    if args.teams:

        requested = set(
            args.teams
        )

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
        "generated_at_utc": (
            datetime.now(
                UTC
            ).isoformat()
        ),
        "season": season,
        "week": week,
        "articles": [],
    }

    for game, game_rows in (
        merged.groupby(
            "game",
            sort=True,
        )
    ):

        print(
            f"\nGenerating {game}..."
        )

        article, article_payload = (
            build_article(
                game,
                game_rows.copy(),
                week,
                season,
                crosswalk,
                ranked_stats,
                venue_lookup,
                edge_game_count,
                args,
            )
        )

        game_slug = (
            slugify_game(
                game
            )
        )

        article_path = (
            weekly_dir
            / f"{game_slug}.md"
        )

        article_path.write_text(
            article,
            encoding="utf-8",
        )

        combined.append(
            article.rstrip()
        )

        article_payload[
            "article_path"
        ] = (
            f"{game_slug}.md"
        )

        # ---------------------------------------------
        # Source audit file
        # ---------------------------------------------

        audit_payload = {
            "game": game,
            "season": season,
            "week": week,
            "generated_at_utc": (
                datetime.now(
                    UTC
                ).isoformat()
            ),
            "guide_enrichment": (
                article_payload.get(
                    "guide_enrichment",
                    {},
                )
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
        f"\nGenerated "
        f"{len(combined)} "
        f"CFB matchup article(s) "
        f"for week {week} "
        f"in {weekly_dir}"
    )


if __name__ == "__main__":
    main()
