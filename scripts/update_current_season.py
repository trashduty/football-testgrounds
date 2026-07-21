#!/usr/bin/env python3
"""Update current-season CFB EPA statistics from the CFBD REST API.

The script:
1. Reads the historical processed Parquet outputs created by Workflow 1.
2. Pulls current-season games and FBS teams from CFBD.
3. Pulls play-by-play only for weeks that have played/scored games.
4. Normalizes CFBD v2 response fields into the historical database schema.
5. Builds current team-game, team-season, league-average, and ranking tables.
6. Writes current-only and combined historical+current Parquet/CSV outputs.

CFBD commonly labels expected-points-added values as PPA. Within this project,
that play-level value is stored in the canonical EPA fields to match the
historical database produced from the cfbfastR RDS files.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests


SEASON = int(os.getenv("CURRENT_SEASON", "2026"))
API_KEY = os.getenv("CFBD_API_KEY", "").strip()
API_BASE = os.getenv("CFBD_API_BASE", "https://api.collegefootballdata.com").rstrip("/")
PROCESSED_DIR = Path(os.getenv("PROCESSED_DIR", "data/processed"))
CURRENT_DIR = Path(os.getenv("CURRENT_DIR", "data/current"))
EXCLUDE_GARBAGE_TIME = os.getenv("EXCLUDE_GARBAGE_TIME", "false").lower() == "true"
REQUEST_TIMEOUT = int(os.getenv("CFBD_REQUEST_TIMEOUT", "90"))

HIST_TEAM_GAME = PROCESSED_DIR / "historical_team_game_stats.parquet"
HIST_TEAM_SEASON = PROCESSED_DIR / "historical_team_season_stats.parquet"
HIST_LEAGUE = PROCESSED_DIR / "historical_league_season_stats.parquet"
HIST_RANKINGS = PROCESSED_DIR / "historical_team_rankings.parquet"

METRIC_CONFIG = [
    ("off_epa_per_play", "off_plays", True, 100),
    ("off_epa_per_rush", "off_rush_plays", True, 50),
    ("off_epa_per_pass", "off_pass_plays", True, 50),
    ("off_success_rate", "off_plays", True, 100),
    ("off_rush_success_rate", "off_rush_plays", True, 50),
    ("off_pass_success_rate", "off_pass_plays", True, 50),
    ("def_epa_allowed_per_play", "def_plays", False, 100),
    ("def_epa_allowed_per_rush", "def_rush_plays", False, 50),
    ("def_epa_allowed_per_pass", "def_pass_plays", False, 50),
    ("def_success_rate_allowed", "def_plays", False, 100),
    ("def_rush_success_rate_allowed", "def_rush_plays", False, 50),
    ("def_pass_success_rate_allowed", "def_pass_plays", False, 50),
]

PASS_TYPE_TERMS = (
    "pass",
    "sack",
    "interception",
)
RUSH_TYPE_TERMS = (
    "rush",
    "rushing",
)
EXCLUDED_TYPE_TERMS = (
    "kickoff",
    "punt",
    "field goal",
    "extra point",
    "two point",
    "timeout",
    "end period",
    "end of",
    "penalty",
    "coin toss",
)


def log(message: str) -> None:
    print(message, flush=True)


def snake_case(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    return value.strip("_").lower()


def normalize_record(value: Any) -> Any:
    if isinstance(value, dict):
        return {snake_case(str(k)): normalize_record(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_record(v) for v in value]
    return value


def first_value(record: dict[str, Any], candidates: Iterable[str], default: Any = None) -> Any:
    for candidate in candidates:
        if candidate in record and record[candidate] is not None:
            return record[candidate]
    return default


def numeric(value: Any) -> float:
    try:
        if value is None or value == "":
            return math.nan
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def integer(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return value == 1
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "completed", "final"}


class CFBDClient:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise RuntimeError("CFBD_API_KEY environment variable is not set.")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": "cfb-query-database/1.0",
            }
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        url = f"{API_BASE}{path}"
        last_error: Exception | None = None

        for attempt in range(1, 4):
            try:
                response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
                if response.status_code == 401:
                    raise RuntimeError("CFBD returned 401 Unauthorized. Verify CFBD_API_KEY.")
                if response.status_code == 403:
                    raise RuntimeError(
                        f"CFBD returned 403 for {path}. Your API tier may not include this endpoint."
                    )
                if response.status_code == 429:
                    raise RuntimeError("CFBD API monthly or temporary request limit reached (429).")
                response.raise_for_status()
                payload = response.json()
                if payload is None:
                    return []
                if not isinstance(payload, list):
                    raise RuntimeError(f"Unexpected non-list response from {path}: {type(payload).__name__}")
                return [normalize_record(item) for item in payload if isinstance(item, dict)]
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt == 3:
                    break
                delay = 2 ** attempt
                log(f"  Request failed for {path} (attempt {attempt}/3): {exc}; retrying in {delay}s")
                time.sleep(delay)

        raise RuntimeError(f"CFBD request failed for {path}: {last_error}")


def ensure_historical_files() -> None:
    required = [HIST_TEAM_GAME, HIST_TEAM_SEASON, HIST_LEAGUE, HIST_RANKINGS]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Workflow 1 outputs are missing. Run Build Historical CFB Database first. Missing: "
            + ", ".join(missing)
        )


def extract_team_name(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        normalized = normalize_record(value)
        name = first_value(normalized, ("school", "team", "name"))
        return str(name).strip() if name else None
    return None


def extract_team_metadata(team_records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in team_records:
        name = extract_team_name(record)
        if not name:
            continue
        conference = first_value(record, ("conference", "conference_abbreviation"))
        if isinstance(conference, dict):
            conference = first_value(normalize_record(conference), ("abbreviation", "name", "short_name"))
        classification = first_value(record, ("classification", "division"), "fbs")
        rows.append(
            {
                "team": name,
                "conference": conference,
                "division": str(classification).lower() if classification else "fbs",
            }
        )
    if not rows:
        return pd.DataFrame(columns=["team", "conference", "division"])
    return pd.DataFrame(rows).drop_duplicates(subset=["team"], keep="last")


def parse_games(records: list[dict[str, Any]], fbs_teams: set[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for game in records:
        game_id = first_value(game, ("id", "game_id"))
        week = integer(first_value(game, ("week",)))
        season = integer(first_value(game, ("season", "year"), SEASON))
        home_team = extract_team_name(first_value(game, ("home_team", "home")))
        away_team = extract_team_name(first_value(game, ("away_team", "away")))
        home_points = numeric(first_value(game, ("home_points", "home_score")))
        away_points = numeric(first_value(game, ("away_points", "away_score")))
        completed_field = first_value(game, ("completed", "status", "game_status"))
        completed = truthy(completed_field) or (not math.isnan(home_points) and not math.isnan(away_points))

        if game_id is None or week is None or not home_team or not away_team:
            continue
        if fbs_teams and home_team not in fbs_teams and away_team not in fbs_teams:
            continue

        start_date = first_value(game, ("start_date", "start_time", "start_date_time"))
        season_type = first_value(game, ("season_type",), "regular")
        venue_value = first_value(game, ("venue",))
        if isinstance(venue_value, dict):
            venue_value = first_value(normalize_record(venue_value), ("name",))

        rows.append(
            {
                "game_id": str(game_id),
                "season": season,
                "week": week,
                "season_type": str(season_type).lower() if season_type else "regular",
                "start_date": start_date,
                "completed": completed,
                "neutral_site": first_value(game, ("neutral_site",)),
                "conference_game": first_value(game, ("conference_game",)),
                "venue": venue_value,
                "home_team": home_team,
                "away_team": away_team,
                "home_conference": first_value(game, ("home_conference",)),
                "away_conference": first_value(game, ("away_conference",)),
                "home_points": home_points,
                "away_points": away_points,
            }
        )

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["start_date"] = pd.to_datetime(frame["start_date"], errors="coerce", utc=True).dt.date
    return frame.drop_duplicates(subset=["game_id"], keep="last")


def play_type_text(play: dict[str, Any]) -> str:
    value = first_value(play, ("play_type", "type", "play_type_text", "play_type_name"), "")
    if isinstance(value, dict):
        value = first_value(normalize_record(value), ("text", "name", "abbreviation"), "")
    return str(value).strip().lower()


def classify_play(play: dict[str, Any]) -> tuple[int, int]:
    type_text = play_type_text(play)
    play_text = str(first_value(play, ("play_text", "text"), "")).lower()
    combined = f"{type_text} {play_text}"

    if any(term in type_text for term in EXCLUDED_TYPE_TERMS):
        return 0, 0
    if any(term in type_text for term in PASS_TYPE_TERMS):
        return 0, 1
    if any(term in type_text for term in RUSH_TYPE_TERMS):
        return 1, 0

    # Conservative fallbacks for occasional generic/missing play-type labels.
    if "sacked" in play_text or "pass " in play_text or "intercepted" in play_text:
        return 0, 1
    if "run for" in play_text or "rush for" in play_text:
        return 1, 0
    return 0, 0


def parse_plays(records: list[dict[str, Any]], valid_game_ids: set[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for play in records:
        game_id = first_value(play, ("game_id", "gameid"))
        if game_id is None or (valid_game_ids and str(game_id) not in valid_game_ids):
            continue

        offense = extract_team_name(first_value(play, ("offense", "offense_team", "pos_team")))
        defense = extract_team_name(first_value(play, ("defense", "defense_team", "def_pos_team")))
        ppa = numeric(first_value(play, ("ppa", "epa")))
        if not offense or not defense or math.isnan(ppa):
            continue

        rush_flag, pass_flag = classify_play(play)
        if rush_flag == 0 and pass_flag == 0:
            continue

        rows.append(
            {
                "game_id": str(game_id),
                "offense_team": offense,
                "defense_team": defense,
                "epa_value": ppa,
                "rush_flag": rush_flag,
                "pass_flag": pass_flag,
                "success_flag": int(ppa > 0),
            }
        )
    return pd.DataFrame(rows)


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator.div(denominator.where(denominator > 0))


def game_context(games: pd.DataFrame, team_meta: pd.DataFrame) -> pd.DataFrame:
    if games.empty:
        return pd.DataFrame()
    meta = team_meta.set_index("team") if not team_meta.empty else pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for game in games.to_dict("records"):
        for side in ("home", "away"):
            other = "away" if side == "home" else "home"
            team = game[f"{side}_team"]
            opponent = game[f"{other}_team"]
            team_conf = game.get(f"{side}_conference")
            opponent_conf = game.get(f"{other}_conference")
            division = "fbs" if team in meta.index else None
            if team in meta.index:
                if pd.isna(team_conf) or team_conf is None:
                    team_conf = meta.at[team, "conference"]
                division = meta.at[team, "division"]
            rows.append(
                {
                    "game_id": game["game_id"],
                    "season": game["season"],
                    "week": game["week"],
                    "season_type": game["season_type"],
                    "start_date": game["start_date"],
                    "team": team,
                    "opponent": opponent,
                    "home_away": side,
                    "team_conference": team_conf,
                    "team_division": division,
                    "opponent_conference": opponent_conf,
                    "opponent_division": "fbs" if opponent in meta.index else None,
                    "points_for": game[f"{side}_points"],
                    "points_against": game[f"{other}_points"],
                    "neutral_site": game.get("neutral_site"),
                    "conference_game": game.get("conference_game"),
                    "venue": game.get("venue"),
                }
            )
    return pd.DataFrame(rows)


def aggregate_side(plays: pd.DataFrame, team_column: str, prefix: str) -> pd.DataFrame:
    if plays.empty:
        return pd.DataFrame()

    group_cols = ["season", "week", "game_id", team_column]
    grouped = plays.groupby(group_cols, dropna=False)
    result = grouped.agg(
        plays=("epa_value", "size"),
        epa_total=("epa_value", "sum"),
        successes=("success_flag", "sum"),
        rush_plays=("rush_flag", "sum"),
        pass_plays=("pass_flag", "sum"),
    ).reset_index()

    rush = (
        plays.loc[plays["rush_flag"] == 1]
        .groupby(group_cols, dropna=False)
        .agg(rush_epa_total=("epa_value", "sum"), rush_successes=("success_flag", "sum"))
        .reset_index()
    )
    passing = (
        plays.loc[plays["pass_flag"] == 1]
        .groupby(group_cols, dropna=False)
        .agg(pass_epa_total=("epa_value", "sum"), pass_successes=("success_flag", "sum"))
        .reset_index()
    )
    result = result.merge(rush, on=group_cols, how="left").merge(passing, on=group_cols, how="left")
    result = result.fillna(
        {
            "rush_epa_total": 0.0,
            "rush_successes": 0,
            "pass_epa_total": 0.0,
            "pass_successes": 0,
        }
    )
    result = result.rename(columns={team_column: "team"})

    if prefix == "off":
        rename = {
            "plays": "off_plays",
            "epa_total": "off_epa_total",
            "successes": "off_successes",
            "rush_plays": "off_rush_plays",
            "rush_epa_total": "off_rush_epa_total",
            "rush_successes": "off_rush_successes",
            "pass_plays": "off_pass_plays",
            "pass_epa_total": "off_pass_epa_total",
            "pass_successes": "off_pass_successes",
        }
    else:
        rename = {
            "plays": "def_plays",
            "epa_total": "def_epa_allowed_total",
            "successes": "def_successes_allowed",
            "rush_plays": "def_rush_plays",
            "rush_epa_total": "def_rush_epa_allowed_total",
            "rush_successes": "def_rush_successes_allowed",
            "pass_plays": "def_pass_plays",
            "pass_epa_total": "def_pass_epa_allowed_total",
            "pass_successes": "def_pass_successes_allowed",
        }
    return result.rename(columns=rename)


def add_rate_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["off_epa_per_play"] = safe_divide(frame["off_epa_total"], frame["off_plays"])
    frame["off_epa_per_rush"] = safe_divide(frame["off_rush_epa_total"], frame["off_rush_plays"])
    frame["off_epa_per_pass"] = safe_divide(frame["off_pass_epa_total"], frame["off_pass_plays"])
    frame["off_success_rate"] = safe_divide(frame["off_successes"], frame["off_plays"])
    frame["off_rush_success_rate"] = safe_divide(frame["off_rush_successes"], frame["off_rush_plays"])
    frame["off_pass_success_rate"] = safe_divide(frame["off_pass_successes"], frame["off_pass_plays"])
    frame["def_epa_allowed_per_play"] = safe_divide(frame["def_epa_allowed_total"], frame["def_plays"])
    frame["def_epa_allowed_per_rush"] = safe_divide(frame["def_rush_epa_allowed_total"], frame["def_rush_plays"])
    frame["def_epa_allowed_per_pass"] = safe_divide(frame["def_pass_epa_allowed_total"], frame["def_pass_plays"])
    frame["def_success_rate_allowed"] = safe_divide(frame["def_successes_allowed"], frame["def_plays"])
    frame["def_rush_success_rate_allowed"] = safe_divide(frame["def_rush_successes_allowed"], frame["def_rush_plays"])
    frame["def_pass_success_rate_allowed"] = safe_divide(frame["def_pass_successes_allowed"], frame["def_pass_plays"])
    return frame


def build_team_game(games: pd.DataFrame, plays: pd.DataFrame, team_meta: pd.DataFrame) -> pd.DataFrame:
    plays = plays.merge(games[["game_id", "season", "week"]], on="game_id", how="inner")
    offense = aggregate_side(plays, "offense_team", "off")
    defense = aggregate_side(plays, "defense_team", "def")
    stats = offense.merge(defense, on=["season", "week", "game_id", "team"], how="outer")
    context = game_context(games, team_meta)
    result = context.merge(stats, on=["season", "week", "game_id", "team"], how="right")

    count_cols = [
        "off_plays", "off_successes", "off_rush_plays", "off_rush_successes", "off_pass_plays", "off_pass_successes",
        "def_plays", "def_successes_allowed", "def_rush_plays", "def_rush_successes_allowed", "def_pass_plays", "def_pass_successes_allowed",
    ]
    total_cols = [
        "off_epa_total", "off_rush_epa_total", "off_pass_epa_total",
        "def_epa_allowed_total", "def_rush_epa_allowed_total", "def_pass_epa_allowed_total",
    ]
    for col in count_cols + total_cols:
        if col not in result:
            result[col] = 0
        result[col] = result[col].fillna(0)
    result[count_cols] = result[count_cols].astype("int64")
    return add_rate_columns(result).sort_values(["season", "week", "game_id", "team"])


def mode_nonmissing(series: pd.Series) -> Any:
    values = series.dropna()
    values = values[values.astype(str).str.len() > 0]
    if values.empty:
        return None
    modes = values.mode()
    return modes.iloc[0] if not modes.empty else values.iloc[0]


def build_team_season(team_game: pd.DataFrame) -> pd.DataFrame:
    sum_cols = [
        "points_for", "points_against",
        "off_plays", "off_epa_total", "off_successes",
        "off_rush_plays", "off_rush_epa_total", "off_rush_successes",
        "off_pass_plays", "off_pass_epa_total", "off_pass_successes",
        "def_plays", "def_epa_allowed_total", "def_successes_allowed",
        "def_rush_plays", "def_rush_epa_allowed_total", "def_rush_successes_allowed",
        "def_pass_plays", "def_pass_epa_allowed_total", "def_pass_successes_allowed",
    ]
    grouped = team_game.groupby(["season", "team"], dropna=False)
    result = grouped[sum_cols].sum(min_count=1).reset_index()
    result["games"] = grouped["game_id"].nunique().values
    result["conference"] = grouped["team_conference"].agg(mode_nonmissing).values
    result["division"] = grouped["team_division"].agg(mode_nonmissing).values
    result["points_per_game"] = safe_divide(result["points_for"], result["games"])
    result["points_allowed_per_game"] = safe_divide(result["points_against"], result["games"])
    return add_rate_columns(result).sort_values(["season", "team"])


def build_league(team_season: pd.DataFrame) -> pd.DataFrame:
    fbs = team_season.loc[team_season["division"].astype(str).str.lower() == "fbs"].copy()
    if fbs.empty:
        raise RuntimeError("No FBS current-season rows were identified.")
    sum_cols = [
        "games", "off_plays", "off_epa_total", "off_rush_plays", "off_rush_epa_total",
        "off_pass_plays", "off_pass_epa_total", "off_successes", "off_rush_successes", "off_pass_successes",
        "def_plays", "def_epa_allowed_total", "def_rush_plays", "def_rush_epa_allowed_total",
        "def_pass_plays", "def_pass_epa_allowed_total", "def_successes_allowed",
        "def_rush_successes_allowed", "def_pass_successes_allowed",
    ]
    result = fbs.groupby("season", as_index=False)[sum_cols].sum(min_count=1)
    result["teams"] = fbs.groupby("season")["team"].nunique().values
    return add_rate_columns(result).sort_values("season")


def build_rankings(team_season: pd.DataFrame, league: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    fbs = team_season.loc[team_season["division"].astype(str).str.lower() == "fbs"].copy()
    parts: list[pd.DataFrame] = []

    for metric, sample_col, higher_is_better, minimum_sample in METRIC_CONFIG:
        part = fbs[["season", "team", "conference", "division", metric, sample_col]].copy()
        part = part.rename(columns={metric: "value", sample_col: "sample_size"})
        part["metric"] = metric
        part["minimum_sample"] = minimum_sample
        part["higher_is_better"] = higher_is_better
        league_map = league.set_index("season")[metric]
        part["league_average"] = part["season"].map(league_map)
        part["difference_from_average"] = part["value"] - part["league_average"]
        part["qualifies"] = part["value"].notna() & (part["sample_size"] >= minimum_sample)
        part["rank"] = np.nan
        part["teams_ranked"] = 0
        part["percentile"] = np.nan

        for season, idx in part.groupby("season").groups.items():
            qualifying_idx = [i for i in idx if bool(part.at[i, "qualifies"])]
            count = len(qualifying_idx)
            part.loc[idx, "teams_ranked"] = count
            if count == 0:
                continue
            ranks = part.loc[qualifying_idx, "value"].rank(
                method="min", ascending=not higher_is_better
            )
            part.loc[qualifying_idx, "rank"] = ranks
            if count == 1:
                part.loc[qualifying_idx, "percentile"] = 100.0
            else:
                part.loc[qualifying_idx, "percentile"] = 100.0 * (count - ranks) / (count - 1)
        parts.append(part)

    rankings = pd.concat(parts, ignore_index=True)
    rankings = rankings.sort_values(["season", "metric", "rank", "team"], na_position="last")

    wide = team_season.copy()
    for metric, _, _, _ in METRIC_CONFIG:
        lookup = rankings.loc[rankings["metric"] == metric, ["season", "team", "rank", "percentile"]].rename(
            columns={"rank": f"{metric}_rank", "percentile": f"{metric}_percentile"}
        )
        wide = wide.merge(lookup, on=["season", "team"], how="left")
    return rankings, wide.sort_values(["season", "team"])


def align_and_combine(historical: pd.DataFrame, current: pd.DataFrame, season: int) -> pd.DataFrame:
    historical = historical.loc[historical["season"] != season].copy()
    columns = list(dict.fromkeys([*historical.columns.tolist(), *current.columns.tolist()]))
    return pd.concat(
        [historical.reindex(columns=columns), current.reindex(columns=columns)],
        ignore_index=True,
    )


def write_table(frame: pd.DataFrame, stem: str, directory: Path = PROCESSED_DIR) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    parquet_path = directory / f"{stem}.parquet"
    csv_path = directory / f"{stem}.csv.gz"
    frame.to_parquet(parquet_path, index=False, compression="zstd")
    with gzip.open(csv_path, "wt", encoding="utf-8", newline="") as handle:
        frame.to_csv(handle, index=False)
    log(f"  Wrote {parquet_path} ({len(frame):,} rows)")


def copy_historical_to_combined() -> None:
    mapping = {
        HIST_TEAM_GAME: PROCESSED_DIR / "combined_team_game_stats.parquet",
        HIST_TEAM_SEASON: PROCESSED_DIR / "combined_team_season_stats.parquet",
        HIST_LEAGUE: PROCESSED_DIR / "combined_league_season_stats.parquet",
        HIST_RANKINGS: PROCESSED_DIR / "combined_team_rankings.parquet",
    }
    for source, target in mapping.items():
        shutil.copy2(source, target)
        frame = pd.read_parquet(source)
        with gzip.open(target.with_suffix(".csv.gz"), "wt", encoding="utf-8", newline="") as handle:
            frame.to_csv(handle, index=False)
        log(f"  Published historical-only combined file: {target}")


def main() -> int:
    ensure_historical_files()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_DIR.mkdir(parents=True, exist_ok=True)

    log(f"Updating CFB statistics for {SEASON} ...")
    client = CFBDClient(API_KEY)

    log("Fetching FBS teams ...")
    team_records = client.get("/teams/fbs", {"year": SEASON})
    team_meta = extract_team_metadata(team_records)
    fbs_teams = set(team_meta["team"].dropna().astype(str))
    log(f"  FBS teams returned: {len(fbs_teams):,}")

    log("Fetching season games ...")
    game_records = client.get("/games", {"year": SEASON, "seasonType": "both"})
    games = parse_games(game_records, fbs_teams)
    log(f"  Games returned after normalization: {len(games):,}")

    completed_games = games.loc[games["completed"] == True].copy() if not games.empty else pd.DataFrame()
    played_weeks = sorted(completed_games["week"].dropna().astype(int).unique().tolist()) if not completed_games.empty else []
    log(f"  Weeks with completed/scored games: {played_weeks or '<none>'}")

    if not played_weeks:
        log("No completed 2026 games are available yet. Publishing historical-only combined outputs.")
        copy_historical_to_combined()
        metadata = {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "current_season": SEASON,
            "status": "preseason_no_completed_games",
            "played_weeks": [],
            "current_team_game_rows": 0,
            "current_team_season_rows": 0,
            "source": "CFBD REST API v2",
        }
        (PROCESSED_DIR / "current_update_metadata.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )
        return 0

    valid_games = completed_games.loc[completed_games["week"].isin(played_weeks)].copy()
    valid_game_ids = set(valid_games["game_id"].astype(str))
    play_frames: list[pd.DataFrame] = []

    for week in played_weeks:
        log(f"Fetching plays for week {week} ...")
        params = {
            "year": SEASON,
            "week": int(week),
            "seasonType": "both",
        }
        play_records = client.get("/plays", params)
        week_frame = parse_plays(play_records, valid_game_ids)
        log(f"  Week {week}: {len(play_records):,} API plays; {len(week_frame):,} qualifying rush/pass plays")
        if not week_frame.empty:
            week_frame["week"] = week
            play_frames.append(week_frame)
            write_table(week_frame, f"pbp_{SEASON}_week_{week:02d}", CURRENT_DIR)

    if not play_frames:
        raise RuntimeError(
            "Games were marked completed, but no qualifying plays with PPA/EPA were returned. "
            "This can indicate an API-tier restriction, a response-schema change, or delayed play data."
        )

    plays = pd.concat(play_frames, ignore_index=True).drop_duplicates()
    current_team_game = build_team_game(valid_games, plays, team_meta)
    current_team_season = build_team_season(current_team_game)
    current_league = build_league(current_team_season)
    current_rankings, current_team_season = build_rankings(current_team_season, current_league)

    historical_team_game = pd.read_parquet(HIST_TEAM_GAME)
    historical_team_season = pd.read_parquet(HIST_TEAM_SEASON)
    historical_league = pd.read_parquet(HIST_LEAGUE)
    historical_rankings = pd.read_parquet(HIST_RANKINGS)

    combined_team_game = align_and_combine(historical_team_game, current_team_game, SEASON)
    combined_team_season = align_and_combine(historical_team_season, current_team_season, SEASON)
    combined_league = align_and_combine(historical_league, current_league, SEASON)
    combined_rankings = align_and_combine(historical_rankings, current_rankings, SEASON)

    write_table(current_team_game, "current_team_game_stats")
    write_table(current_team_season, "current_team_season_stats")
    write_table(current_league, "current_league_season_stats")
    write_table(current_rankings, "current_team_rankings")
    write_table(combined_team_game, "combined_team_game_stats")
    write_table(combined_team_season, "combined_team_season_stats")
    write_table(combined_league, "combined_league_season_stats")
    write_table(combined_rankings, "combined_team_rankings")

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_season": SEASON,
        "status": "updated",
        "played_weeks": played_weeks,
        "exclude_garbage_time": EXCLUDE_GARBAGE_TIME,
        "current_team_game_rows": len(current_team_game),
        "current_team_season_rows": len(current_team_season),
        "current_ranking_rows": len(current_rankings),
        "combined_team_season_rows": len(combined_team_season),
        "source": "CFBD REST API v2",
        "methodology_note": (
            "CFBD play-level PPA is stored as canonical EPA to align with the historical database. "
            "Rush/pass classification uses the CFBD play-type label, with conservative text fallbacks."
        ),
    }
    (PROCESSED_DIR / "current_update_metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )

    alabama = current_team_season.loc[current_team_season["team"].str.lower() == "alabama"]
    if not alabama.empty:
        cols = [
            "season", "team", "games", "off_rush_plays", "off_epa_per_rush",
            "off_epa_per_rush_rank", "off_epa_per_rush_percentile",
        ]
        log("\nAlabama current-season validation:")
        log(alabama[cols].to_string(index=False))

    log("\nCurrent-season CFB statistics update completed successfully.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"ERROR: {exc}")
        raise
