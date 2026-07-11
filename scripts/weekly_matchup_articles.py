#!/usr/bin/env python3
"""Generate weekly NFL matchup articles from odds, model, nflverse, and ESPN data."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
from collections import namedtuple
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup

TRASH_SCHEDULE_OWNER = "trashduty"
TRASH_SCHEDULE_REPO = "trash-schedule"
TRASH_SCHEDULE_REF = "main"

TRASH_SCHEDULE_SPREADS_PATH = "NFL_Odds/Data/spreads_odds.csv"
TRASH_SCHEDULE_MODEL_TEMPLATE = "Week {week} model pred_updated.csv"

NFLVERSE_GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
NFLVERSE_PBP_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"
)
NFLVERSE_WEEKLY_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats/"
    "player_stats_{season}.parquet"
)
NFLVERSE_TEAMS_URL = (
    "https://github.com/nflverse/nflfastR-data/raw/master/teams_colors_logos.csv"
)

COMBINED_DICTIONARY_PATH = (
    Path(__file__).resolve().parents[1] / "inst" / "extdata" / "combined_data_dictionary.csv"
)
QB_CROSSWALK_PATH = (
    Path(__file__).resolve().parents[1] / "data-raw" / "QB Crosswalk.csv"
)

REQUEST_TIMEOUT = 30
TOP_10 = 10
TOP_5 = 5
TOP_3 = 3

# Edge thresholds. Single source of truth for every bet/lean/pass decision in
# this module. Keep the label functions, has_bet, and build_bottom_line all
# reading from these so the table and the Bottom Line can never disagree.
LEAN_EDGE_THRESHOLD = 0.04  # Minimum model edge to register as a lean.
BET_EDGE_THRESHOLD = 0.06  # Minimum model edge for a full bet.
STRONG_BET_EDGE_THRESHOLD = 0.08  # Minimum model edge for a strong bet.


@dataclass
class EspnDebugEvent:
    team: str
    source: str
    url: str
    failure: str


@dataclass
class StarterInjury:
    player: str
    status: str
    detail: str


@dataclass
class TeamInjuryReport:
    team: str
    starters: List[StarterInjury] = field(default_factory=list)
    debug: List[EspnDebugEvent] = field(default_factory=list)
    status: str = "ok_no_injuries"


@dataclass
class StatContext:
    season: int
    through_week: Optional[int]
    note: str


UnitBattle = namedtuple("UnitBattle", ["kind", "team_a", "team_b", "rank_a", "rank_b", "delta"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="outputs/matchup_articles",
        help="Directory where weekly matchup article outputs will be written.",
    )
    parser.add_argument("--week", type=int)
    parser.add_argument("--season", type=int)
    parser.add_argument("--teams", nargs="*")
    parser.add_argument("--trash-schedule-dir")
    parser.add_argument("--trash-schedule-owner", default=TRASH_SCHEDULE_OWNER)
    parser.add_argument("--trash-schedule-repo", default=TRASH_SCHEDULE_REPO)
    parser.add_argument("--trash-schedule-ref", default=TRASH_SCHEDULE_REF)
    parser.add_argument("--espn-debug", action="store_true")
    return parser.parse_args()


def ordinal(rank: Optional[int]) -> str:
    """Convert a rank to an ordinal string."""
    if rank is None:
        return "—"
    if 10 <= rank % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
    return f"{rank}{suf}"


def display_percent(value: object, decimals: int = 1) -> str:
    """Format a decimal value as a percentage string."""
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value) * 100:.{decimals}f}%"


def format_line(line: object) -> str:
    """Format a betting line."""
    if line is None or pd.isna(line):
        return "PK"
    f = float(line)
    if f > 0:
        return f"+{f:.1f}".rstrip("0").rstrip(".")
    else:
        return f"{f:.1f}".rstrip("0").rstrip(".")


def resolve_edge_numeric(row: pd.Series) -> Optional[float]:
    """Resolve edge_numeric from various possible columns."""
    edge = row.get("best_edge")
    if edge is None or pd.isna(edge):
        edge = row.get("edge_numeric")
    if edge is None or pd.isna(edge):
        return None
    return float(edge)


def edge_confidence_label(edge: Optional[float]) -> str:
    """Translate edge numeric to a confidence label."""
    if edge is None or edge < LEAN_EDGE_THRESHOLD:
        return "Pass"
    if edge < BET_EDGE_THRESHOLD:
        return "Lean"
    if edge < STRONG_BET_EDGE_THRESHOLD:
        return "Bet"
    return "Strong Bet"


def matchup_call_label(edge: Optional[float]) -> str:
    """Translate edge numeric to advice for the matchup table."""
    if edge is None or edge < LEAN_EDGE_THRESHOLD:
        return "No Bet"
    if edge < BET_EDGE_THRESHOLD:
        return "Lean – doesn't meet our edge criteria to fully bet"
    return "Bet"


def clean_qb(name: Optional[str], qb_crosswalk: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Clean and standardize QB names."""
    if not name or not isinstance(name, str):
        return None
    if qb_crosswalk and name in qb_crosswalk:
        return qb_crosswalk[name]
    return name


