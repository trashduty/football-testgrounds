#!/usr/bin/env python3
"""College Football team stats for the weekly matchup articles.

Uses the official CFBD Python library (`cfbd`, v5.x). Auth is a Bearer token in
env / GitHub secret CFBD_API_KEY. All field paths below were verified against
the installed cfbd model classes (snake_case), so there is no field-name
guessing left:

  EPA (per game): AdvancedGameStat.offense.passing_plays.ppa / rushing_plays.ppa
                  (and .defense.* for the allowed side)
  Eckel (drives): Drive.scoring / start_yards_to_goal / end_yards_to_goal
  Venues (games): Game.home_id / away_id / venue / neutral_site / week
  Team ids:       Team.id / school  (bridges CFBD school name -> team_id)

Design decisions locked with the user:
  1. Pull directly from CFBD in Python.
  2. Show EPA (CFBD PPA) for pass/rush on offense and defense, plus eckel rate;
     success rate dropped.
  3. EPA and eckel shown as FBS ranks.
  4. Each stat is a rolling last-10-games average per team, crossing the season
     boundary (2026 week 1 uses the last 10 games of 2025).

SCOPE NOTE: these are RAW CFBD values (unadjusted per-game PPA + a raw eckel
rate from drives), NOT your R model's "over expected"/"weighted" columns. Use
load_team_stats_csv() instead if you want those exact figures.

TESTING: the fetch_* functions hit the live API and are not exercised here (no
key in the build sandbox). Their field access is validated against the real
cfbd classes. The pure functions (rolling, ranking, eckel math, venue lookup,
rendering, CSV read) are unit-tested.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

import cfbd
from cfbd.models.season_type import SeasonType

ROLLING_GAMES = 10
ECKEL_YARDS_TO_GOAL = 40  # reaching the opponent's 40 = a scoring opportunity
BOTH = SeasonType("both")  # regular + postseason, so bowl games fill the window

LOGO_ID_RE = re.compile(r"/500/(\d+)\.png")

# The six article stats. Each: (internal_name, display_label, higher_is_better).
STAT_SPECS: List[Tuple[str, str, bool]] = [
    ("off_pass_epa", "Offensive Pass EPA", True),
    ("off_rush_epa", "Offensive Rush EPA", True),
    ("def_pass_epa", "Defensive Pass EPA", False),
    ("def_rush_epa", "Defensive Rush EPA", False),
    ("off_eckel", "Offensive Eckel Rate", True),
    ("def_eckel", "Defensive Eckel Rate", False),
]
STAT_COLS = [name for name, _, _ in STAT_SPECS]


# --------------------------------------------------------------------------- #
# Shared helpers (pure)
# --------------------------------------------------------------------------- #
def _ordinal(rank: object) -> str:
    if rank is None or pd.isna(rank):
        return "unranked"
    rank = int(rank)
    suffix = "th" if 10 <= rank % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
    return f"{rank}{suffix}"


def team_id_from_logo(url: object) -> Optional[int]:
    if not isinstance(url, str):
        return None
    match = LOGO_ID_RE.search(url)
    return int(match.group(1)) if match else None


def load_crosswalk(path: str) -> pd.DataFrame:
    cw = pd.read_csv(path)
    cw["team_id"] = cw["team_id"].astype("Int64")
    return cw


# --------------------------------------------------------------------------- #
# Ranking + rendering (pure; unit-tested)
# --------------------------------------------------------------------------- #
def rank_team_stats(stats: pd.DataFrame) -> pd.DataFrame:
    """Add <name>_rank for each stat. Rank 1 = best; defense inverts."""
    ranked = stats.copy()
    for name, _, higher_is_better in STAT_SPECS:
        if name in ranked.columns:
            ranked[f"{name}_rank"] = (
                ranked[name].rank(ascending=not higher_is_better, method="min").astype("Int64")
            )
    return ranked


def build_cfb_tale_of_tape(
    bet_id: int, opp_id: int, ranked: pd.DataFrame, crosswalk: pd.DataFrame
) -> List[str]:
    """Six-row markdown 'Why The Pick' table of FBS ranks for the two teams."""
    names = crosswalk.set_index("team_id")["btb_team"].to_dict()
    ranked_by_id = ranked.dropna(subset=["team_id"]).set_index("team_id")

    def cell(team_id: int, name: str) -> str:
        if team_id not in ranked_by_id.index:
            return "unranked"
        return _ordinal(ranked_by_id.loc[team_id].get(f"{name}_rank"))

    bet_name = names.get(bet_id, str(bet_id))
    opp_name = names.get(opp_id, str(opp_id))
    rows = [f"| | {bet_name} | {opp_name} |", "|---|---|---|"]
    for name, label, _ in STAT_SPECS:
        rows.append(f"| {label} | {cell(bet_id, name)} | {cell(opp_id, name)} |")
    return rows


# --------------------------------------------------------------------------- #
# Rolling window + eckel math (pure; unit-tested)
# --------------------------------------------------------------------------- #
def rolling_last_n_games(game_level: pd.DataFrame, n: int = ROLLING_GAMES) -> pd.DataFrame:
    """Collapse per-(team, season, week) rows to one row per team = mean of the
    team's most recent n games (ordered by season then week)."""
    df = game_level.sort_values(["team", "season", "week"])
    recent = df.groupby("team", group_keys=False).tail(n)
    present = [c for c in STAT_COLS if c in recent.columns]
    return recent.groupby("team")[present].mean().reset_index()


