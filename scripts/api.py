#!/usr/bin/env python3
"""
scripts/api.py
--------------
Flask API for CFB stats search.

Startup behaviour:
  - Loads docs/data/cfb-stats.json (pre-computed by R pipeline) into RAM.
  - Serves fast in-memory search queries (<50 ms).
  - Also exposes a /live endpoint that proxies real-time queries to the
    CollegeFootballData.com API (CFBD) via the `cfbd` Python package.

Environment variables:
  CFBD_API_KEY   – required for /live endpoints
  PORT           – port to listen on (default: 10000, Render default)
  DATA_JSON_PATH – path to pre-computed JSON file
                   (default: docs/data/cfb-stats.json)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import cfbd
import pandas as pd
from flask import Flask, jsonify, request
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

DATA_JSON_PATH = os.getenv(
    "DATA_JSON_PATH",
    str(Path(__file__).parent.parent / "docs" / "data" / "cfb-stats.json"),
)
CFBD_API_KEY = os.getenv("CFBD_API_KEY", "")
PORT = int(os.getenv("PORT", "10000"))

app = Flask(__name__)
CORS(app)  # allow cross-origin requests from GitHub Pages

# ---------------------------------------------------------------------------
# In-memory data store (loaded once at startup)
# ---------------------------------------------------------------------------

_store: dict[str, Any] = {
    "records": [],        # list of game dicts
    "df": None,           # pandas DataFrame (for structured filtering)
    "loaded_at": None,
    "total": 0,
}


def load_data(path: str = DATA_JSON_PATH) -> None:
    """Load pre-computed JSON into RAM. Called at startup."""
    p = Path(path)
    if not p.exists():
        log.warning("Data file not found: %s – API will serve empty results", path)
        return

    with p.open(encoding="utf-8") as fh:
        records = json.load(fh)

    _store["records"] = records
    _store["df"] = pd.DataFrame(records) if records else pd.DataFrame()
    _store["loaded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _store["total"] = len(records)
    log.info("Loaded %d CFB game records from %s", len(records), path)


load_data()

# ---------------------------------------------------------------------------
# CFBD API client helper
# ---------------------------------------------------------------------------

def _cfbd_client() -> cfbd.ApiClient:
    config = cfbd.Configuration()
    config.api_key["Authorization"] = CFBD_API_KEY
    config.api_key_prefix["Authorization"] = "Bearer"
    return cfbd.ApiClient(config)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "records_loaded": _store["total"],
        "loaded_at": _store["loaded_at"],
    })


@app.get("/search")
def search():
    """
    Fast in-memory text search across all pre-computed game records.

    Query params:
      q        – free-text query (searches home_team, away_team, and all string fields)
      team     – exact (case-insensitive) team name filter
      week     – integer week filter
      season   – integer season filter
      min_epa  – minimum home or away off_epa_play threshold
      limit    – max results to return (default 50, max 200)
    """
    t0 = time.perf_counter()

    records = _store["records"]
    if not records:
        return jsonify({"results": [], "count": 0, "query_ms": 0})

    q      = (request.args.get("q") or "").strip().lower()
    team   = (request.args.get("team") or "").strip().lower()
    week   = request.args.get("week", type=int)
    season = request.args.get("season", type=int)
    min_epa = request.args.get("min_epa", type=float)
    limit  = min(request.args.get("limit", default=50, type=int), 200)

    results = []
    for game in records:
        # --- structured filters ---
        if week is not None and game.get("week") != week:
            continue
        if season is not None and game.get("season") != season:
            continue
        if team:
            ht = (game.get("home_team") or "").lower()
            at = (game.get("away_team") or "").lower()
            if team not in ht and team not in at:
                continue
        if min_epa is not None:
            h_epa = game.get("home_off_epa_play")
            a_epa = game.get("away_off_epa_play")
            if (h_epa is None or h_epa < min_epa) and \
               (a_epa is None or a_epa < min_epa):
                continue

        # --- free-text filter ---
        if q:
            blob = " ".join(str(v) for v in game.values() if v is not None).lower()
            if q not in blob:
                continue

        results.append(game)
        if len(results) >= limit:
            break

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    return jsonify({
        "results": results,
        "count": len(results),
        "total_searched": _store["total"],
        "query_ms": elapsed_ms,
    })


@app.get("/games")
def games():
    """
    Structured game filter (uses pandas for richer filtering).

    Query params (all optional):
      team     – team name substring (case-insensitive)
      week     – week number
      season   – season year
      limit    – max rows (default 50)
      sort_by  – column to sort by
      order    – asc | desc (default desc)
    """
    df = _store.get("df")
    if df is None or df.empty:
        return jsonify({"results": [], "count": 0})

    team   = (request.args.get("team") or "").strip().lower()
    week   = request.args.get("week", type=int)
    season = request.args.get("season", type=int)
    limit  = min(request.args.get("limit", default=50, type=int), 200)
    sort_by = request.args.get("sort_by", "week")
    order   = request.args.get("order", "desc")

    mask = pd.Series([True] * len(df))

    if team:
        ht = df["home_team"].str.lower().str.contains(team, na=False)
        at = df["away_team"].str.lower().str.contains(team, na=False)
        mask = mask & (ht | at)

    if week is not None and "week" in df.columns:
        mask = mask & (df["week"] == week)

    if season is not None and "season" in df.columns:
        mask = mask & (df["season"] == season)

    result_df = df[mask]

    if sort_by in result_df.columns:
        ascending = order.lower() != "desc"
        result_df = result_df.sort_values(sort_by, ascending=ascending)

    result_df = result_df.head(limit)
    return jsonify({
        "results": result_df.where(result_df.notna(), None).to_dict(orient="records"),
        "count": len(result_df),
    })


@app.get("/live/games")
def live_games():
    """
    Live CFBD API query – proxies cfbd.GamesApi.get_games().

    Query params:
      year   – season year (required)
      week   – week number
      team   – team filter
      conference – conference abbreviation
    """
    if not CFBD_API_KEY:
        return jsonify({"error": "CFBD_API_KEY not configured"}), 503

    year = request.args.get("year", type=int)
    if not year:
        return jsonify({"error": "year parameter is required"}), 400

    kwargs: dict[str, Any] = {"year": year}
    if request.args.get("week"):
        kwargs["week"] = request.args.get("week", type=int)
    if request.args.get("team"):
        kwargs["team"] = request.args.get("team")
    if request.args.get("conference"):
        kwargs["conference"] = request.args.get("conference")

    try:
        with _cfbd_client() as client:
            api = cfbd.GamesApi(client)
            games_list = api.get_games(**kwargs)
        return jsonify([g.to_dict() for g in games_list])
    except Exception as exc:
        log.error("CFBD live games error: %s", exc)
        return jsonify({"error": str(exc)}), 502


@app.get("/live/stats")
def live_stats():
    """
    Live team game stats from CFBD API.

    Query params:
      year   – season year (required)
      week   – week number
      team   – team filter
    """
    if not CFBD_API_KEY:
        return jsonify({"error": "CFBD_API_KEY not configured"}), 503

    year = request.args.get("year", type=int)
    if not year:
        return jsonify({"error": "year parameter is required"}), 400

    kwargs: dict[str, Any] = {"year": year}
    if request.args.get("week"):
        kwargs["week"] = request.args.get("week", type=int)
    if request.args.get("team"):
        kwargs["team"] = request.args.get("team")

    try:
        with _cfbd_client() as client:
            api = cfbd.GamesApi(client)
            stats = api.get_team_game_stats(**kwargs)
        return jsonify([s.to_dict() for s in stats])
    except Exception as exc:
        log.error("CFBD live stats error: %s", exc)
        return jsonify({"error": str(exc)}), 502


@app.get("/live/epa")
def live_epa():
    """
    Live advanced game stats (EPA) from CFBD API.

    Query params:
      year      – season year (required)
      week      – week number
      team      – team filter
      opponent  – opponent filter
    """
    if not CFBD_API_KEY:
        return jsonify({"error": "CFBD_API_KEY not configured"}), 503

    year = request.args.get("year", type=int)
    if not year:
        return jsonify({"error": "year parameter is required"}), 400

    kwargs: dict[str, Any] = {"year": year}
    if request.args.get("week"):
        kwargs["week"] = request.args.get("week", type=int)
    if request.args.get("team"):
        kwargs["team"] = request.args.get("team")
    if request.args.get("opponent"):
        kwargs["opponent"] = request.args.get("opponent")

    try:
        with _cfbd_client() as client:
            api = cfbd.GamesApi(client)
            epa_data = api.get_advanced_game_stats(**kwargs)
        return jsonify([e.to_dict() for e in epa_data])
    except Exception as exc:
        log.error("CFBD live EPA error: %s", exc)
        return jsonify({"error": str(exc)}), 502


@app.get("/meta")
def meta():
    """Return metadata about the loaded dataset."""
    meta_path = Path(DATA_JSON_PATH).parent / "cfb-meta.json"
    if meta_path.exists():
        with meta_path.open(encoding="utf-8") as fh:
            return jsonify(json.load(fh))
    return jsonify({
        "records_loaded": _store["total"],
        "loaded_at": _store["loaded_at"],
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
