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
    ]
    weekly_columns = ["season", "week", "season_type", "recent_team", "special_teams_tds"]

    pbp = read_remote_parquet(NFLVERSE_PBP_URL.format(season=stat_context.season), pbp_columns)
    weekly = read_remote_parquet(
        NFLVERSE_WEEKLY_URL.format(season=stat_context.season), weekly_columns
    )

    pbp["posteam"] = pbp["posteam"].fillna("").str.upper()
    pbp["defteam"] = pbp["defteam"].fillna("").str.upper()
    pbp["home_team"] = pbp["home_team"].fillna("").str.upper()
    pbp["away_team"] = pbp["away_team"].fillna("").str.upper()
    pbp["season_type"] = pbp["season_type"].fillna("")
    pbp = pbp[pbp["season_type"] == "REG"].copy()
    if stat_context.through_week is not None:
        pbp = pbp[pbp["week"] <= stat_context.through_week].copy()

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
            if pbp.empty or weekly.empty:
                raise ValueError("No nflverse rows returned.")
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

    weekly_summary = (
        weekly.groupby("recent_team")["special_teams_tds"]
        .sum(min_count=1)
        .reset_index()
        .rename(columns={"recent_team": "team"})
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


def build_offense_sentence(team_name: str, row: pd.Series, total_teams: int, model_row: pd.Series) -> str:
    pieces = [
        (
            f"{team_name} averages {format_float(row.get('off_rush_yards_pg'))} rushing yards per game"
            + (
                f" ({summarize_rank(row.get('off_rush_rank'), total_teams, top_n=TOP_10, high_is_good=True, noun='rushing offense')})"
                if summarize_rank(
                    row.get("off_rush_rank"),
                    total_teams,
                    top_n=TOP_10,
                    high_is_good=True,
                    noun="rushing offense",
                )
                else ""
            )
        ),
        (
            f"{format_float(row.get('off_pass_yards_pg'))} passing yards per game"
            + (
                f" ({summarize_rank(row.get('off_pass_rank'), total_teams, top_n=TOP_10, high_is_good=True, noun='passing offense')})"
                if summarize_rank(
                    row.get("off_pass_rank"),
                    total_teams,
                    top_n=TOP_10,
                    high_is_good=True,
                    noun="passing offense",
                )
                else ""
            )
        ),
        (
            f"and {format_float(row.get('off_epa_per_play'), 3)} EPA per play"
            + (
                f" ({summarize_rank(row.get('off_epa_rank'), total_teams, top_n=TOP_10, high_is_good=True, noun='offensive EPA/play')})"
                if summarize_rank(
                    row.get("off_epa_rank"),
                    total_teams,
                    top_n=TOP_10,
                    high_is_good=True,
                    noun="offensive EPA/play",
                )
                else ""
            )
        ),
    ]
    sentence = ", ".join(pieces[:-1]) + ", " + pieces[-1] + "."
    eckel = model_row.get("Offensive Eckel Rate Over Expected (%)")
    sentence += f" Offensive Eckel ROE from the model file sits at {display_percent(eckel, 2)}."
    sacks_rank = row.get("off_sacks_rank")
    if pd.notna(sacks_rank) and (
        int(sacks_rank) <= TOP_5 or int(sacks_rank) > total_teams - TOP_5
    ):
        qualifier = (
            f"{ordinal(int(sacks_rank))}-fewest sacks taken"
            if int(sacks_rank) <= TOP_5
            else f"{ordinal(int(sacks_rank))}-most sacks taken"
        )
        sentence += (
            f" They have taken {format_float(row.get('off_sacks_pg'), 2)} sacks per game, "
            f"which ranks {qualifier}."
        )
    return sentence


def build_defense_sentence(team_name: str, row: pd.Series, total_teams: int, model_row: pd.Series) -> str:
    pieces = [
        (
            f"{team_name} allows {format_float(row.get('def_rush_yards_pg_allowed'))} rushing yards per game"
            + (
                f" ({summarize_rank(row.get('def_rush_rank'), total_teams, top_n=TOP_10, high_is_good=False, noun='rush defense')})"
                if summarize_rank(
                    row.get("def_rush_rank"),
                    total_teams,
                    top_n=TOP_10,
                    high_is_good=False,
                    noun="rush defense",
                )
                else ""
            )
        ),
        (
            f"{format_float(row.get('def_pass_yards_pg_allowed'))} passing yards per game"
            + (
                f" ({summarize_rank(row.get('def_pass_rank'), total_teams, top_n=TOP_10, high_is_good=False, noun='pass defense')})"
                if summarize_rank(
                    row.get("def_pass_rank"),
                    total_teams,
                    top_n=TOP_10,
                    high_is_good=False,
                    noun="pass defense",
                )
                else ""
            )
        ),
        (
            f"and {format_float(row.get('def_epa_allowed'), 3)} EPA allowed per play"
            + (
                f" ({summarize_rank(row.get('def_epa_rank'), total_teams, top_n=TOP_10, high_is_good=False, noun='defensive EPA/play')})"
                if summarize_rank(
                    row.get("def_epa_rank"),
                    total_teams,
                    top_n=TOP_10,
                    high_is_good=False,
                    noun="defensive EPA/play",
                )
                else ""
            )
        ),
    ]
    sentence = ", ".join(pieces[:-1]) + ", " + pieces[-1] + "."
    eckel = model_row.get("Defensive Eckel Rate Over Expected (%)")
    sentence += f" Defensive Eckel ROE from the model file sits at {display_percent(eckel, 2)}."
    sacks_rank = row.get("def_sacks_rank")
    if pd.notna(sacks_rank) and (
        int(sacks_rank) <= TOP_5 or int(sacks_rank) > total_teams - TOP_5
    ):
        qualifier = (
            f"{ordinal(int(sacks_rank))} in sacks"
            if int(sacks_rank) <= TOP_5
            else f"{ordinal(int(sacks_rank))} in sacks"
        )
        sentence += (
            f" Their defense is producing {format_float(row.get('def_sacks_pg'), 2)} sacks per game, "
            f"which ranks {qualifier}."
        )
    return sentence


def build_special_teams_sentence(team_name: str, row: pd.Series, total_teams: int) -> Optional[str]:
    notes: List[str] = []
    kicker = row.get("kicker")
    if isinstance(kicker, str) and kicker.strip():
        if bool(row.get("made_60_plus")):
            notes.append(f"{kicker} has already connected from 60-plus yards")
        fg_att = row.get("fg_att")
        fg_pct_rank = row.get("fg_pct_rank")
        if pd.notna(fg_att) and float(fg_att) > 0 and pd.notna(fg_pct_rank):
            fg_pct_rank_int = int(fg_pct_rank)
            if fg_pct_rank_int <= TOP_5 or fg_pct_rank_int > total_teams - TOP_5:
                notes.append(
                    f"{kicker} is hitting {display_percent(row.get('fg_pct'), 1)} of field goals "
                    f"({ordinal(fg_pct_rank_int)} among kickers with attempts)"
                )
        blocked_rank = row.get("blocked_fg_rank")
        blocked_fg = row.get("blocked_fg")
        if pd.notna(blocked_rank) and pd.notna(blocked_fg) and float(blocked_fg) > 0:
            if int(blocked_rank) <= TOP_3:
                notes.append(f"{kicker} is tied for {ordinal(int(blocked_rank))} in blocked field goals")

    st_rank = row.get("special_teams_tds_rank")
    st_tds = row.get("special_teams_tds")
    if pd.notna(st_rank) and pd.notna(st_tds) and int(st_rank) <= TOP_5:
        notes.append(
            f"{team_name} has {int(st_tds)} kick/punt return touchdown(s), tied for {ordinal(int(st_rank))}"
        )

    if not notes:
        return None
    return "Special teams: " + "; ".join(notes) + "."


def build_weather_sentence(schedule_row: Optional[pd.Series], provenance: Dict[str, str]) -> Optional[str]:
    if schedule_row is None or pd.isna(schedule_row.get("wind")):
        return None
    wind = float(schedule_row["wind"])
    if wind <= 20:
        return None
    roof = schedule_row.get("roof") or "unknown"
    description = provenance.get("wind", "Wind speed in miles per hour.")
    return (
        f"Weather note: nflverse lists {wind:.0f} mph winds with a {roof} roof designation. "
        f"(Provenance: {description})"
    )


def format_line(value: object) -> str:
    """Return a signed line string, e.g. '-3.5' or '+11.5'."""
    if value is None or pd.isna(value):
        return "N/A"
    f = float(value)
    return f"+{f:.1f}" if f > 0 else f"{f:.1f}"


def extract_team_logo(row: pd.Series) -> Optional[str]:
    """Return a logo URL from a spreads row when the CSV includes one.

    Assumption: if spreads_odds.csv contains a logo column it will be named
    one of: logo, team_logo, logo_url.  Column names are lower-cased during
    load.  Returns None when no recognisable logo column is present or the
    value is not an http URL.
    """
    for col in ("logo", "team_logo", "logo_url"):
        val = row.get(col)
        if isinstance(val, str) and val.strip().lower().startswith("http"):
            return val.strip()
    return None


def build_model_prediction(game_rows: pd.DataFrame, edge_game_count: int, team_names: Dict[str, str]) -> str:
    """Return the Model Prediction section body.

    Opening sentences: one per team, formatted like –
      "The model gives the Arizona Cardinals a 55.77% chance of covering +11.5
       which at -110 odds is a 3.39% edge which does not meet our threshold of
       4% to bet."

    Columns consulted (in priority order):
      cover prob  → best_cover_probability (from spreads CSV if present),
                    then model_cover_probability (from model CSV)
      line        → best_line (spreads CSV)
      price/odds  → best_price (spreads CSV, optional)
      edge        → edge_numeric (model CSV, stored as decimal e.g. 0.04)
    """
    sentences: List[str] = []
    for _, row in game_rows.iterrows():
        team_abbr = row.get("team", "")
        team_name = team_names.get(str(team_abbr).upper(), str(team_abbr))

        # Cover probability
        cover_raw = row.get("best_cover_probability")
        if cover_raw is None or pd.isna(cover_raw):
            cover_raw = row.get("model_cover_probability")
        cover_pct = display_percent(cover_raw, 2)

        # Line
        line_display = format_line(row.get("best_line"))

        # Price / odds (American format, e.g. -110)
        price_val = row.get("best_price")
        has_price = price_val is not None and not pd.isna(price_val)
        if has_price:
            price_f = float(price_val)
            price_str = f"{price_f:+.0f}" if price_f > 0 else f"{price_f:.0f}"
        else:
            price_str = None

        # Edge — use signed display so negative edges are clearly identified
        edge_numeric = row.get("edge_numeric")
        has_edge = edge_numeric is not None and not pd.isna(edge_numeric)
        if has_edge:
            edge_f = float(edge_numeric)
            edge_pct_str = f"{edge_f * 100:.2f}%"
            meets = edge_f >= 0.04
            threshold_phrase = (
                "meets our threshold of 4% to bet"
                if meets
                else "does not meet our threshold of 4% to bet"
            )
        else:
            edge_pct_str = "N/A"
            threshold_phrase = "does not meet our threshold of 4% to bet"

        if has_price and has_edge:
            sentence = (
                f"The model gives the {team_name} a {cover_pct} chance of covering {line_display} "
                f"which at {price_str} odds is a {edge_pct_str} edge which {threshold_phrase}."
            )
        elif has_edge:
            sentence = (
                f"The model gives the {team_name} a {cover_pct} chance of covering {line_display}, "
                f"representing a {edge_pct_str} edge which {threshold_phrase}."
            )
        else:
            sentence = (
                f"The model gives the {team_name} a {cover_pct} chance of covering {line_display}."
            )
        sentences.append(sentence)

    cta = (
        f"Our model shows edges of at least 4% on {edge_game_count} games this week. "
        "To view all of our predictions and bets for the week, go to btb-analytics.com/member-access today!"
    )
    return "  \n".join(sentences) + "\n\n" + cta


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


def build_article(
    game: str,
    game_rows: pd.DataFrame,
    metrics: pd.DataFrame,
    records: Dict[str, Dict[str, int]],
    team_names: Dict[str, str],
    schedule_row: Optional[pd.Series],
    stat_context: StatContext,
    provenance: Dict[str, str],
    injury_reports: Dict[str, TeamInjuryReport],
    edge_game_count: int,
) -> Tuple[str, Dict[str, object]]:
    away_team, home_team = game.split("@")
    rows_by_team = {row["team"]: row for _, row in game_rows.iterrows()}
    away_row = rows_by_team[away_team]
    home_row = rows_by_team[home_team]
    metrics_indexed = metrics.set_index("team")
    total_teams = int(metrics["team"].nunique()) if not metrics.empty else 32

    away_metrics = metrics_indexed.loc[away_team] if away_team in metrics_indexed.index else pd.Series()
    home_metrics = metrics_indexed.loc[home_team] if home_team in metrics_indexed.index else pd.Series()

    away_name = team_names.get(away_team, away_team)
    home_name = team_names.get(home_team, home_team)

    kickoff = away_row["game_date_est"]
    kickoff_label = kickoff.strftime("%Y-%m-%d") if pd.notna(kickoff) else "N/A"
    time_label = away_row.get("game_time_est", "N/A")
    location = f"at {home_name}"
    if schedule_row is not None:
        stadium = schedule_row.get("stadium")
        if isinstance(stadium, str) and stadium.strip():
            location = f"{stadium} ({home_name})"

    # ── Determine favorite / underdog from market_line ────────────────────────
    # Most-negative market_line → favorite; most-positive → underdog.
    favorite_row = game_rows.sort_values("market_line").iloc[0]
    dog_row = game_rows.sort_values("market_line").iloc[-1]
    favorite_team = favorite_row["team"]
    dog_team = dog_row["team"]
    favorite_name = team_names.get(favorite_team, favorite_team)
    dog_name = team_names.get(dog_team, dog_team)
    favorite_metrics = (
        metrics_indexed.loc[favorite_team]
        if favorite_team in metrics_indexed.index
        else pd.Series()
    )
    dog_metrics = (
        metrics_indexed.loc[dog_team]
        if dog_team in metrics_indexed.index
        else pd.Series()
    )

    # ── Line / book summaries ─────────────────────────────────────────────────
    lines_summary = (
        f"{favorite_row['team']} {format_float(favorite_row['market_line'])} / "
        f"{dog_row['team']} +{format_float(abs(dog_row['market_line']))}"
    )
    best_book_summary = (
        f"{favorite_row['team']} {format_float(favorite_row['best_line'])} at {favorite_row['best_book']}; "
        f"{dog_row['team']} +{format_float(abs(dog_row['best_line']))} at {dog_row['best_book']}"
    )

    weather_sentence = build_weather_sentence(schedule_row, provenance)

    # ── Logos ─────────────────────────────────────────────────────────────────
    # Logo URLs are extracted from the spreads rows when the CSV includes a
    # logo / team_logo / logo_url column.  If the column is absent or empty the
    # logo lines are simply omitted.
    away_logo = extract_team_logo(away_row)
    home_logo = extract_team_logo(home_row)

    # ── Article sections ──────────────────────────────────────────────────────
    sections: List[str] = []

    # Optional logo header (markdown image syntax)
    if away_logo or home_logo:
        logo_parts = []
        if away_logo:
            logo_parts.append(f"![{away_name}]({away_logo})")
        if home_logo:
            logo_parts.append(f"![{home_name}]({home_logo})")
        sections.append("  ".join(logo_parts))
        sections.append("")

    sections.extend([
        f"# {away_name} at {home_name}",
        "",
        "## Matchup Info",
        (
            f"The {away_name} ({format_record(records.get(away_team))}) travel to face the "
            f"{home_name} ({format_record(records.get(home_team))}) on {kickoff_label} at "
            f"{time_label} ET {location}. "
            f"**Line:** {lines_summary}. "
            f"**Best book:** {best_book_summary}."
        ),
    ])

    if weather_sentence:
        sections.append("")
        sections.append(weather_sentence)

    # ── Statistical matchup ───────────────────────────────────────────────────
    # Eckel ROE definition — shown once, inline before the stat sections.
    eckel_definition = (
        "_Eckel ROE (Rate Over Expected): how often an offense generates — or a defense allows — "
        "a big-play touchdown or a first down inside the 40 on any given drive, "
        "relative to the expected rate._"
    )

    sections.extend([
        "",
        eckel_definition,
        "",
        f"## {favorite_name} offense vs {dog_name} defense",
        build_offense_sentence(favorite_name, favorite_metrics, total_teams, favorite_row),
        build_defense_sentence(dog_name, dog_metrics, total_teams, dog_row),
        "",
        f"## {favorite_name} defense vs {dog_name} offense",
        build_defense_sentence(favorite_name, favorite_metrics, total_teams, favorite_row),
        build_offense_sentence(dog_name, dog_metrics, total_teams, dog_row),
    ])

    # Special teams — only rendered when at least one team meets the criteria.
    away_st = build_special_teams_sentence(away_name, away_metrics, total_teams)
    home_st = build_special_teams_sentence(home_name, home_metrics, total_teams)
    if away_st or home_st:
        sections.append("")
        sections.append("## Special teams")
        if away_st:
            sections.append(away_st)
        if home_st:
            sections.append(home_st)

    # ── Injury report ─────────────────────────────────────────────────────────
    _INJURY_STATUS_LABELS = {
        "ok_starters_found": None,  # handled below via report.starters
        "ok_no_injuries": "No starter injuries found on ESPN.",
        "no_slug": "Injury data unavailable: team slug could not be resolved.",
        "injury_fetch_failed": "Injury data unavailable: ESPN injuries page could not be fetched.",
        "injury_parse_failed": "Injury data unavailable: ESPN injuries page fetched but no rows parsed.",
        "depth_fetch_failed": "Depth-chart data unavailable: ESPN depth-chart page could not be fetched.",
        "depth_parse_failed": "Depth-chart data unavailable: ESPN depth-chart fetched but no starters parsed.",
        "no_starter_match": "No starter injuries found after cross-matching the depth chart.",
    }

    sections.extend(["", "## Injury report"])
    for team, team_name in [(away_team, away_name), (home_team, home_name)]:
        report = injury_reports[team]
        if report.starters:
            entries = [
                f"{injury.player} ({injury.status or 'status unavailable'}: {injury.detail or 'detail unavailable'})"
                for injury in report.starters
            ]
            sections.append(f"**{team_name}:** " + "; ".join(entries) + ".")
        else:
            label = _INJURY_STATUS_LABELS.get(report.status, f"Status unknown ({report.status}).")
            sections.append(f"**{team_name}:** {label}")

    debug_events = [asdict(event) for report in injury_reports.values() for event in report.debug]
    if debug_events:
        sections.extend(["", "## ESPN debug"])
        for event in debug_events:
            sections.append(
                f"- {event['team']} {event['source']}: `{event['url']}` -> {event['failure']}"
            )

    # ── Model Prediction ──────────────────────────────────────────────────────
    sections.extend(
        [
            "",
            "## Model Prediction",
            build_model_prediction(game_rows, edge_game_count, team_names),
            "",
            "## Notes",
            f"- {stat_context.note}",
        ]
    )

    payload = {
        "game": game,
        "away_team": away_team,
        "home_team": home_team,
        "kickoff_date": kickoff_label,
        "kickoff_time_et": time_label,
        "location": location,
        "line_summary": lines_summary,
        "best_book_summary": best_book_summary,
        "stat_context": asdict(stat_context),
        "injury_reports": {
            team: {
                "status": report.status,
                "starters": [asdict(injury) for injury in report.starters],
                "debug": [asdict(event) for event in report.debug],
            }
            for team, report in injury_reports.items()
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
