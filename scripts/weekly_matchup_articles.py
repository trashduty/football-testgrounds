#!/usr/bin/env python3
"""Generate weekly NFL matchup articles from odds, model, nflverse, and ESPN data."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
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

REQUEST_TIMEOUT = 30
TOP_10 = 10
TOP_5 = 5
TOP_3 = 3


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
    # Always-populated status that distinguishes outcome even without --espn-debug:
    # "ok_starters_found" | "ok_no_injuries" | "no_slug" | "injury_fetch_failed"
    # | "injury_parse_failed" | "depth_fetch_failed" | "depth_parse_failed"
    # | "no_starter_match"
    status: str = "ok_no_injuries"


@dataclass
class StatContext:
    season: int
    through_week: Optional[int]
    note: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="outputs/matchup_articles",
        help="Directory where weekly matchup article outputs will be written.",
    )
    parser.add_argument(
        "--week",
        type=int,
        help="NFL week to generate. Defaults to the latest week in spreads_odds.csv.",
    )
    parser.add_argument(
        "--season",
        type=int,
        help="NFL season for record/stat context. Defaults to the year in spreads_odds.csv.",
    )
    parser.add_argument(
        "--teams",
        nargs="*",
        help="Optional team abbreviations to limit output to matching games.",
    )
    parser.add_argument(
        "--trash-schedule-dir",
        help="Optional local checkout of trashduty/trash-schedule to read source CSVs from.",
    )
    parser.add_argument(
        "--trash-schedule-owner",
        default=TRASH_SCHEDULE_OWNER,
        help="GitHub owner for the trash-schedule source repository.",
    )
    parser.add_argument(
        "--trash-schedule-repo",
        default=TRASH_SCHEDULE_REPO,
        help="GitHub repository name for the trash-schedule source repository.",
    )
    parser.add_argument(
        "--trash-schedule-ref",
        default=TRASH_SCHEDULE_REF,
        help="Git ref to use when reading files from trash-schedule.",
    )
    parser.add_argument(
        "--espn-debug",
        action="store_true",
        help="Include exact ESPN URLs and exact failure reasons when injury/depth lookups fail.",
    )
    return parser.parse_args()


def ordinal(rank: Optional[int]) -> str:
    if rank is None or math.isnan(rank):
        return "unranked"
    rank = int(rank)
    if 10 <= rank % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
    return f"{rank}{suffix}"


def normalize_name(value: str) -> str:
    value = re.sub(r"\s+\(.*?\)$", "", str(value or "")).strip()
    value = re.sub(r"\s+(Jr\.?|Sr\.?|II|III|IV|V)$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[^A-Za-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip().lower()


def slugify_game(game: str) -> str:
    return game.lower().replace("@", "_at_")


def format_float(value: object, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value):.{digits}f}"


def parse_percent(value: object) -> float:
    if value is None or pd.isna(value):
        return float("nan")
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.endswith("%"):
            return float(stripped.rstrip("%")) / 100.0
        value = stripped
    numeric = float(value)
    return numeric / 100.0 if numeric > 1 else numeric


def display_percent(value: object, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    numeric = float(value)
    if abs(numeric) <= 1:
        numeric *= 100
    return f"{numeric:.{digits}f}%"


def display_edge(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value) * 100:.2f}%"


def safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def raw_github_url(owner: str, repo: str, ref: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"


def github_contents_url(owner: str, repo: str, ref: str, path: str) -> str:
    return f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"


def fetch_text(
    path: str,
    *,
    local_root: Optional[Path] = None,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    ref: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> str:
    if local_root is not None:
        local_path = local_root / path
        return local_path.read_text(encoding="utf-8")

    if not owner or not repo or not ref:
        raise ValueError("owner, repo, and ref are required for remote fetches")

    session = session or requests.Session()
    raw_url = raw_github_url(owner, repo, ref, path)
    response = session.get(raw_url, timeout=REQUEST_TIMEOUT)
    if response.ok:
        response.encoding = response.encoding or "utf-8"
        return response.text

    token = session.headers.get("Authorization") or (
        f"token {token}" if (token := os.getenv("GITHUB_TOKEN")) else None
    )
    if token:
        headers = {"Accept": "application/vnd.github+json", "Authorization": token}
        api_response = session.get(
            github_contents_url(owner, repo, ref, path),
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        api_response.raise_for_status()
        payload = api_response.json()
        content = payload.get("content")
        if content:
            return base64.b64decode(content).decode("utf-8")

    response.raise_for_status()
    return response.text


def read_repo_csv(
    path: str,
    *,
    local_root: Optional[Path] = None,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    ref: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    text = fetch_text(
        path,
        local_root=local_root,
        owner=owner,
        repo=repo,
        ref=ref,
        session=session,
    )
    return pd.read_csv(StringIO(text))


def load_spreads_and_target_context(
    args: argparse.Namespace,
    session: requests.Session,
) -> Tuple[pd.DataFrame, int, int, Optional[Path]]:
    local_root = Path(args.trash_schedule_dir).resolve() if args.trash_schedule_dir else None
    spreads = read_repo_csv(
        TRASH_SCHEDULE_SPREADS_PATH,
        local_root=local_root,
        owner=args.trash_schedule_owner,
        repo=args.trash_schedule_repo,
        ref=args.trash_schedule_ref,
        session=session,
    )
    spreads.columns = spreads.columns.str.strip().str.lower()
    spreads["team"] = spreads["team"].str.upper()
    spreads["week"] = spreads["week"].astype(int)
    spreads["game_date_est"] = pd.to_datetime(spreads["game_date_est"], errors="coerce")

    week = args.week or int(spreads["week"].max())
    week_spreads = spreads[spreads["week"] == week].copy()
    if week_spreads.empty:
        raise ValueError(f"No spreads_odds.csv rows found for week {week}")

    season = args.season or int(week_spreads["game_date_est"].dt.year.mode().iloc[0])
    return week_spreads, week, season, local_root


def load_model_data(
    week: int,
    args: argparse.Namespace,
    local_root: Optional[Path],
    session: requests.Session,
) -> pd.DataFrame:
    model_path = TRASH_SCHEDULE_MODEL_TEMPLATE.format(week=week)
    model = read_repo_csv(
        model_path,
        local_root=local_root,
        owner=args.trash_schedule_owner,
        repo=args.trash_schedule_repo,
        ref=args.trash_schedule_ref,
        session=session,
    )
    model.columns = model.columns.str.strip()
    model["Team"] = model["Team"].str.upper()
    model["model_cover_probability"] = model["Cover Probability (%)"].map(parse_percent)
    model["edge_numeric"] = model["Edge"].astype(float)
    return model


def load_games_schedule() -> pd.DataFrame:
    games = pd.read_csv(NFLVERSE_GAMES_URL)
    games["game_type"] = games["game_type"].fillna("")
    games["home_team"] = games["home_team"].str.upper()
    games["away_team"] = games["away_team"].str.upper()
    return games


def load_team_names() -> Dict[str, str]:
    teams = pd.read_csv(NFLVERSE_TEAMS_URL)
    return dict(zip(teams["team_abbr"].str.upper(), teams["team_name"]))


def read_remote_parquet(url: str, columns: Sequence[str]) -> pd.DataFrame:
    return pd.read_parquet(url, columns=list(columns))


def choose_stat_context(target_season: int, target_week: int) -> StatContext:
    if target_week <= 1:
        return StatContext(
            season=target_season - 1,
            through_week=None,
            note=(
                f"Week {target_week} has no prior in-season nflverse sample yet, so matchup stats "
                f"fall back to the {target_season - 1} regular season."
            ),
        )
    return StatContext(
        season=target_season,
        through_week=target_week - 1,
        note=f"Matchup stats use {target_season} regular-season data through week {target_week - 1}.",
    )


def load_stat_inputs(stat_context: StatContext) -> Tuple[pd.DataFrame, pd.DataFrame]:
    pbp_columns = [
        "game_id",
        "season",
        "week",
        "season_type",
        "posteam",
        "defteam",
        "home_team",
        "away_team",
        "pass",
        "rush",
        "sack",
        "passing_yards",
        "rushing_yards",
        "epa",
        "field_goal_attempt",
        "field_goal_result",
        "kick_distance",
        "kicker_player_name",
        "touchdown",
        "special",
        "td_team",
    ]
    weekly_columns = ["season", "week", "season_type", "recent_team", "special_teams_tds"]

    pbp = read_remote_parquet(NFLVERSE_PBP_URL.format(season=stat_context.season), pbp_columns)
    try:
        weekly = read_remote_parquet(
            NFLVERSE_WEEKLY_URL.format(season=stat_context.season), weekly_columns
        )
    except Exception:  # noqa: BLE001
        weekly = pd.DataFrame(columns=weekly_columns)

    pbp["posteam"] = pbp["posteam"].fillna("").str.upper()
    pbp["defteam"] = pbp["defteam"].fillna("").str.upper()
    pbp["home_team"] = pbp["home_team"].fillna("").str.upper()
    pbp["away_team"] = pbp["away_team"].fillna("").str.upper()
    pbp["season_type"] = pbp["season_type"].fillna("")
    pbp = pbp[pbp["season_type"] == "REG"].copy()
    if stat_context.through_week is not None:
        pbp = pbp[pbp["week"] <= stat_context.through_week].copy()

    if not weekly.empty:
        weekly["recent_team"] = weekly["recent_team"].fillna("").str.upper()
        weekly["season_type"] = weekly["season_type"].fillna("")
        weekly = weekly[weekly["season_type"] == "REG"].copy()
        if stat_context.through_week is not None:
            weekly = weekly[weekly["week"] <= stat_context.through_week].copy()

    return pbp, weekly


def load_stat_inputs_with_fallback(target_season: int, target_week: int) -> Tuple[StatContext, pd.DataFrame, pd.DataFrame]:
    attempted: List[str] = []
    primary = choose_stat_context(target_season, target_week)
    candidate_contexts = [primary]
    for season in range(primary.season - 1, max(primary.season - 4, 1998), -1):
        candidate_contexts.append(
            StatContext(
                season=season,
                through_week=None,
                note=(
                    f"Requested {target_season} week {target_week} data was unavailable. "
                    f"Matchup stats fall back to the {season} regular season."
                ),
            )
        )

    for context in candidate_contexts:
        try:
            pbp, weekly = load_stat_inputs(context)
            if pbp.empty:
                raise ValueError("No nflverse play-by-play rows returned.")
            return context, pbp, weekly
        except Exception as exc:  # noqa: BLE001
            attempted.append(f"{context.season}: {type(exc).__name__}: {exc}")

    raise RuntimeError(
        "Unable to load nflverse stat data for any fallback season. Attempts: "
        + " | ".join(attempted)
    )


def add_rank_columns(frame: pd.DataFrame, column: str, rank_column: str, higher_better: bool) -> None:
    frame[rank_column] = (
        frame[column]
        .rank(method="min", ascending=not higher_better if higher_better else True)
        .astype("Int64")
    )


def compute_team_metrics(pbp: pd.DataFrame, weekly: pd.DataFrame) -> pd.DataFrame:
    offense = pbp[pbp["posteam"] != ""].copy()
    offense["is_off_play"] = (
        offense["pass"].fillna(0).eq(1)
        | offense["rush"].fillna(0).eq(1)
        | offense["sack"].fillna(0).eq(1)
    )
    offense = offense[offense["is_off_play"]].copy()

    games_played = (
        offense.groupby("posteam")["game_id"]
        .nunique()
        .rename("games_played")
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
        .merge(games_played, on="team", how="left")
    )

    offense_summary["off_rush_yards_pg"] = (
        offense_summary["off_rush_yards"] / offense_summary["games_played"]
    )
    offense_summary["off_pass_yards_pg"] = (
        offense_summary["off_pass_yards"] / offense_summary["games_played"]
    )
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

    defense_summary["def_rush_yards_pg_allowed"] = (
        defense_summary["def_rush_yards_allowed"] / defense_summary["def_games_played"]
    )
    defense_summary["def_pass_yards_pg_allowed"] = (
        defense_summary["def_pass_yards_allowed"] / defense_summary["def_games_played"]
    )
    defense_summary["def_sacks_pg"] = defense_summary["def_sacks"] / defense_summary["def_games_played"]

    fg_attempts = pbp[pbp["field_goal_attempt"].fillna(0).eq(1)].copy()
    fg_attempts["made"] = fg_attempts["field_goal_result"].eq("made")
    fg_attempts["blocked"] = fg_attempts["field_goal_result"].eq("blocked")

    kicker_totals = (
        fg_attempts.groupby(["posteam", "kicker_player_name"])
        .agg(
            fg_att=("field_goal_attempt", "sum"),
            fg_made=("made", "sum"),
            blocked_fg=("blocked", "sum"),
            longest_fg=("kick_distance", lambda s: s[fg_attempts.loc[s.index, "made"]].max()),
        )
        .reset_index()
        .rename(columns={"posteam": "team", "kicker_player_name": "kicker"})
    )
    if kicker_totals.empty:
        kicker_summary = pd.DataFrame(
            columns=[
                "team",
                "kicker",
                "fg_att",
                "fg_made",
                "blocked_fg",
                "longest_fg",
                "fg_pct",
                "made_60_plus",
            ]
        )
    else:
        kicker_summary = (
            kicker_totals.sort_values(["team", "fg_att", "fg_made"], ascending=[True, False, False])
            .drop_duplicates("team")
            .copy()
        )
        kicker_summary["fg_pct"] = kicker_summary["fg_made"] / kicker_summary["fg_att"]
        kicker_summary["made_60_plus"] = kicker_summary["longest_fg"].fillna(0).ge(60)
        fg_pct_pool = kicker_summary[kicker_summary["fg_att"] > 0].copy()
        kicker_summary["fg_pct_rank"] = pd.NA
        if not fg_pct_pool.empty:
            fg_pct_ranks = (
                fg_pct_pool["fg_pct"].rank(method="min", ascending=False).astype("Int64")
            )
            kicker_summary.loc[fg_pct_pool.index, "fg_pct_rank"] = fg_pct_ranks
        kicker_summary["blocked_fg_rank"] = (
            kicker_summary["blocked_fg"].rank(method="min", ascending=False).astype("Int64")
        )

    if (
        not weekly.empty
        and "recent_team" in weekly.columns
        and "special_teams_tds" in weekly.columns
    ):
        weekly_summary = (
            weekly.groupby("recent_team")["special_teams_tds"]
            .sum(min_count=1)
            .reset_index()
            .rename(columns={"recent_team": "team"})
        )
    else:
        special_tds = pbp[
            pbp["special"].fillna(0).eq(1)
            & pbp["touchdown"].fillna(0).eq(1)
            & pbp["td_team"].fillna("").ne("")
        ]
        weekly_summary = (
            special_tds.groupby("td_team")
            .size()
            .rename("special_teams_tds")
            .reset_index()
            .rename(columns={"td_team": "team"})
        )
    if weekly_summary.empty:
        weekly_summary = pd.DataFrame(columns=["team", "special_teams_tds"])
    weekly_summary["special_teams_tds_rank"] = (
        weekly_summary["special_teams_tds"].rank(method="min", ascending=False).astype("Int64")
        if not weekly_summary.empty
        else pd.Series(dtype="Int64")
    )

    metrics = (
        games_played.merge(offense_summary, on=["team", "games_played"], how="outer")
        .merge(defense_summary, on="team", how="outer")
        .merge(kicker_summary, on="team", how="left")
        .merge(weekly_summary, on="team", how="left")
    )

    add_rank_columns(metrics, "off_rush_yards_pg", "off_rush_rank", higher_better=True)
    add_rank_columns(metrics, "off_pass_yards_pg", "off_pass_rank", higher_better=True)
    add_rank_columns(metrics, "off_epa_per_play", "off_epa_rank", higher_better=True)
    add_rank_columns(metrics, "off_sacks_pg", "off_sacks_rank", higher_better=False)
    add_rank_columns(
        metrics, "def_rush_yards_pg_allowed", "def_rush_rank", higher_better=False
    )
    add_rank_columns(
        metrics, "def_pass_yards_pg_allowed", "def_pass_rank", higher_better=False
    )
    add_rank_columns(metrics, "def_epa_allowed", "def_epa_rank", higher_better=False)
    add_rank_columns(metrics, "def_sacks_pg", "def_sacks_rank", higher_better=True)
    return metrics


def build_records(games: pd.DataFrame, season: int, week: int) -> Dict[str, Dict[str, int]]:
    records: Dict[str, Dict[str, int]] = {}
    history = games[
        (games["season"] == season)
        & (games["game_type"] == "REG")
        & (games["week"] < week)
        & games["home_score"].notna()
        & games["away_score"].notna()
    ].copy()
    for _, row in history.iterrows():
        home = row["home_team"]
        away = row["away_team"]
        records.setdefault(home, {"wins": 0, "losses": 0, "ties": 0})
        records.setdefault(away, {"wins": 0, "losses": 0, "ties": 0})
        if row["home_score"] > row["away_score"]:
            records[home]["wins"] += 1
            records[away]["losses"] += 1
        elif row["home_score"] < row["away_score"]:
            records[away]["wins"] += 1
            records[home]["losses"] += 1
        else:
            records[home]["ties"] += 1
            records[away]["ties"] += 1
    return records


def format_record(record: Optional[Dict[str, int]]) -> str:
    if not record:
        return "0-0"
    base = f"{record['wins']}-{record['losses']}"
    return f"{base}-{record['ties']}" if record.get("ties") else base


def load_field_provenance() -> Dict[str, str]:
    if not COMBINED_DICTIONARY_PATH.exists():
        return {}
    dictionary = pd.read_csv(COMBINED_DICTIONARY_PATH)
    dictionary["field"] = dictionary["field"].astype(str)
    dictionary["description"] = dictionary["description"].astype(str)
    needed = {"wind", "special_teams_tds", "epa", "passing_yards", "rushing_yards", "sack"}
    subset = dictionary[dictionary["field"].isin(needed)]
    return dict(zip(subset["field"], subset["description"]))


def summarize_rank(
    rank: object, total_teams: int, *, top_n: int, high_is_good: bool, noun: str
) -> Optional[str]:
    if rank is None or pd.isna(rank):
        return None
    rank_int = int(rank)
    if rank_int <= top_n:
        return f"{ordinal(rank_int)} in the league in {noun}"
    if rank_int > total_teams - top_n:
        return f"{ordinal(rank_int)} in the league in {noun}"
    return None










def format_line(value: object) -> str:
    """Return a signed line string, e.g. '-3.5' or '+11.5'."""
    if value is None or pd.isna(value):
        return "N/A"
    f = float(value)
    return f"+{f:.1f}" if f > 0 else f"{f:.1f}"


def extract_team_logo(row: pd.Series) -> Optional[str]:
    """Return a logo URL from a spreads row when the CSV includes one.

    Prefer `team_logo_espn` from spreads_odds.csv when present.
    Falls back to legacy logo columns for compatibility.
    Returns None when no recognisable logo column is present or the value is
    not an http URL.
    """
    for col in ("team_logo_espn", "logo", "team_logo", "logo_url"):
        val = row.get(col)
        if isinstance(val, str) and val.strip().lower().startswith("http"):
            return val.strip()
    return None


def resolve_edge_numeric(row: pd.Series) -> Optional[float]:
    edge_numeric = row.get("best_edge")
    if edge_numeric is None or pd.isna(edge_numeric):
        edge_numeric = row.get("edge_numeric")
    if edge_numeric is None or pd.isna(edge_numeric):
        return None
    return float(parse_percent(edge_numeric))


def edge_confidence_label(edge: Optional[float]) -> str:
    if edge is None or edge < 0.04:
        return "Pass"
    if edge >= 0.07:
        return "Strong"
    return "Lean"


def matchup_call_label(edge: Optional[float]) -> str:
    if edge is None or edge < 0.01:
        return "No Bet"
    if edge < 0.04:
        return "Lean – doesn’t meet our edge criteria to fully bet"
    return "Bet"


def format_title_kickoff_date(kickoff: object) -> str:
    if kickoff is None or pd.isna(kickoff):
        return "N/A"
    return pd.Timestamp(kickoff).strftime("%m/%d/%Y")


def render_logo_row(
    away_name: str,
    away_logo: Optional[str],
    home_name: str,
    home_logo: Optional[str],
) -> Optional[str]:
    if away_logo and home_logo:
        return (
            "<p align=\"center\">"
            f"<img src=\"{away_logo}\" alt=\"{away_name}\" width=\"84\" />"
            " <strong>vs</strong> "
            f"<img src=\"{home_logo}\" alt=\"{home_name}\" width=\"84\" />"
            "</p>"
        )
    if away_logo:
        return f"<img src=\"{away_logo}\" alt=\"{away_name}\" width=\"84\" />"
    if home_logo:
        return f"<img src=\"{home_logo}\" alt=\"{home_name}\" width=\"84\" />"
    return None




def parse_table_headers(table: BeautifulSoup) -> List[str]:
    header_cells = table.find_all("th")
    return [cell.get_text(" ", strip=True) for cell in header_cells]


def parse_depth_chart_starters(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    starters: List[str] = []
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            texts = [cell.get_text(" ", strip=True) for cell in cells]
            if len(texts) < 2:
                continue
            position = texts[0].upper()
            if not re.match(r"^[A-Z0-9/+-]{1,5}$", position):
                continue
            for candidate in texts[1:]:
                cleaned = re.sub(r"\s+\d+$", "", candidate).strip()
                if len(cleaned) >= 3 and any(ch.isalpha() for ch in cleaned):
                    starters.append(cleaned)
                    break
    deduped: List[str] = []
    seen = set()
    for starter in starters:
        key = normalize_name(starter)
        if key and key not in seen:
            deduped.append(starter)
            seen.add(key)
    return deduped


def parse_injuries(html: str) -> List[StarterInjury]:
    soup = BeautifulSoup(html, "html.parser")
    injuries: List[StarterInjury] = []
    for table in soup.find_all("table"):
        headers = [header.lower() for header in parse_table_headers(table)]
        if not headers:
            continue
        if not any("name" in header or "player" in header for header in headers):
            continue
        rows = table.find_all("tr")
        for row in rows[1:]:
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
            if len(cells) != len(headers):
                continue
            payload = dict(zip(headers, cells))
            player = payload.get("name") or payload.get("player")
            if not player:
                continue
            status = payload.get("status") or payload.get("game status") or ""
            detail = payload.get("injury") or payload.get("comment") or payload.get("details") or ""
            injuries.append(StarterInjury(player=player, status=status, detail=detail))
    return injuries


def fetch_espn_starter_injuries(
    team: str,
    slug: Optional[str],
    session: requests.Session,
    debug_enabled: bool,
) -> TeamInjuryReport:
    report = TeamInjuryReport(team=team)

    # ── URL construction failure ──────────────────────────────────────────────
    if not slug:
        report.status = "no_slug"
        if debug_enabled:
            report.debug.append(
                EspnDebugEvent(
                    team=team,
                    source="slug",
                    url="",
                    failure="Team slug missing from the model CSV Team column.",
                )
            )
        return report

    injuries_url = f"https://www.espn.com/nfl/team/injuries/_/name/{slug}"
    depth_url = f"https://www.espn.com/nfl/team/depth/_/name/{slug}"

    # ── Injury-page fetch / parse ─────────────────────────────────────────────
    try:
        injuries_html = session.get(injuries_url, timeout=REQUEST_TIMEOUT)
        injuries_html.raise_for_status()
        injury_rows = parse_injuries(injuries_html.text)
        if not injury_rows:
            # Fetched successfully but nothing parsed — log always so article
            # can say "no injury rows parsed" rather than "no injuries".
            report.status = "injury_parse_failed"
            if debug_enabled:
                report.debug.append(
                    EspnDebugEvent(
                        team=team,
                        source="injuries",
                        url=injuries_url,
                        failure="Injuries page fetched, but no injury table rows were parsed.",
                    )
                )
    except Exception as exc:  # noqa: BLE001
        report.status = "injury_fetch_failed"
        if debug_enabled:
            report.debug.append(
                EspnDebugEvent(
                    team=team,
                    source="injuries",
                    url=injuries_url,
                    failure=f"{type(exc).__name__}: {exc}",
                )
            )
        return report

    # ── Depth-chart fetch / parse ─────────────────────────────────────────────
    try:
        depth_html = session.get(depth_url, timeout=REQUEST_TIMEOUT)
        depth_html.raise_for_status()
        starters = parse_depth_chart_starters(depth_html.text)
        if not starters:
            report.status = "depth_parse_failed"
            if debug_enabled:
                report.debug.append(
                    EspnDebugEvent(
                        team=team,
                        source="depth",
                        url=depth_url,
                        failure="Depth chart page fetched, but no starters were parsed.",
                    )
                )
    except Exception as exc:  # noqa: BLE001
        report.status = "depth_fetch_failed"
        if debug_enabled:
            report.debug.append(
                EspnDebugEvent(
                    team=team,
                    source="depth",
                    url=depth_url,
                    failure=f"{type(exc).__name__}: {exc}",
                )
            )
        return report

    # ── Cross-match injury list against depth-chart starters ─────────────────
    starter_keys = {normalize_name(name) for name in starters}
    report.starters = [
        injury
        for injury in injury_rows
        if normalize_name(injury.player) in starter_keys
    ]
    if report.starters:
        report.status = "ok_starters_found"
    elif report.status not in ("injury_parse_failed", "depth_parse_failed"):
        # Both pages parsed fine but no injures matched a starter
        report.status = "no_starter_match"
    return report


def find_schedule_row(
    games: pd.DataFrame, season: int, week: int, away_team: str, home_team: str
) -> Optional[pd.Series]:
    matched = games[
        (games["season"] == season)
        & (games["week"] == week)
        & (games["game_type"] == "REG")
        & (games["away_team"] == away_team)
        & (games["home_team"] == home_team)
    ]
    if matched.empty:
        return None
    return matched.iloc[0]


def prepare_games(
    spreads: pd.DataFrame,
    model: pd.DataFrame,
    requested_teams: Optional[Iterable[str]],
) -> pd.DataFrame:
    merged = spreads.merge(model, left_on="team", right_on="Team", how="left", validate="one_to_one")
    if requested_teams:
        requested = {team.upper() for team in requested_teams}
        eligible_games = merged[merged["team"].isin(requested)]["game"].unique()
        merged = merged[merged["game"].isin(eligible_games)].copy()
    if merged.empty:
        raise ValueError("No matchup rows remain after filtering")
    return merged


# ============================================================================
# NARRATIVE ENGINE (model-led articles) — replaces the old sentence builders
# and build_article. See header notes for the two main() wiring lines.
# ============================================================================

# ─────────────────────────────────────────────────────────────────────────────
# Plain-English translation: a rank becomes a word a normal bettor understands.
# ─────────────────────────────────────────────────────────────────────────────
def tier(rank: object, total_teams: int = 32) -> Optional[str]:
    """Map a 1-is-best rank to a tier phrase. Returns None if unavailable."""
    if rank is None or pd.isna(rank):
        return None
    r, n = int(rank), max(total_teams, 1)
    if r <= round(n * 0.12):
        return "elite"
    if r <= round(n * 0.31):
        return "one of the league's better"
    if r <= round(n * 0.47):
        return "above-average"
    if r <= round(n * 0.56):
        return "middling"
    if r <= round(n * 0.75):
        return "below-average"
    if r <= round(n * 0.90):
        return "struggling"
    return "among the league's worst"


def _safe_rank(metrics: pd.Series, col: str) -> Optional[int]:
    if metrics is None or not hasattr(metrics, "get"):
        return None
    val = metrics.get(col)
    if val is None or pd.isna(val):
        return None
    return int(val)


def _pick(variants: List[str], seed: str) -> str:
    """Deterministic phrasing rotation so 16 articles don't share a sentence.
    NOTE: if any rendered line shows up on >30% of a week's games, add variants."""
    if not variants:
        return ""
    return variants[hash(seed) % len(variants)]