def get_github_file(
    owner: str, repo: str, path: str, ref: str = "main", timeout: int = REQUEST_TIMEOUT
) -> Optional[str]:
    """Fetch a file from GitHub."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return base64.b64decode(r.json()["content"]).decode("utf-8")
    except Exception as e:
        print(f"Failed to fetch {path} from {owner}/{repo}: {e}")
        return None


def apply_rank_column(metrics: pd.DataFrame, col: str, rank_col: str, ascending: bool = True):
    """Add rank column to metrics dataframe (inplace)."""
    metrics[rank_col] = metrics[col].rank(method="min", ascending=ascending).astype("Int64")


def add_rank_columns(metrics: pd.DataFrame, col: str, rank_col: str, ascending: bool = True):
    """Wrapper for apply_rank_column with both ascending and descending."""
    apply_rank_column(metrics, col, rank_col, ascending=ascending)


def load_nflverse_games(season: int) -> pd.DataFrame:
    """Load NFL games data from nflverse."""
    return pd.read_csv(NFLVERSE_GAMES_URL)


def load_nflverse_pbp(season: int) -> pd.DataFrame:
    """Load NFL play-by-play data from nflverse."""
    return pd.read_parquet(NFLVERSE_PBP_URL.format(season=season))


def load_nflverse_weekly(season: int) -> pd.DataFrame:
    """Load NFL weekly player stats from nflverse."""
    return pd.read_parquet(NFLVERSE_WEEKLY_URL.format(season=season))


def load_nflverse_teams() -> pd.DataFrame:
    """Load NFL team colors and logos from nflverse."""
    return pd.read_csv(NFLVERSE_TEAMS_URL)


def load_injuries_from_espn(team: str, season: int, cache: Dict[str, TeamInjuryReport], debug: bool = False) -> TeamInjuryReport:
    """Fetch injury report from ESPN."""
    if team in cache:
        return cache[team]
    
    # Implementation would load from ESPN
    return TeamInjuryReport(team=team, status="ok_no_injuries")


def build_pbp_metrics(pbp: pd.DataFrame) -> pd.DataFrame:
    """Build offensive and defensive metrics from play-by-play data."""
    offense = pbp[pbp["posteam"] != ""].copy()
    games_played = (
        offense.groupby("posteam")
        .agg(games_played=("game_id", "nunique"))
        .reset_index()
        .rename(columns={"posteam": "team"})
    )

    offense_summary = (
        offense.groupby("posteam")
        .agg(
            off_rush_yards=("rushing_yards", lambda s: s[offense.loc[s.index, "rush"].fillna(0).eq(1)].sum()),
            off_pass_yards=("passing_yards", lambda s: s[offense.loc[s.index, "pass"].fillna(0).eq(1)].sum()),
            off_epa_per_play=("epa", "mean"),
            off_sacks=("sack", "sum"),
        )
        .reset_index()
        .rename(columns={"posteam": "team"})
    )
    offense_summary["off_rush_yards_pg"] = offense_summary["off_rush_yards"] / offense_summary["games_played"]
    offense_summary["off_pass_yards_pg"] = offense_summary["off_pass_yards"] / offense_summary["games_played"]
    offense_summary["off_sacks_pg"] = offense_summary["off_sacks"] / offense_summary["games_played"]

    defense = offense[offense["defteam"] != ""].copy()
    defense_summary = (
        defense.groupby("defteam")
        .agg(
            def_rush_yards_allowed=("rushing_yards", lambda s: s[defense.loc[s.index, "rush"].fillna(0).eq(1)].sum()),
            def_pass_yards_allowed=("passing_yards", lambda s: s[defense.loc[s.index, "pass"].fillna(0).eq(1)].sum()),
            def_epa_allowed=("epa", "mean"),
            def_sacks=("sack", "sum"),
            def_games_played=("game_id", "nunique"),
        )
        .reset_index()
        .rename(columns={"defteam": "team"})
    )
    defense_summary["def_rush_yards_pg_allowed"] = defense_summary["def_rush_yards_allowed"] / defense_summary["def_games_played"]
    defense_summary["def_pass_yards_pg_allowed"] = defense_summary["def_pass_yards_allowed"] / defense_summary["def_games_played"]
    defense_summary["def_sacks_pg"] = defense_summary["def_sacks"] / defense_summary["def_games_played"]

    metrics = games_played.merge(offense_summary, on=["team", "games_played"], how="outer").merge(defense_summary, on="team", how="outer")

    # Special-teams TDs: plays where special==1 and td_team matches posteam
    if "special" in pbp.columns and "td_team" in pbp.columns:
        st_td = (
            pbp[pbp["special"].fillna(0).eq(1) & pbp["td_team"].eq(pbp["posteam"])]
            .groupby("posteam")
            .size()
            .reset_index(name="special_teams_tds")
            .rename(columns={"posteam": "team"})
        )
        metrics = metrics.merge(st_td, on="team", how="left")
        metrics["special_teams_tds"] = metrics["special_teams_tds"].fillna(0).astype(int)
    else:
        metrics["special_teams_tds"] = 0

    add_rank_columns(metrics, "off_rush_yards_pg", "off_rush_rank", True)
    add_rank_columns(metrics, "off_pass_yards_pg", "off_pass_rank", True)
    add_rank_columns(metrics, "off_epa_per_play", "off_epa_rank", True)
    add_rank_columns(metrics, "off_sacks_pg", "off_sacks_rank", False)
    add_rank_columns(metrics, "def_rush_yards_pg_allowed", "def_rush_rank", False)
    add_rank_columns(metrics, "def_pass_yards_pg_allowed", "def_pass_rank", False)
    add_rank_columns(metrics, "def_epa_allowed", "def_epa_rank", False)
    add_rank_columns(metrics, "def_sacks_pg", "def_sacks_rank", True)
    return metrics


def build_records(games: pd.DataFrame, season: int, week: int) -> Dict[str, Dict[str, int]]:
    records: Dict[str, Dict[str, int]] = {}
    history = games[
        (games["season"] == season) & (games["game_type"] == "REG") & (games["week"] < week)
    ]
    for team in pd.concat([history["home_team"], history["away_team"]]).unique():
        wins = len(history[(history["home_team"] == team) & (history["result"] > 0)]) + len(
            history[(history["away_team"] == team) & (history["result"] < 0)]
        )
        losses = len(history[(history["home_team"] == team) & (history["result"] < 0)]) + len(
            history[(history["away_team"] == team) & (history["result"] > 0)]
        )
        ties = len(history[(history["home_team"] == team) & (history["result"] == 0)]) + len(
            history[(history["away_team"] == team) & (history["result"] == 0)]
        )
        records[team] = {"W": wins, "L": losses, "T": ties}
    return records


def model_ranks(model_frame: pd.DataFrame) -> pd.DataFrame:
    """Convert model metrics into rank columns."""
    metrics = model_frame.copy()
    if metrics.empty:
        return metrics
    metrics = metrics.rename(
        columns={
            "Team": "team",
            "Offensive Expected Points (Season)": "off_ep",
            "Defensive Expected Points (Season)": "def_ep",
            "Offensive Success Rate (%)": "off_sr",
            "Defensive Success Rate (%)": "def_sr",
            "QB Expected Points Added (Last 10 games)": "qb10_ep",
            "Offensive Eckel Rate Over Expected (%)": "off_eckel",
            "Defensive Eckel Rate Over Expected (%)": "def_eckel",
            "Qbname": "qbname",
        }
    )
    apply_rank_column(metrics, "off_ep", "off_sr_rank", ascending=False)
    apply_rank_column(metrics, "def_ep", "def_sr_rank", ascending=False)
    apply_rank_column(metrics, "qb10_ep", "qb10_rank", ascending=False)
    return metrics[
        ["team", "off_sr_rank", "def_sr_rank", "qb10_rank", "qbname", "off_eckel", "def_eckel"]
    ]


def _safe_rank(metrics: pd.Series, col: str) -> Optional[int]:
    val = metrics.get(col) if hasattr(metrics, "get") else None
    if val is None or pd.isna(val):
        return None
    return int(val)


def _ord(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def assumed_starters(bet_name, bet_m, opp_name, opp_m, qb_crosswalk=None) -> Optional[str]:
    a = clean_qb(bet_m.get("qbname"), qb_crosswalk)
    b = clean_qb(opp_m.get("qbname"), qb_crosswalk)
    if not a and not b:
        return None
    parts = []
    if a:
        parts.append(f"{a} ({bet_name})")
    if b:
        parts.append(f"{b} ({opp_name})")
    return f"*Model assumes {' and '.join(parts)} under center. QB news moves these numbers fast, so check inactives before you bet.*"


def build_tale_of_tape(bet_name, bet_m, opp_name, opp_m, total_teams, qb_crosswalk=None) -> List[str]:
    def cell_rank(mm, col):
        r = _safe_rank(mm, col)
        return _ord(r) if r else "—"

    def cell_pct(mm, col):
        v = mm.get(col) if hasattr(mm, "get") else None
        return display_percent(v, 1) if v is not None and not pd.isna(v) else "—"

    def cell_qb(mm):
        name = clean_qb(mm.get("qbname"), qb_crosswalk)
        r = _safe_rank(mm, "qb10_rank")
        if name and r:
            return f"{name} ({_ord(r)})"
        return name or "—"

    rows = [
        ("QB Efficiency (Last 10 Games)", lambda mm: cell_qb(mm)),
        ("Offensive Success Rate", lambda mm: cell_rank(mm, "off_sr_rank")),
        ("Defensive Success Rate", lambda mm: cell_rank(mm, "def_sr_rank")),
        ("Offensive Eckel Rate Over Expected*", lambda mm: cell_pct(mm, "off_eckel")),
        ("Defensive Eckel Rate Over Expected", lambda mm: cell_pct(mm, "def_eckel")),
    ]
    body = []
    for label, fn in rows:
        bcell, ocell = fn(bet_m), fn(opp_m)
        if bcell == "—" and ocell == "—":
            continue
        body.append(f"| {label} | {bcell} | {ocell} |")
    if not body:
        return []
    return [f"| | {bet_name} | {opp_name} |", "|---|---|---|"] + body


def _price(value: object) -> str:
    if value is None or pd.isna(value):
        return "-110"
    f = float(value)
    return f"{f:+.0f}" if f > 0 else f"{f:.0f}"


def _side_facts(row: pd.Series) -> Dict[str, object]:
    cover = row.get("best_cover_probability")
    if cover is None or pd.isna(cover):
        cover = row.get("model_cover_probability")
    edge = row.get("best_edge")
    if edge is None or pd.isna(edge):
        edge = row.get("edge_numeric")
    return {"cover": cover, "edge": edge, "line": row.get("best_line"), "price": row.get("best_price")}


def extract_team_logo(row: pd.Series) -> Optional[str]:
    """Return the best available team logo URL from a row."""
    espn = row.get("team_logo_espn")
    if espn is not None and not (isinstance(espn, float) and math.isnan(espn)):
        return str(espn)
    logo = row.get("logo")
    if logo is not None and not (isinstance(logo, float) and math.isnan(logo)):
        return str(logo)
    return None


def render_logo_row(away_name: str, away_logo: str, home_name: str, home_logo: str) -> str:
    """Render a borderless HTML table with team logos flanking a vs separator."""
    html = (
        f'<p align="center">'
        f'<img src="{away_logo}" alt="{away_name}" width="224" /> '
        f"<strong>vs</strong> "
        f'<img src="{home_logo}" alt="{home_name}" width="224" />'
        f"</p>"
    )
    return html


def model_vs_market_lead(team_name: str, market_line: float, best_line: float, seed: str) -> Optional[str]:
    """Return a one-line model-vs-market lead sentence (no em-dash)."""
    def _fmt(v: float) -> str:
        s = f"{v:+.1f}" if v >= 0 else f"{v:.1f}"
        return s.rstrip("0").rstrip(".")

    return (
        f"the model favors **the {team_name}** at {_fmt(best_line)}"
        f" vs. the market at {_fmt(market_line)}."
    )


def build_bottom_line(
    away_name: str,
    home_name: str,
    stadium_name: Optional[str],
    bet_name: str,
    bet_line: str,
    confidence: str,
    bet_facts: Dict[str, object],
    seed: str,
    has_bet: bool,
    model_lead: Optional[str],
) -> List[str]:
    """Build the Bottom Line section as a list of markdown lines.

    MANDATORY RULES enforced here for every article:
      1. The numeric edge is ALWAYS stated when discussing whether to bet a
         team. Every return path below includes ``edge_pct``.
      2. A team we are passing on is NEVER re-referenced as a "closest look"
         (or any equivalent redundant callout) in a trailing sentence.

    Tiering is driven by the same thresholds as ``matchup_call_label`` so the
    Bottom Line can never contradict the summary table two paragraphs above.
    """
    stadium = stadium_name or "their home stadium"

    # Robust edge extraction: treat None / NaN as 0.0 so we never print "nan%".
    raw_edge = bet_facts.get("edge")
    edge = float(raw_edge) if raw_edge is not None and not pd.isna(raw_edge) else 0.0
    edge_pct = f"{edge * 100:.2f}%"

    price = bet_facts.get("price")
    price_str = str(int(price)) if price is not None and not pd.isna(price) else "N/A"

    intro_prefix = f"The {away_name} take on the {home_name} at {stadium} and"

    if edge >= BET_EDGE_THRESHOLD:
        # Full bet.
        lead = model_lead or f"the model likes {bet_name} {bet_line}."
        # Ensure lead starts with lowercase to read naturally after "and ".
        if lead and lead[0].isupper():
            lead = lead[0].lower() + lead[1:]
        intro = f"{intro_prefix} {lead}"
        edge_line = (
            f"This puts the edge at {edge_pct}, which at {bet_line} for {price_str}"
            f" clears our {BET_EDGE_THRESHOLD * 100:.0f}% full-bet threshold and makes"
            f" the {bet_name} a bet."
        )
        return ["## The Bottom Line", intro, edge_line]

    if edge >= LEAN_EDGE_THRESHOLD:
        # Lean: a real edge, but short of a full bet. State the edge; no
        # "closest look" restatement of the side we are not fully backing.
        text = (
            f"{intro_prefix} the model leans {bet_name} {bet_line} with an edge of"
            f" {edge_pct}. That is short of our {BET_EDGE_THRESHOLD * 100:.0f}%"
            f" full-bet threshold, so this is a lean, not a play."
        )
        return ["## The Bottom Line", text]

    # Pass: edge below the lean floor. State the edge; no "closest look".
    text = (
        f"{intro_prefix} the model's slight lean is {bet_name} {bet_line}, but at an"
        f" edge of {edge_pct} it does not clear our {LEAN_EDGE_THRESHOLD * 100:.0f}%"
        f" minimum, so we are passing on this one."
    )
    return ["## The Bottom Line", text]


def build_cta(edge_game_count: int, has_bet: bool) -> List[str]:
    """Build the Best Bets of the Week CTA section."""
    lines = ["", "## Best Bets Of The Week", ""]
    if edge_game_count > 0:
        lines.append(
            f"Our model found edges of at least 4% on **{edge_game_count} game{'s' if edge_game_count != 1 else ''}** this week. See the model output for every NFL and CFB game at btb-analytics.com/member-access."
        )
    lines.append("")
    lines.append("<p align='center'><em>Built by the BTB model. We target a 55-57% win rate and publish every result, wins and losses.</em></p>")
    return lines


def render_risk(
    risk_tuple: Tuple[str, "UnitBattle"],
    opp_name: str,
    total_teams: int,
    seed: str,
) -> str:
    """Render a risk callout line (no em-dash)."""
    risk_name, ub = risk_tuple
    if ub.rank_a is None or ub.rank_b is None:
        return None
    
    if ub.delta < 0:
        return f"**{risk_name} Edge**: {opp_name} has a {_ord(ub.rank_b)} {ub.kind}, vs {_ord(ub.rank_a)} for {ub.team_a}."
    else:
        return f"**{risk_name} Edge**: {ub.team_a} has a {_ord(ub.rank_a)} {ub.kind}, vs {_ord(ub.rank_b)} for {opp_name}."


def build_article(
    seed: str,
    game_rows: Sequence[pd.Series],
    metrics: pd.DataFrame,
    records: Dict[str, Dict[str, int]],
    team_names: Dict[str, str],
    mr: Optional[pd.DataFrame],
    stat_context: StatContext,
    qb_crosswalk: Dict[str, str],
    injuries: Dict[str, TeamInjuryReport],
    edge_game_count: int,
    model_ranks_df: Optional[pd.DataFrame] = None,
) -> Tuple[str, Optional[str]]:
    """Build a single matchup article."""
    if not game_rows or len(game_rows) < 2:
        return None, None

    away_row, home_row = game_rows[0], game_rows[1]
    away_team = away_row.get("away_team")
    home_team = home_row.get("home_team")

    away_name = team_names.get(away_team, away_team)
    home_name = team_names.get(home_team, home_team)

    # Extract required fields
    kickoff = away_row.get("kickoff_time")
    kickoff_title_label = None
    if kickoff:
        try:
            dt = pd.to_datetime(kickoff)
            kickoff_title_label = dt.strftime("%m/%d/%Y")
        except:
            pass

    stadium_name = away_row.get("location") or home_row.get("location")

    verdict_row = away_row if away_row.get("edge_numeric") or away_row.get("best_edge") else home_row
    bet_facts = _side_facts(verdict_row)
    bet_line = format_line(verdict_row.get("best_line"))

    sections: List[str] = [f"# {away_name} vs {home_name} Prediction For {kickoff_title_label}", ""]

    # Logo row (team logos underneath title)
    away_logo = extract_team_logo(away_row)
    home_logo = extract_team_logo(home_row)
    if mr is not None and away_logo and home_logo:
        sections.append(
            f'<p align="center">'
            f'<img src="{away_logo}" alt="{away_name}" width="224" /> '
            f'<strong>vs</strong> '
            f'<img src="{home_logo}" alt="{home_name}" width="224" />'
            f'</p>'
        )
        sections.append("")

    has_bet = (resolve_edge_numeric(verdict_row) or 0) >= BET_EDGE_THRESHOLD

    bet_name = away_name if verdict_row.equals(away_row) else home_name
    opp_name = home_name if bet_name == away_name else away_name

    bet_m = model_ranks_df[model_ranks_df["team"] == away_team].iloc[0] if (model_ranks_df is not None and not model_ranks_df.empty) else None
    opp_m = model_ranks_df[model_ranks_df["team"] == home_team].iloc[0] if (model_ranks_df is not None and not model_ranks_df.empty) else None

    # BTB Analytics subheader with logo (underneath team logos, above table)
    sections.append("<p align='center'><img src='https://raw.githubusercontent.com/trashduty/football-testgrounds/main/BTB%20Analytics%20.png.png' alt='BTB Analytics' width='100' /><br/><em>Brought to you by BTB Analytics</em></p>")
    sections.append("")

    # Summary table
    matchup_rows = []
    for row, team_name in ((away_row, away_name), (home_row, home_name)):
        edge = resolve_edge_numeric(row)
        cover = row.get("best_cover_probability")
        if cover is None or pd.isna(cover):
            cover = row.get("model_cover_probability")
        matchup_rows.append(
            (
                team_name,
                f"{format_line(row.get('best_line'))} ({_price(row.get('best_price'))})",
                row.get("best_book") or "N/A",
                display_percent(cover, 1),
                matchup_call_label(edge),
            )
        )
    sections.extend(
        [
            "| Team name | Best Spread/Odds | Best Book | Cover Probability | BTB Advice |",
            "|---|---|---|---|---|",
        ]
    )
    sections.extend(
        [f"| {team} | {spread_odds} | {book} | {cover} | {call} |"
         for team, spread_odds, book, cover, call in matchup_rows]
    )

    if mr is not None:
        # ── New-format article ────────────────────────────────────────────────
        if not has_bet:
            sections.extend([
                "",
                "The edge here does not clear our"
                f" {BET_EDGE_THRESHOLD * 100:.0f}% full-bet threshold, so there is no play.",
            ])

        starters_note = assumed_starters(bet_name, bet_m, opp_name, opp_m, qb_crosswalk=qb_crosswalk)
        if starters_note:
            sections.extend(["", starters_note])

        model_lead = model_vs_market_lead(
            bet_name,
            float(verdict_row.get("market_line") or 0),
            float(verdict_row.get("best_line") or 0),
            seed,
        )
        bottom_lines = build_bottom_line(
            away_name=away_name,
            home_name=home_name,
            stadium_name=stadium_name,
            bet_name=bet_name,
            bet_line=bet_line,
            confidence=edge_confidence_label(resolve_edge_numeric(verdict_row)),
            bet_facts=bet_facts,
            seed=seed,
            has_bet=has_bet,
            model_lead=model_lead,
        )
        sections.extend([""] + bottom_lines)

        sections.extend([
            "",
            "## Why The Pick",
            "",
            "Our model uses data points that correlate best with a team covering."
            " Here's how these two teams stack up in some of those categories",
            "",
        ])

        tape = build_tale_of_tape(bet_name, bet_m, opp_name, opp_m, len(model_ranks_df) if model_ranks_df is not None else 0, qb_crosswalk=qb_crosswalk)
        if tape:
            sections.extend(tape)
            sections.extend([
                "",
                "\\*The rate of possessions that result in a big play touchdown or 1st down"
                " inside the opponent's 40 yard line",
            ])

        sections.extend(build_cta(edge_game_count, has_bet))

    else:
        # ── Old-format article ────────────────────────────────────────────────
        starters_note = assumed_starters(bet_name, bet_m, opp_name, opp_m, qb_crosswalk=qb_crosswalk)
        if starters_note:
            sections.extend(["", starters_note])

        sections.extend(["", "## Verdict", ""])
        sections.extend(["", "## The Why", ""])
        sections.extend(["", "## The Mismatch", ""])
        sections.extend(["", "## The Number", ""])
        sections.extend(["", "## The Risk", ""])

    return "\n".join(sections), seed


def main():
    args = parse_args()
    print("Build article complete")


if __name__ == "__main__":
    main()
