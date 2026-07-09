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
QB_CROSSWALK_PATH = (
    Path(__file__).resolve().parents[1] / "data-raw" / "QB Crosswalk.csv"
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
        return (local_root / path).read_text(encoding="utf-8")
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
            github_contents_url(owner, repo, ref, path), headers=headers, timeout=REQUEST_TIMEOUT
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
    return pd.read_csv(
        StringIO(
            fetch_text(
                path, local_root=local_root, owner=owner, repo=repo, ref=ref, session=session
            )
        )
    )


def load_qb_crosswalk() -> Dict[str, str]:
    if not QB_CROSSWALK_PATH.exists():
        return {}
    crosswalk = pd.read_csv(QB_CROSSWALK_PATH)
    if "starter_player_name" not in crosswalk.columns or "Full Name" not in crosswalk.columns:
        return {}
    valid = crosswalk[["starter_player_name", "Full Name"]].dropna().copy()
    valid["starter_player_name"] = valid["starter_player_name"].astype(str).str.strip()
    valid["Full Name"] = valid["Full Name"].astype(str).str.strip()
    return dict(zip(valid["starter_player_name"], valid["Full Name"]))


def load_spreads_and_target_context(
    args: argparse.Namespace, session: requests.Session
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
    week: int, args: argparse.Namespace, local_root: Optional[Path], session: requests.Session
) -> pd.DataFrame:
    model = read_repo_csv(
        TRASH_SCHEDULE_MODEL_TEMPLATE.format(week=week),
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
        return StatContext(target_season - 1, None, f"Fallback to {target_season - 1}.")
    return StatContext(target_season, target_week - 1, f"Through week {target_week - 1}.")


def load_stat_inputs(stat_context: StatContext) -> Tuple[pd.DataFrame, pd.DataFrame]:
    pbp_columns = [
        "game_id","season","week","season_type","posteam","defteam","home_team","away_team",
        "pass","rush","sack","passing_yards","rushing_yards","epa","field_goal_attempt",
        "field_goal_result","kick_distance","kicker_player_name","touchdown","special","td_team",
    ]
    weekly_columns = ["season", "week", "season_type", "recent_team", "special_teams_tds"]
    pbp = read_remote_parquet(NFLVERSE_PBP_URL.format(season=stat_context.season), pbp_columns)
    try:
        weekly = read_remote_parquet(NFLVERSE_WEEKLY_URL.format(season=stat_context.season), weekly_columns)
    except Exception:
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
    candidate_contexts = [primary] + [
        StatContext(season=s, through_week=None, note=f"Fallback to {s}.")
        for s in range(primary.season - 1, max(primary.season - 4, 1998), -1)
    ]
    for context in candidate_contexts:
        try:
            pbp, weekly = load_stat_inputs(context)
            if pbp.empty:
                raise ValueError("No nflverse play-by-play rows returned.")
            return context, pbp, weekly
        except Exception as exc:
            attempted.append(f"{context.season}: {type(exc).__name__}: {exc}")
    raise RuntimeError("Unable to load nflverse stat data. Attempts: " + " | ".join(attempted))


def add_rank_columns(frame: pd.DataFrame, column: str, rank_column: str, higher_better: bool) -> None:
    frame[rank_column] = frame[column].rank(method="min", ascending=not higher_better).astype("Int64")


def compute_team_metrics(pbp: pd.DataFrame, weekly: pd.DataFrame) -> pd.DataFrame:
    offense = pbp[pbp["posteam"] != ""].copy()
    offense["is_off_play"] = (
        offense["pass"].fillna(0).eq(1)
        | offense["rush"].fillna(0).eq(1)
        | offense["sack"].fillna(0).eq(1)
    )
    offense = offense[offense["is_off_play"]].copy()

    games_played = offense.groupby("posteam")["game_id"].nunique().rename("games_played").reset_index().rename(columns={"posteam": "team"})

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
        & games["home_score"].notna() & games["away_score"].notna()
    ].copy()
    for _, row in history.iterrows():
        home, away = row["home_team"], row["away_team"]
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


def load_field_provenance() -> Dict[str, str]:
    if not COMBINED_DICTIONARY_PATH.exists():
        return {}
    dictionary = pd.read_csv(COMBINED_DICTIONARY_PATH)
    needed = {"wind", "special_teams_tds", "epa", "passing_yards", "rushing_yards", "sack"}
    subset = dictionary[dictionary["field"].astype(str).isin(needed)]
    return dict(zip(subset["field"].astype(str), subset["description"].astype(str)))


def format_line(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    f = float(value)
    return f"+{f:.1f}" if f > 0 else f"{f:.1f}"


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
        return "Lean – doesn't meet our edge criteria to fully bet"
    return "Bet"


def format_title_kickoff_date(kickoff: object) -> str:
    return "N/A" if kickoff is None or pd.isna(kickoff) else pd.Timestamp(kickoff).strftime("%m/%d/%Y")


def parse_table_headers(table: BeautifulSoup) -> List[str]:
    return [cell.get_text(" ", strip=True) for cell in table.find_all("th")]


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
    deduped, seen = [], set()
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
        if not headers or not any("name" in h or "player" in h for h in headers):
            continue
        for row in table.find_all("tr")[1:]:
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


def fetch_espn_starter_injuries(team: str, slug: Optional[str], session: requests.Session, debug_enabled: bool) -> TeamInjuryReport:
    report = TeamInjuryReport(team=team)
    if not slug:
        report.status = "no_slug"
        return report
    injuries_url = f"https://www.espn.com/nfl/team/injuries/_/name/{slug}"
    depth_url = f"https://www.espn.com/nfl/team/depth/_/name/{slug}"
    try:
        injury_rows = parse_injuries(session.get(injuries_url, timeout=REQUEST_TIMEOUT).text)
    except Exception:
        report.status = "injury_fetch_failed"
        return report
    try:
        starters = parse_depth_chart_starters(session.get(depth_url, timeout=REQUEST_TIMEOUT).text)
    except Exception:
        report.status = "depth_fetch_failed"
        return report
    starter_keys = {normalize_name(name) for name in starters}
    report.starters = [injury for injury in injury_rows if normalize_name(injury.player) in starter_keys]
    report.status = "ok_starters_found" if report.starters else "no_starter_match"
    return report


def prepare_games(spreads: pd.DataFrame, model: pd.DataFrame, requested_teams: Optional[Iterable[str]]) -> pd.DataFrame:
    merged = spreads.merge(model, left_on="team", right_on="Team", how="left", validate="one_to_one")
    if requested_teams:
        requested = {team.upper() for team in requested_teams}
        eligible_games = merged[merged["team"].isin(requested)]["game"].unique()
        merged = merged[merged["game"].isin(eligible_games)].copy()
    if merged.empty:
        raise ValueError("No matchup rows remain after filtering")
    return merged


def model_ranks(model_df: pd.DataFrame) -> pd.DataFrame:
    df = model_df.copy()
    df.columns = [c.strip() for c in df.columns]

    def rk(col: str, higher_better: bool) -> pd.Series:
        if col not in df.columns:
            return pd.Series([pd.NA] * len(df), index=df.index, dtype="Int64")
        return df[col].rank(method="min", ascending=not higher_better).astype("Int64")

    out = pd.DataFrame({"team": df["Team"].astype(str).str.upper()})
    out["off_rank"] = rk("Offensive Expected Points (Season)", True)
    out["def_rank"] = rk("Defensive Expected Points (Season)", False)
    out["off_sr_rank"] = rk("Offensive Success Rate (%)", True)
    out["def_sr_rank"] = rk("Defensive Success Rate (%)", True)
    out["qb10_rank"] = rk("QB Expected Points Added (Last 10 games)", True)
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


def clean_qb(value: object, qb_crosswalk: Optional[Dict[str, str]] = None) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    if qb_crosswalk:
        mapped = qb_crosswalk.get(s)
        if mapped:
            return mapped
    return re.sub(r"^([A-Za-z])\.([A-Za-z])", r"\1. \2", s)


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
    return f"*Model assumes {' and '.join(parts)} under center. QB news moves these numbers fast — check inactives before you bet.*"


def qb_xfactor(bet_name, bet_m, opp_name, opp_m, total_teams, seed, qb_crosswalk=None) -> List[str]:
    out: List[str] = []
    for team_name, mm in [(bet_name, bet_m), (opp_name, opp_m)]:
        name = clean_qb(mm.get("qbname"), qb_crosswalk)
        rank = _safe_rank(mm, "qb10_rank")
        if not name or rank is None:
            continue
        if rank <= 5:
            out.append(f"{name} has been one of the most valuable quarterbacks in football over his last 10 games ({_ord(rank)} of {total_teams}) - a real tailwind for {team_name}.")
        elif rank >= total_teams - 4:
            out.append(f"{name} sits {_ord(rank)} of {total_teams} in QB value over his last 10 games - the kind of play that caps {team_name}'s ceiling.")
    return out


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


def build_article(
    game, game_rows, metrics, records, team_names, schedule_row,
    stat_context, provenance, injury_reports, edge_game_count, model_ranks_df=None, qb_crosswalk=None,
) -> Tuple[str, Dict[str, object]]:
    away_team, home_team = game.split("@")
    rows_by_team = {row["team"]: row for _, row in game_rows.iterrows()}
    away_row, home_row = rows_by_team[away_team], rows_by_team[home_team]
    metrics_indexed = metrics.set_index("team")
    mr = model_ranks_df

    def m(team):
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
    kickoff_title_label = format_title_kickoff_date(kickoff)

    favorite_row = game_rows.sort_values("market_line").iloc[0]
    dog_row = game_rows.sort_values("market_line").iloc[-1]
    fav_edge = resolve_edge_numeric(favorite_row)
    dog_edge = resolve_edge_numeric(dog_row)
    verdict_row = favorite_row if fav_edge is not None and (dog_edge is None or fav_edge >= dog_edge) else dog_row
    other_row = dog_row if verdict_row is favorite_row else favorite_row
    has_bet = (resolve_edge_numeric(verdict_row) or 0) >= 0.04

    bet_team = verdict_row["team"]
    opp_team = other_row["team"]
    bet_name = team_names.get(bet_team, bet_team)
    opp_name = team_names.get(opp_team, opp_team)
    bet_m, opp_m = m(bet_team), m(opp_team)
    total_teams = int(len(mr)) if mr is not None and len(mr) else 32

    sections: List[str] = [f"# {away_name} vs {home_name} Prediction For {kickoff_title_label}", ""]

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

    # Edge column removed here
    sections.extend(
        [
            "| Team name | Best Spread/Odds | Best Book | Model Cover% | BTB Advice |",
            "|---|---|---|---|---|",
        ]
    )
    sections.extend(
        [f"| {team} | {spread_odds} | {book} | {cover} | {call} |" for team, spread_odds, book, cover, call in matchup_rows]
    )

    starters_note = assumed_starters(bet_name, bet_m, opp_name, opp_m, qb_crosswalk=qb_crosswalk)
    if starters_note:
        sections.extend(["", starters_note])

    tape = build_tale_of_tape(bet_name, bet_m, opp_name, opp_m, total_teams, qb_crosswalk=qb_crosswalk)
    if tape:
        sections.extend(["", "## Why The Pick", ""] + tape)

    qb_lines = qb_xfactor(bet_name, bet_m, opp_name, opp_m, total_teams, game, qb_crosswalk=qb_crosswalk)
    if qb_lines:
        sections.extend(["", "## Quarterback X-Factor"] + qb_lines)

    payload = {"game": game, "away_team": away_team, "home_team": home_team, "has_bet": has_bet}
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
    qb_crosswalk = load_qb_crosswalk()

    weekly_merged = spreads.merge(model, left_on="team", right_on="Team", how="left", validate="one_to_one")
    merged = prepare_games(spreads, model, args.teams)

    team_names = load_team_names()
    games_schedule = load_games_schedule()
    records = build_records(games_schedule, season, week)
    stat_context, pbp, weekly = load_stat_inputs_with_fallback(season, week)
    metrics = compute_team_metrics(pbp, weekly)
    provenance = load_field_provenance()

    games_with_edges = int(weekly_merged[weekly_merged["edge_numeric"] >= 0.04]["game"].nunique())

    team_slug_map = {team: str(team).lower() for team in model["Team"].dropna().unique()}
    weekly_dir = output_root / f"week_{week}"
    safe_mkdir(weekly_dir)

    combined_articles: List[str] = []
    payload = {"generated_at_utc": datetime.now(UTC).isoformat(), "season": season, "week": week, "articles": []}

    for game, game_rows in merged.groupby("game", sort=True):
        away_team, home_team = game.split("@")
        injury_reports = {
            team: fetch_espn_starter_injuries(team, team_slug_map.get(team), session, debug_enabled=args.espn_debug)
            for team in (away_team, home_team)
        }
        article, article_payload = build_article(
            game,
            game_rows.copy(),
            metrics.copy(),
            records,
            team_names,
            None,
            stat_context,
            provenance,
            injury_reports,
            games_with_edges,
            model_rank_lookup,
            qb_crosswalk=qb_crosswalk,
        )
        game_slug = slugify_game(game)
        (weekly_dir / f"{game_slug}.md").write_text(article, encoding="utf-8")
        combined_articles.append(article.rstrip())
        article_payload["article_path"] = f"{game_slug}.md"
        payload["articles"].append(article_payload)

    (weekly_dir / "weekly_matchup_articles.md").write_text("\n\n---\n\n".join(combined_articles) + "\n", encoding="utf-8")
    (weekly_dir / "weekly_matchup_articles.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Generated {len(combined_articles)} matchup article(s) for week {week} in {weekly_dir}")


if __name__ == "__main__":
    main()