def poss(name: str) -> str:
    """Possessive that handles names ending in s: Chargers -> Chargers'."""
    return name + "'" if str(name).endswith("s") else name + "'s"


# ─────────────────────────────────────────────────────────────────────────────
# MODEL-FIELD layer: rank the proprietary columns across the 32-team slate.
# Directions verified from the data:
#   Offensive Expected Points / Success Rate -> higher is better
#   Defensive Expected Points -> LOWER is better ; Defensive Success Rate -> higher
#   QB EPA (career / last 10) -> higher is better
# ─────────────────────────────────────────────────────────────────────────────
def model_ranks(model_df: pd.DataFrame) -> pd.DataFrame:
    """League-wide ranks + raw values from the model CSV, indexed by team abbr.

    Pass the FULL model frame (all 32 teams) so ranks are slate-wide. Build this
    once in main() and hand it to build_article.
    """
    df = model_df.copy()
    df.columns = [c.strip() for c in df.columns]

    def rk(col: str, higher_better: bool) -> pd.Series:
        if col not in df.columns:
            return pd.Series([pd.NA] * len(df), index=df.index, dtype="Int64")
        return df[col].rank(method="min", ascending=not higher_better).astype("Int64")

    out = pd.DataFrame({"team": df["Team"].astype(str).str.upper()})
    out["off_rank"] = rk("Offensive Expected Points (Season)", True)
    out["def_rank"] = rk("Defensive Expected Points (Season)", False)   # lower = better D
    out["off_sr_rank"] = rk("Offensive Success Rate (%)", True)
    out["def_sr_rank"] = rk("Defensive Success Rate (%)", True)
    out["qb10_rank"] = rk("QB Expected Points Added (Last 10 games)", True)
    # raw values for display
    for src, dst in [
        ("Model Prediction", "model_pred"),
        ("QB Expected Points Added (Last 10 games)", "qb10"),
        ("QB Expected Points Added (Career)", "qb_career"),
        ("Offensive Eckel Rate Over Expected (%)", "off_eckel"),
        ("Defensive Eckel Rate Over Expected (%)", "def_eckel"),
        ("Qbname", "qbname"),
        ("PROE", "proe"),
    ]:
        out[dst] = df[src].values if src in df.columns else pd.NA
    return out.set_index("team")