def compute_game_eckel(drives: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Per-(team, season, week) eckel rate from drives, offense and defense.

    A drive is an eckel drive if it scored or reached the opponent's 40 (start
    or end yards-to-goal <= ECKEL_YARDS_TO_GOAL). Proxy for Connelly's scoring
    opportunity; exact needs /plays. season/week attached via game_id -> games.
    """
    gmap = games[["game_id", "season", "week"]].drop_duplicates()
    d = drives.merge(gmap, on="game_id", how="left")
    start = pd.to_numeric(d["start"], errors="coerce")
    end = pd.to_numeric(d["end"], errors="coerce")
    d["eckel"] = (d["scoring"] | (start <= ECKEL_YARDS_TO_GOAL) | (end <= ECKEL_YARDS_TO_GOAL)).astype(int)
    off = (d.groupby(["offense", "season", "week"])["eckel"].mean()
           .rename("off_eckel").reset_index().rename(columns={"offense": "team"}))
    deff = (d.groupby(["defense", "season", "week"])["eckel"].mean()
            .rename("def_eckel").reset_index().rename(columns={"defense": "team"}))
    return off.merge(deff, on=["team", "season", "week"], how="outer")


def build_venue_lookup(games: pd.DataFrame) -> Dict[Tuple[frozenset, int], str]:
    """Map (frozenset{home_id, away_id}, week) -> venue name."""
    lut: Dict[Tuple[frozenset, int], str] = {}
    for _, g in games.iterrows():
        if pd.isna(g.get("home_id")) or pd.isna(g.get("away_id")):
            continue
        lut[(frozenset([int(g["home_id"]), int(g["away_id"])]), int(g["week"]))] = g.get("venue")
    return lut


def build_team_stats(
    epa: pd.DataFrame, eckel: pd.DataFrame, teams: pd.DataFrame,
    crosswalk: pd.DataFrame, n: int = ROLLING_GAMES,
) -> pd.DataFrame:
    """Rolling last-n EPA + eckel, bridged CFBD school -> team_id -> btb_team,
    then ranked. `teams` maps school ('team') to team_id."""
    game_level = epa.merge(eckel, on=["team", "season", "week"], how="outer")
    rolled = rolling_last_n_games(game_level, n=n)
    keyed = rolled.merge(teams, on="team", how="left")
    keyed = keyed.merge(crosswalk[["team_id", "btb_team"]], on="team_id", how="left")
    return rank_team_stats(keyed)


# --------------------------------------------------------------------------- #
# LIVE CFBD fetchers (official library; field ACCESS validated, live calls not)
# --------------------------------------------------------------------------- #
def make_client(api_key: Optional[str] = None) -> cfbd.ApiClient:
    key = api_key or os.getenv("CFBD_API_KEY")
    if not key:
        raise RuntimeError("CFBD_API_KEY not set (env var / GitHub secret) and no api_key passed.")
    return cfbd.ApiClient(cfbd.Configuration(access_token=key))


def _ppa(side: object, plays_attr: str) -> Optional[float]:
    sub = getattr(side, plays_attr, None) if side is not None else None
    return getattr(sub, "ppa", None) if sub is not None else None


def fetch_fbs_team_ids(year: int, client: cfbd.ApiClient) -> pd.DataFrame:
    teams = cfbd.TeamsApi(client).get_fbs_teams(year=year)
    return pd.DataFrame([{"team": t.school, "team_id": t.id} for t in teams])


def fetch_game_epa(years: List[int], client: cfbd.ApiClient) -> pd.DataFrame:
    api = cfbd.StatsApi(client)
    rows = []
    for year in years:
        for g in api.get_advanced_game_stats(year=year, exclude_garbage_time=True, season_type=BOTH):
            off, deff = g.offense, g.defense
            rows.append({
                "team": g.team, "season": g.season, "week": g.week,
                "off_pass_epa": _ppa(off, "passing_plays"),
                "off_rush_epa": _ppa(off, "rushing_plays"),
                "def_pass_epa": _ppa(deff, "passing_plays"),
                "def_rush_epa": _ppa(deff, "rushing_plays"),
            })
    return pd.DataFrame(rows)


def fetch_games(years: List[int], client: cfbd.ApiClient) -> pd.DataFrame:
    api = cfbd.GamesApi(client)
    rows = []
    for year in years:
        for g in api.get_games(year=year, season_type=BOTH):
            rows.append({
                "game_id": g.id, "season": g.season, "week": g.week,
                "home_id": g.home_id, "away_id": g.away_id,
                "venue": g.venue, "neutral_site": g.neutral_site,
            })
    return pd.DataFrame(rows)


def fetch_drives(years: List[int], client: cfbd.ApiClient) -> pd.DataFrame:
    api = cfbd.DrivesApi(client)
    rows = []
    for year in years:
        for d in api.get_drives(year=year, season_type=BOTH):
            rows.append({
                "game_id": d.game_id, "offense": d.offense, "defense": d.defense,
                "scoring": bool(d.scoring),
                "start": d.start_yards_to_goal, "end": d.end_yards_to_goal,
            })
    return pd.DataFrame(rows)


def build_cfb_stats_and_venues(
    years: List[int], crosswalk: pd.DataFrame, api_key: Optional[str] = None,
    n: int = ROLLING_GAMES,
) -> Tuple[pd.DataFrame, Dict[Tuple[frozenset, int], str]]:
    """End-to-end: (ranked team stats keyed by team_id, venue lookup).

    `years` should span enough seasons to fill the rolling window (e.g.
    [season-1, season]). One call each per year for teams/epa/games/drives.
    """
    with make_client(api_key) as client:
        teams = fetch_fbs_team_ids(max(years), client)
        epa = fetch_game_epa(years, client)
        games = fetch_games(years, client)
        drives = fetch_drives(years, client)
    eckel = compute_game_eckel(drives, games)
    stats = build_team_stats(epa, eckel, teams, crosswalk, n=n)
    return stats, build_venue_lookup(games)


# --------------------------------------------------------------------------- #
# CSV path (alternative; use if you want your R model's exact stats instead)
# --------------------------------------------------------------------------- #
def load_team_stats_csv(path: str, crosswalk: pd.DataFrame) -> pd.DataFrame:
    """Read a Team-Data-Table-style CSV, map columns to STAT_COLS, rank, key by
    team_id (Team -> crosswalk.btb_team_short). Uses the model's 'Eckel Rate
    Over Expected' columns as the eckel stat."""
    td = pd.read_csv(path)
    colmap = {
        "Offensive Pass EPA": "off_pass_epa",
        "Offensive Rush EPA": "off_rush_epa",
        "Defensive Pass EPA": "def_pass_epa",
        "Defensive Rush EPA": "def_rush_epa",
        "Offensive Eckel Rate Over Expected (%)": "off_eckel",
        "Defensive Eckel Rate Over Expected (%)": "def_eckel",
    }
    td = td.rename(columns=colmap)
    short_to_id = crosswalk.dropna(subset=["btb_team_short"]).set_index("btb_team_short")["team_id"].to_dict()
    td["team_id"] = td["Team"].map(short_to_id).astype("Int64")
    keyed = td.merge(crosswalk[["team_id", "btb_team"]], on="team_id", how="left")
    return rank_team_stats(keyed)
