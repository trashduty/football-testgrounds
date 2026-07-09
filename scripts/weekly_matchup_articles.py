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


def load_qb_crosswalk() -> Dict[str, str]:
    """Load QB short-name -> full-name mapping from data-raw/QB Crosswalk.csv."""
    if not QB_CROSSWALK_PATH.exists():
        return {}
    crosswalk = pd.read_csv(QB_CROSSWALK_PATH)
    short_col = "starter_player_name"
    full_col = "Full Name"
    if short_col not in crosswalk.columns or full_col not in crosswalk.columns:
        return {}
    valid = crosswalk[[short_col, full_col]].dropna()
    valid[short_col] = valid[short_col].astype(str).str.strip()
    valid[full_col] = valid[full_col].astype(str).str.strip()
    return dict(zip(valid[short_col], valid[full_col]))


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

# ... (all unchanged code omitted for brevity in explanation — keep your existing functions as-is) ...

def clean_qb(value: object, qb_crosswalk: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Normalize QB display name.

    - If value is in crosswalk (e.g., 'J.Allen'), return full name ('Josh Allen').
    - Else fallback to spacing normalization ('J.Allen' -> 'J. Allen').
    """
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


def assumed_starters(bet_name, bet_m, opp_name, opp_m, qb_crosswalk=None) -> Optional[str]:
    """Trust line: name the QBs the model assumes, so readers can sanity-check
    against injury news before betting. NFL QB attrition is high — this matters."""
    a = clean_qb(bet_m.get("qbname"), qb_crosswalk)
    b = clean_qb(opp_m.get("qbname"), qb_crosswalk)
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


def qb_xfactor(bet_name, bet_m, opp_name, opp_m, total_teams, seed, qb_crosswalk=None) -> List[str]:
    """A named QB callout, fired only when a starter's last-10 EPA is extreme."""
    out: List[str] = []
    for team_name, mm in [(bet_name, bet_m), (opp_name, opp_m)]:
        name = clean_qb(mm.get("qbname"), qb_crosswalk)
        rank = _safe_rank(mm, "qb10_rank")
        if not name or rank is None:
            continue
        if rank <= 5:
            out.append(
                _pick(
                    [f"{name} has been one of the most valuable quarterbacks in "
                     f"football over his last 10 games ({_ord(rank)} of {total_teams}) "
                     f" - a real tailwind for {team_name}.",
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
                     f"last 10 games - the kind of play that caps {poss(team_name)} "
                     f"ceiling.",
                     f"{name} has been among the least productive starters in the league "
                     f"lately ({_ord(rank)} of {total_teams}), a real drag on {team_name}."],
                    seed + team_name,
                )
            )
    return out


def build_tale_of_tape(bet_name, bet_m, opp_name, opp_m, total_teams, qb_crosswalk=None) -> List[str]:
    """Numbers belong in a table, not the prose. Lead with the model's own fields."""
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


def build_article(
    game, game_rows, metrics, records, team_names, schedule_row,
    stat_context, provenance, injury_reports, edge_game_count, model_ranks_df=None,
    qb_crosswalk=None,
) -> Tuple[str, Dict[str, object]]:
    # ... unchanged setup code ...

    # initial matchup table (EDGE COLUMN REMOVED)
    sections.extend(
        [
            "| Team name | Best Spread/Odds | Best Book | Model Cover% | BTB Advice |",
            "|---|---|---|---|---|",
        ]
    )
    sections.extend(
        [
            f"| {team} | {spread_odds} | {book} | {cover} | {call} |"
            for team, spread_odds, book, cover, _edge, call in matchup_rows
        ]
    )

    # ... unchanged code ...

    starters_note = assumed_starters(bet_name, bet_m, opp_name, opp_m, qb_crosswalk=qb_crosswalk)
    if starters_note:
        sections.extend(["", starters_note])

    tape = build_tale_of_tape(bet_name, bet_m, opp_name, opp_m, total_teams, qb_crosswalk=qb_crosswalk)
    # ... unchanged code ...

    qb_lines = qb_xfactor(bet_name, bet_m, opp_name, opp_m, total_teams, seed, qb_crosswalk=qb_crosswalk)
    if qb_lines:
        sections.extend(["", "## Quarterback X-Factor"] + qb_lines)

    # ... unchanged return payload ...


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
            qb_crosswalk=qb_crosswalk,
        )
        game_slug = slugify_game(game)
        away_full = team_names.get(away_team, away_team)
        home_full = team_names.get(home_team, home_team)
        front_matter = (
            f"---\n"
            f"layout: article\n"
            f"title: \"{away_full} vs {home_full}\"\n"
            f"week: {week}\n"
            f"season: {season}\n"
            f"permalink: /outputs/matchup_articles/week_{week}/{game_slug}/\n"
            f"---\n\n"
        )
        article_with_front_matter = front_matter + article
        combined_articles.append(article.rstrip())
        article_payload["article_path"] = f"{game_slug}.md"
        payload["articles"].append(article_payload)
        (weekly_dir / f"{game_slug}.md").write_text(article_with_front_matter, encoding="utf-8")

    combined_path = weekly_dir / "weekly_matchup_articles.md"
    combined_path.write_text("\n\n---\n\n".join(combined_articles) + "\n", encoding="utf-8")
    (weekly_dir / "weekly_matchup_articles.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )

    print(f"Generated {len(combined_articles)} matchup article(s) for week {week} in {weekly_dir}")


if __name__ == "__main__":
    main()