def clean_qb(value: object) -> Optional[str]:
    """'J.Allen' -> 'J. Allen'. Returns None if missing.
    If you have the fuller-name lookup from your odds page, map it in here."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    return re.sub(r"^([A-Za-z])\.([A-Za-z])", r"\1. \2", s)


def _spread_words(x: object) -> Optional[str]:
    if x is None or pd.isna(x):
        return None
    v = float(x)
    if v < 0:
        return f"a {abs(v):.1f}-point favorite"
    if v > 0:
        return f"a {abs(v):.1f}-point underdog"
    return "a pick'em"


def model_vs_market_lead(bet_name, model_pred, market_line, seed) -> Optional[str]:
    """The most digestible edge framing: our projected line vs the market's."""
    words = _spread_words(model_pred)
    if words is None or market_line is None or pd.isna(market_line):
        return None
    gap = abs(float(model_pred) - float(market_line))
    market_words = _spread_words(market_line) or format_line(market_line)
    return _pick(
        [
            f"Our model makes **{bet_name}** {words}. The market is only pricing them "
            f"as {market_words} — a **{gap:.1f}-point** gap, and that gap is the bet.",
            f"The number doing the work: our model projects **{bet_name}** {words}, "
            f"while the market sits at {market_words}. That **{gap:.1f} points** of "
            f"disagreement is the edge.",
        ],
        seed,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Salience mismatch engine: find the ONE storyline the data is telling.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class UnitBattle:
    dimension: str   # 'overall' | 'pass' | 'rush' | 'pressure'
    off_team: str
    def_team: str
    off_rank: int
    def_rank: int
    score: float     # >0 => offense exploits a weak defense; <0 => defense wins


_DIMS = [
    ("overall", "off_rank", "def_rank"),          # MODEL Expected Points ranks
    ("pass", "off_pass_rank", "def_pass_rank"),    # nflverse, positional color
    ("rush", "off_rush_rank", "def_rush_rank"),    # nflverse, positional color
]


def _battles(off_name, off_m, def_name, def_m, total_teams) -> List[UnitBattle]:
    mid = (total_teams + 1) / 2.0
    out: List[UnitBattle] = []
    for dim, ocol, dcol in _DIMS:
        orank, drank = _safe_rank(off_m, ocol), _safe_rank(def_m, dcol)
        if orank is None or drank is None:
            continue
        # off_strength: good offense is low rank -> positive
        # def_weak:     weak defense is high rank -> positive
        score = (mid - orank) + (drank - mid)
        out.append(UnitBattle(dim, off_name, def_name, orank, drank, score))
    return out


def _overall(battles: List[UnitBattle]) -> Optional[UnitBattle]:
    for b in battles:
        if b.dimension == "overall":
            return b
    return None


def pick_support_and_risk(
    bet_name, bet_m, opp_name, opp_m, total_teams, risk_floor: float = 2.0
) -> Tuple[Optional[Tuple[str, UnitBattle]], Optional[Tuple[str, UnitBattle]]]:
    """Return (support, risk).

    SUPPORT is anchored on OVERALL efficiency (what the model actually prices),
    framed as offense-led or defense-led depending on which favors the bet — so
    the 'Why' can never argue against the pick, and never leads with a freak
    positional number on a unit the team rarely uses.

    RISK is the opponent's genuine path to covering:
      ('exploit', battle)    -> a real edge the opponent can attack (score >= floor)
      ('outclassed', battle) -> no real edge; narrate their best unit + variance
    """
    bet_off = _battles(bet_name, bet_m, opp_name, opp_m, total_teams)   # bet has ball
    opp_off = _battles(opp_name, opp_m, bet_name, bet_m, total_teams)   # opp has ball

    # ── Support: overall efficiency, offense-led vs defense-led ───────────────
    support = None
    cands = []
    bo, oo = _overall(bet_off), _overall(opp_off)
    if bo is not None:
        cands.append(("offense", bo, bo.score))     # bet offense vs opp defense
    if oo is not None:
        cands.append(("defense", oo, -oo.score))    # bet defense vs opp offense
    if cands:
        cands.sort(key=lambda x: x[2], reverse=True)
        support = (cands[0][0], cands[0][1])

    # ── Risk: opponent's real avenue, or honest "outclassed" fallback ─────────
    risk = None
    if opp_off:
        best = max(opp_off, key=lambda b: b.score)
        if best.score >= risk_floor:
            risk = ("exploit", best)               # opp can genuinely attack here
        else:
            usable = [b for b in opp_off if b.dimension in ("overall", "pass", "rush")]
            if usable:
                risk = ("outclassed", min(usable, key=lambda b: b.off_rank))
    return support, risk


# ─────────────────────────────────────────────────────────────────────────────
# Render a storyline into a sentence (translated + rotated, never raw EPA).
# ─────────────────────────────────────────────────────────────────────────────
def _ord(n: int) -> str:  # local ordinal so this module is self-contained
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def render_storyline(role: str, b: UnitBattle, total_teams: int, seed: str) -> str:
    """role='offense' -> the offense exploits; role='defense' -> the defense wins."""
    o, d = b.off_team, b.def_team
    o_ord, d_ord = _ord(b.off_rank), _ord(b.def_rank)
    o_tier, d_tier = tier(b.off_rank, total_teams), tier(b.def_rank, total_teams)

    if role == "offense":
        templates = {
            "overall": [
                f"{poss(o)} offense ({o_tier}, {o_ord} in efficiency) draws a get-right "
                f"spot against {poss(d)} {d_tier} defense ({d_ord}).",
                f"The unit to attack is {poss(d)} defense ({d_ord}, {d_tier}), and {o} "
                f"({o_ord} on offense) is equipped to do it.",
                f"On efficiency alone {o} ({o_ord}) should move the ball on {d} ({d_ord}).",
            ],
            "pass": [
                f"{o} can throw on this {d} secondary — the {o_ord}-ranked passing "
                f"attack against a pass defense sitting {d_ord}.",
                f"The matchup to exploit is {poss(o)} pass game ({o_ord}) versus {poss(d)} "
                f"{d_tier} pass defense ({d_ord}).",
            ],
            "rush": [
                f"{poss(o)} ground game ({o_ord}) lines up against a {d} front giving up "
                f"yards on the ground ({d_ord} in rush defense).",
                f"{o} can lean on the run here — {o_ord} rushing offense into {poss(d)} "
                f"{d_tier} run defense ({d_ord}).",
            ],
            "pressure": [
                f"{poss(o)} line should keep it clean ({o_ord} in sacks allowed) against "
                f"a {d} pass rush that hasn't gotten home ({d_ord}).",
            ],
        }
    else:  # defense wins -> subject is the DEFENSE team (b.def_team)
        templates = {
            "overall": [
                f"{poss(d)} defense ({d_tier}, {d_ord}) should smother {poss(o)} {o_tier} "
                f"offense ({o_ord}) — that gap is the spine of the number.",
                f"This rides on {poss(d)} defense ({d_ord}, {d_tier}) against {o}, "
                f"whose offense ranks just {o_ord}.",
                f"{poss(o)} offense ({o_ord}) runs into {poss(d)} {d_tier} defense ({d_ord}), "
                f"and the model trusts the defense.",
            ],
            "pass": [
                f"{poss(d)} pass defense ({d_ord}, {d_tier}) is built to take away {poss(o)} "
                f"{o_ord}-ranked passing game.",
            ],
            "rush": [
                f"{poss(d)} run defense ({d_ord}) should bottle up {poss(o)} {o_ord} ground game "
                f"and keep them one-dimensional.",
            ],
            "pressure": [
                f"{poss(d)} pass rush ({d_ord} in sacks) against shaky {o} protection "
                f"({o_ord} in sacks allowed) is a script-wrecker.",
            ],
        }
    options = templates.get(b.dimension) or templates["overall"]
    return _pick(options, seed + role + b.dimension)


def headline_tail(support: Optional[Tuple[str, UnitBattle]], bet_name: str) -> str:
    if not support:
        return "a model lean"
    role, b = support
    if role == "defense":
        return f"betting {poss(bet_name)} defense"
    return {
        "pass": f"value in {poss(bet_name)} passing game",
        "rush": f"value on the ground for {bet_name}",
        "pressure": f"{poss(bet_name)} edge up front",
    }.get(b.dimension, f"a model edge on {bet_name}")


def render_risk(risk: Tuple[str, UnitBattle], opp_name: str,
                total_teams: int, seed: str) -> str:
    """Render the risk: a real exploit, or an honest 'outclassed + variance' line."""
    kind, b = risk
    if kind == "exploit":
        return render_storyline("offense", b, total_teams, seed)  # opp offense exploits
    o_ord = _ord(b.off_rank)
    unit = {"overall": "on offense", "pass": "through the air",
            "rush": "on the ground"}.get(b.dimension, "on offense")
    return _pick(
        [
            f"{poss(opp_name)} best path is {unit} ({o_ord}), but they're outgunned across "
            f"the board — so the real threat is variance: a fluky turnover, a "
            f"special-teams swing, or garbage-time points backing them into a cover.",
            f"There's no obvious soft spot for {opp_name} to attack; their {o_ord}-ranked "
            f"offense is the only lever. Realistically the risk is variance — short "
            f"fields, a non-offensive score, or a late backdoor cover.",
        ],
        seed,
    )


def assumed_starters(bet_name, bet_m, opp_name, opp_m) -> Optional[str]:
    """Trust line: name the QBs the model assumes, so readers can sanity-check
    against injury news before betting. NFL QB attrition is high — this matters."""
    a, b = clean_qb(bet_m.get("qbname")), clean_qb(opp_m.get("qbname"))
    if not a and not b:
        return None
    parts = []
    if a:
        parts.append(f"{a} ({bet_name})")
    if b:
        parts.append(f"{b} ({opp_name})")
    return (
        f"*Model assumes {' and '.join(parts)} under center. QB news moves these "
        f"numbers fast — check inactives before you bet.*"
    )


def qb_xfactor(bet_name, bet_m, opp_name, opp_m, total_teams, seed) -> List[str]:
    """A named QB callout, fired only when a starter's last-10 EPA is extreme.
    Replaces the old special-teams filler. Names build trust and engagement."""
    out: List[str] = []
    for team_name, mm in [(bet_name, bet_m), (opp_name, opp_m)]:
        name = clean_qb(mm.get("qbname"))
        rank = _safe_rank(mm, "qb10_rank")
        if not name or rank is None:
            continue
        if rank <= 5:
            out.append(
                _pick(
                    [f"{name} has been one of the most valuable quarterbacks in "
                     f"football over his last 10 games ({_ord(rank)} of {total_teams}) "
                     f"— a real tailwind for {team_name}.",
                     f"{name} grades out {_ord(rank)} of {total_teams} in QB value over "
                     f"his last 10 starts; that's the engine behind {poss(team_name)} "
                     f"number."],
                    seed + team_name,
                )
            )
        elif rank >= total_teams - 4:
            out.append(
                _pick(
                    [f"{name} sits {_ord(rank)} of {total_teams} in QB value over his "
                     f"last 10 games — the kind of play that caps {poss(team_name)} "
                     f"ceiling.",
                     f"{name} has been among the least productive starters in the league "
                     f"lately ({_ord(rank)} of {total_teams}), a real drag on {team_name}."],
                    seed + team_name,
                )
            )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Section builders
# ─────────────────────────────────────────────────────────────────────────────
def _side_facts(row: pd.Series) -> Dict[str, object]:
    cover = row.get("best_cover_probability")
    if cover is None or pd.isna(cover):
        cover = row.get("model_cover_probability")
    edge = row.get("best_edge")
    if edge is None or pd.isna(edge):
        edge = row.get("edge_numeric")
    return {"cover": cover, "edge": edge, "line": row.get("best_line"),
            "price": row.get("best_price")}


def build_bottom_line(
    away_name, home_name, stadium_name, bet_name, bet_line, confidence, bet_facts,
    seed, has_bet, model_lead=None,
) -> List[str]:
    """Human paragraph only. Replaces the Q&A 'Verdict'.
    `model_lead` (model-line-vs-market sentence) leads when available."""
    out: List[str] = ["## The Bottom Line"]
    venue = stadium_name or f"{home_name}'s home stadium"
    opener = f"{away_name} takes on {home_name} at {venue} and "
    if has_bet:
        hammer = (
            "a lean, not a hammer" if confidence == "Lean"
            else "a confident play" if confidence == "Strong" else "a lean"
        )
        if model_lead:
            out.append(
                f"{opener}{model_lead[:1].lower() + model_lead[1:]}"
            )
            out.append(
                f"That puts **{bet_name} {bet_line}** on the card at "
                f"{_price(bet_facts['price'])} — {hammer}."
            )
        else:
            out.append(
                f"{opener}"
                + _pick(
                    [f"the play is **{bet_name} {bet_line}**. At "
                     f"{display_percent(bet_facts['cover'], 1)} to cover, the price "
                     f"({_price(bet_facts['price'])}) leaves a "
                     f"{display_edge(bet_facts['edge'])} edge on the table. Treat it as "
                     f"{hammer}."],
                    seed,
                )
            )
    else:
        out.append(
            f"{opener}we have **no play here**. Neither side clears our 4% edge bar, so we're passing "
            f"— and that discipline is the point. The closest look is {bet_name} "
            f"{bet_line} at {display_edge(bet_facts['edge'])}, still short of the trigger."
        )
    return out


def build_tale_of_tape(bet_name, bet_m, opp_name, opp_m, total_teams) -> List[str]:
    """Numbers belong in a table, not the prose. Lead with the model's own fields."""
    def cell_rank(mm, col):
        r = _safe_rank(mm, col)
        return _ord(r) if r else "—"

    def cell_pct(mm, col):
        v = mm.get(col) if hasattr(mm, "get") else None
        return display_percent(v, 1) if v is not None and not pd.isna(v) else "—"

    def cell_qb(mm):
        name = clean_qb(mm.get("qbname"))
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


def build_cta(edge_game_count: int, has_bet: bool) -> List[str]:
    if has_bet:
        hook = (
            f"**{edge_game_count} games clear our 4% threshold this week — this is one "
            f"of them.** See all {edge_game_count}, live and updating as the lines move,"
        )
    else:
        hook = (
            f"**This isn't one of our plays — but {edge_game_count} games clear our 4% "
            f"threshold this week.** See every one of them, live,"
        )
    return [
        "## Best Bets Of The Week",
        "",
        f"{hook} in the member dashboard → "
        "[btb-analytics.com/member-access](https://btb-analytics.com/member-access)",
        "",
        "_Built by the BTB model. We target a 55–57% win rate and publish every "
        "result, wins and losses. [How the model works] · [Our full record]_",
    ]


# ─────────────────────────────────────────────────────────────────────────────
# REPLACEMENT build_article — assembles the new, narrative structure.
# ─────────────────────────────────────────────────────────────────────────────
def build_article(
    game, game_rows, metrics, records, team_names, schedule_row,
    stat_context, provenance, injury_reports, edge_game_count, model_ranks_df=None,
) -> Tuple[str, Dict[str, object]]:
    away_team, home_team = game.split("@")
    rows_by_team = {row["team"]: row for _, row in game_rows.iterrows()}
    away_row, home_row = rows_by_team[away_team], rows_by_team[home_team]
    metrics_indexed = metrics.set_index("team")
    mr = model_ranks_df  # league-wide model ranks/values, indexed by team
    total_teams = (
        int(len(mr)) if mr is not None and len(mr)
        else (int(metrics["team"].nunique()) if not metrics.empty else 32)
    )

    def m(team):
        """Per-team metrics: nflverse positional ranks + model ranks/values merged.
        Aliases off_rank/def_rank to nflverse EPA ranks if model data is absent."""
        base = metrics_indexed.loc[team] if team in metrics_indexed.index else pd.Series(dtype="float64")
        if mr is not None and team in mr.index:
            base = pd.concat([base, mr.loc[team]])
        if "off_rank" not in base.index and "off_epa_rank" in base.index:
            base["off_rank"] = base.get("off_epa_rank")
            base["def_rank"] = base.get("def_epa_rank")
        return base

    away_name = team_names.get(away_team, away_team)
    home_name = team_names.get(home_team, home_team)

    kickoff = away_row["game_date_est"]
    kickoff_label = kickoff.strftime("%Y-%m-%d") if pd.notna(kickoff) else "N/A"
    kickoff_title_label = format_title_kickoff_date(kickoff)
    time_label = away_row.get("game_time_est", "N/A")
    location = None
    stadium_name = None
    if schedule_row is not None:
        stadium = schedule_row.get("stadium")
        if isinstance(stadium, str) and stadium.strip():
            stadium_name = stadium.strip()
            location = stadium_name

    # favorite / underdog by market line (most negative == favorite)
    favorite_row = game_rows.sort_values("market_line").iloc[0]
    dog_row = game_rows.sort_values("market_line").iloc[-1]

    # verdict side = larger edge
    fav_edge = resolve_edge_numeric(favorite_row)
    dog_edge = resolve_edge_numeric(dog_row)
    verdict_row = (
        favorite_row
        if fav_edge is not None and (dog_edge is None or fav_edge >= dog_edge)
        else dog_row
    )
    other_row = dog_row if verdict_row is favorite_row else favorite_row
    verdict_edge = resolve_edge_numeric(verdict_row)
    confidence = edge_confidence_label(verdict_edge)
    has_bet = verdict_edge is not None and verdict_edge >= 0.04

    bet_team = verdict_row["team"]
    opp_team = other_row["team"]
    bet_name = team_names.get(bet_team, bet_team)
    opp_name = team_names.get(opp_team, opp_team)
    bet_line = format_line(verdict_row.get("best_line"))
    bet_m, opp_m = m(bet_team), m(opp_team)
    seed = game  # stable per-matchup seed for phrasing rotation

    support, _risk = pick_support_and_risk(bet_name, bet_m, opp_name, opp_m, total_teams)

    lines_summary = (
        f"{favorite_row['team']} {format_float(favorite_row['market_line'])} / "
        f"{dog_row['team']} +{format_float(abs(dog_row['market_line']))}"
    )
    best_book_summary = (
        f"{favorite_row['team']} {format_float(favorite_row['best_line'])} at "
        f"{favorite_row['best_book']}; {dog_row['team']} "
        f"+{format_float(abs(dog_row['best_line']))} at {dog_row['best_book']}"
    )

    # ── Assemble ──────────────────────────────────────────────────────────────
    sections: List[str] = []

    sections.append(f"# {away_name} vs {home_name} Prediction For {kickoff_title_label}")
    sections.append("")

    away_logo, home_logo = extract_team_logo(away_row), extract_team_logo(home_row)
    logo_row = render_logo_row(away_name, away_logo, home_name, home_logo)
    if logo_row:
        sections.extend([logo_row, ""])

    bet_facts = _side_facts(verdict_row)
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
                display_edge(edge),
                matchup_call_label(edge),
            )
        )
    sections.extend(["| Team name | Best Spread/Odds | Best Book | Model Cover% | Edge | BTB Advice |", "|---|---|---|---|---|---|"])
    sections.extend([f"| {team} | {spread_odds} | {book} | {cover} | {edge} | {call} |" for team, spread_odds, book, cover, edge, call in matchup_rows])

    model_lead = model_vs_market_lead(
        bet_name, verdict_row.get("Model Prediction"),
        verdict_row.get("best_line", verdict_row.get("market_line")), seed,
    ) if has_bet else None
    sections.extend([""] + build_bottom_line(
        away_name, home_name, stadium_name, bet_name, bet_line, confidence, bet_facts,
        seed, has_bet, model_lead
    ))

    # Assumed starters — trust line so readers can sanity-check QB news
    starters_note = assumed_starters(bet_name, bet_m, opp_name, opp_m)
    if starters_note:
        sections.extend(["", starters_note])

    # Why — always supports the pick
    tape = build_tale_of_tape(bet_name, bet_m, opp_name, opp_m, total_teams)
    if has_bet and support:
        sections.extend(["", "## Why The Pick",
                         "Our model uses data points that correlate best with a team covering. Here’s how these two teams stack up in some of those categories"])
        if tape:
            sections.extend([""] + tape)
            sections.extend(["", "\\*The rate of possessions that result in a big play touchdown or 1st down inside the opponent’s 40 yard line"])
    elif not has_bet and support:
        sections.extend(["", "## What the Model Sees",
                         render_storyline(support[0], support[1], total_teams, seed),
                         "The lean exists — it just isn't big enough to bet."])

    # QB X-factor — named callout when a starter's last-10 EPA is extreme
    qb_lines = qb_xfactor(bet_name, bet_m, opp_name, opp_m, total_teams, seed)
    if qb_lines:
        sections.extend(["", "## Quarterback X-Factor"] + qb_lines)

    # Injury report — kept, conditional, no null leakage
    injury_lines = []
    for team, team_name in [(away_team, away_name), (home_team, home_name)]:
        report = injury_reports[team]
        if report.starters:
            entries = [
                f"{inj.player} ({inj.status or 'status unavailable'})"
                for inj in report.starters
            ]
            injury_lines.append(f"**{team_name}:** " + "; ".join(entries) + ".")
    if injury_lines:
        sections.extend(["", "## Injury Report"] + injury_lines)

    sections.extend([""] + build_cta(edge_game_count, has_bet))

    payload = {
        "game": game, "away_team": away_team, "home_team": home_team,
        "kickoff_date": kickoff_label, "kickoff_time_et": time_label,
        "location": location, "bet_side": f"{bet_name} {bet_line}" if has_bet else None,
        "confidence": confidence, "has_bet": has_bet,
        "governing_storyline": (support[0] + ":" + support[1].dimension) if support else None,
        "line_summary": lines_summary, "best_book_summary": best_book_summary,
        "stat_context": asdict(stat_context),
        "injury_reports": {
            t: {"status": r.status, "starters": [asdict(i) for i in r.starters]}
            for t, r in injury_reports.items()
        },
    }
    return "\n".join(sections) + "\n", payload



def main() -> None:
    args = parse_args()
    output_root = Path(args.output_dir).resolve()
    safe_mkdir(output_root)

    session = requests.Session()
    session.headers["User-Agent"] = "football-testgrounds-matchup-articles/1.0"

    spreads, week, season, local_root = load_spreads_and_target_context(args, session)
    model = load_model_data(week, args, local_root, session)
    model_rank_lookup = model_ranks(model)
    weekly_merged = spreads.merge(model, left_on="team", right_on="Team", how="left", validate="one_to_one")
    merged = prepare_games(spreads, model, args.teams)

    team_names = load_team_names()
    games_schedule = load_games_schedule()
    records = build_records(games_schedule, season, week)
    stat_context, pbp, weekly = load_stat_inputs_with_fallback(season, week)
    metrics = compute_team_metrics(pbp, weekly)
    provenance = load_field_provenance()

    game_count = int(weekly_merged["game"].nunique())
    games_with_edges = int(weekly_merged[weekly_merged["edge_numeric"] >= 0.04]["game"].nunique())

    team_slug_map = {team: str(team).lower() for team in model["Team"].dropna().unique()}

    weekly_dir = output_root / f"week_{week}"
    safe_mkdir(weekly_dir)
    combined_articles: List[str] = []
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "season": season,
        "week": week,
        "games_with_edges_at_or_above_0_04": games_with_edges,
        "total_games": game_count,
        "field_provenance": provenance,
        "articles": [],
    }

    for game, game_rows in merged.groupby("game", sort=True):
        away_team, home_team = game.split("@")
        injury_reports = {
            team: fetch_espn_starter_injuries(
                team,
                team_slug_map.get(team),
                session,
                debug_enabled=args.espn_debug,
            )
            for team in (away_team, home_team)
        }
        article, article_payload = build_article(
            game,
            game_rows.copy(),
            metrics.copy(),
            records,
            team_names,
            find_schedule_row(games_schedule, season, week, away_team, home_team),
            stat_context,
            provenance,
            injury_reports,
            games_with_edges,
            model_rank_lookup,
        )
        combined_articles.append(article.rstrip())
        article_payload["article_path"] = f"{slugify_game(game)}.md"
        payload["articles"].append(article_payload)
        (weekly_dir / f"{slugify_game(game)}.md").write_text(article, encoding="utf-8")

    combined_path = weekly_dir / "weekly_matchup_articles.md"
    combined_path.write_text("\n\n---\n\n".join(combined_articles) + "\n", encoding="utf-8")
    (weekly_dir / "weekly_matchup_articles.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )

    print(f"Generated {len(combined_articles)} matchup article(s) for week {week} in {weekly_dir}")


if __name__ == "__main__":
    main()
