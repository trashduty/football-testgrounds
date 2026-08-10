# Football Testgrounds — Repository Map

> Comprehensive map of workflows, scripts, configuration files, and their relationships.
> Generated 2026-08-10.

---

## 1. GitHub Actions Workflows (`.github/workflows/`)

There are **11 workflow files**:

| Workflow File | Name | Trigger |
|---|---|---|
| `QB.yml` | Run 2025 QB starter inference | `workflow_dispatch` |
| `build-historical-database.yml` | Build Historical CFB Database | `workflow_dispatch` |
| `cfb-data-pipeline.yml` | CFB Data Pipeline | `workflow_dispatch` |
| `daily_digests.yml` | Daily Sports Digests | `workflow_dispatch` |
| `generate-cfb-articles.yml` | Generate CFB Matchup Articles | `workflow_dispatch` |
| `qb-comparison-visuals.yml` | Generate QB comparison visuals | `workflow_dispatch` + schedule (Tuesdays 1 PM UTC) |
| `tiktok-seo-weekly.yml` | Weekly TikTok SEO Report | `workflow_dispatch` + schedule (Mondays 3 PM UTC / 8 AM AZ) |
| `update-current-season.yml` | Update Current CFB Season | `workflow_dispatch` (schedule commented out) |
| `weekly-matchup-articles.yml` | Generate NFL matchup articles | `workflow_dispatch` + schedule (Fridays 3 PM UTC) |
| `x-growth-radar.yml` | BTB X Growth Radar | `workflow_dispatch` + schedule (every 10 min 6 AM–10 PM AZ; hourly overnight) |

---

## 2. Scripts and Their Purposes

### Python Scripts (`scripts/`)

#### `scripts/qb_starters_2025.py`
- **Purpose:** Infers NFL QB starters from play-by-play data for 2025.
- Uses `nfl_data_py` to load PBP data. Identifies starters by finding the player with the earliest QB play in each game. Computes play share.
- **Outputs:** `outputs/qb_starter_inference_2025_game_level.csv`, `outputs/qb_starter_inference_2025_team_summary.csv` (lines 7–8)

#### `scripts/post_digest.py`
- **Purpose:** Fetches sports news from ESPN and RapidAPI, uses OpenAI to write a digest summary, and posts it to a Discord webhook.
- Reads env vars: `LEAGUE` (NFL or CFB), `DISCORD_WEBHOOK_NFL`/`DISCORD_WEBHOOK_CFB`, `OPENAI_API_KEY`, `RAPIDAPI_KEY`, `RAPIDAPI_HOST` (lines 7–14)
- **No file inputs or outputs** — purely API-in, Discord-out.

#### `scripts/weekly_matchup_articles.py`
- **Purpose:** Generates NFL weekly matchup articles in Markdown and JSON from odds/model data, nflverse PBP stats, ESPN injury reports.
- **Inputs:**
  - `trash-schedule` repo: `NFL_Odds/Data/spreads_odds.csv`, `Week {week} model pred_updated.csv` (lines 27–30)
  - Remote: nflverse games CSV (`nflverse/nfldata`), PBP parquet, weekly player stats parquet, team colors CSV
  - Local: `inst/extdata/combined_data_dictionary.csv` (line 40), `data-raw/QB Crosswalk.csv` (line 44)
- **Outputs:** `outputs/matchup_articles/week_{n}/*.md`, `weekly_matchup_articles.json`, `weekly_matchup_articles.md`

#### `scripts/weekly_matchup_articles_cfb.py`
- **Purpose:** Generates CFB weekly matchup articles from CFBD odds + stats.
- Imports and depends on `scripts/cfb_stats.py` (local module import, line 30 `import cfb_stats`).
- **Inputs:**
  - `trash-schedule` repo: `CFB_Odds/Data/spreads_odds.csv`, `CFB_Odds/Data/CFB Teams Full Crosswalk.csv` (lines 35–36)
  - CFBD REST API (via `cfb_stats.py`): EPA/PPA, games, drives, venues
- **Outputs:** `outputs/cfb_matchup_articles/week_{n}/*.md`, `weekly_matchup_articles.json`, `weekly_matchup_articles.md`

#### `scripts/cfb_stats.py`
- **Purpose:** Library module — pulls rolling-10-game CFB team stats (EPA/PPA, eckel rate) from the CFBD API; provides helpers to rank teams FBS-wide. Also reads local CSVs as fallback.
- Called by `weekly_matchup_articles_cfb.py` as a local import.

#### `scripts/api.py`
- **Purpose:** Flask/Gunicorn web server for serving CFB stats queries.
- **Inputs:** `docs/data/cfb-stats.json` (pre-built by R pipeline) loaded into RAM at startup. Also proxies live queries to CFBD API.
- **Deployed via:** Render (`render.yaml`, `PYTHONPATH=scripts gunicorn api:app`)

#### `scripts/qb_comparison_visuals.py`
- **Purpose:** Generates data and visual comparison charts for Brissett vs Minshew (or configurable QBs) using nfl_data_py PBP data.
- **Outputs:** `outputs/qb_comparison/qb_comparison_metrics.csv`, `qb_headshots_panel.png`, `qb_metric_bars.png`, `qb_metrics_table.png`, headshot PNGs in `outputs/qb_comparison/headshots/`

#### `scripts/Brissett_vs_Minshew.py`
- **Purpose:** Earlier/standalone version of the QB comparison script, hardcoded for Brissett and Minshew. Likely a prototype of `qb_comparison_visuals.py`.
- **Inputs/Outputs:** Same pattern as `qb_comparison_visuals.py` using nfl_data_py.

#### `scripts/update_current_season.py`
- **Purpose:** Pulls current-season (2026) CFB data from the CFBD REST API and merges it with the historical Parquet database.
- **Inputs (required):**
  - `data/processed/historical_team_game_stats.parquet`
  - `data/processed/historical_team_season_stats.parquet`
  - `data/processed/historical_league_season_stats.parquet`
  - `data/processed/historical_team_rankings.parquet`
  - `CFBD_API_KEY` env var (lines 22–23)
- **Outputs:**
  - `data/processed/combined_team_game_stats.parquet` + `.csv.gz`
  - `data/processed/combined_team_season_stats.parquet` + `.csv.gz`
  - `data/processed/combined_league_season_stats.parquet` + `.csv.gz`
  - `data/processed/combined_team_rankings.parquet` + `.csv.gz`
  - `data/processed/current_update_metadata.json`
  - `data/current/` (per-week files)

#### `scripts/build_team_logo_assets.py`
- **Purpose:** Downloads and standardizes CFB team logos as PNGs.
- **Inputs:** `CFB Teams Full Crosswalk.csv` (root level)
- **Outputs:** `assets/team_logos/<team-slug>.png`, `data/processed/team_logo_map.csv`, `data/processed/team_logo_download_report.csv`

#### `scripts/tiktok_seo/weekly_tiktok_seo.py`
- **Purpose:** Queries KeywordTool.io API for TikTok search volume on football-related seeds, generates a ranked keyword report.
- **Inputs:** `config/tiktok_seeds.yml`
- **Outputs:**
  - `data/tiktok/latest_keywords.csv`
  - `data/tiktok/keyword_history.csv`
  - `data/tiktok/latest_report.md`
  - `data/tiktok/weekly_reports/YYYY-MM-DD.md`
  - `data/tiktok/weekly_reports/YYYY-MM-DD.csv`

#### `scripts/tiktok_seo/email_report.py`
- **Purpose:** Emails the TikTok SEO report via Gmail SMTP.
- **Inputs:** `data/tiktok/latest_report.md`, `data/tiktok/latest_keywords.csv`
- **Outputs:** Email to `bullytheboard@gmail.com`

---

### R Scripts (`scripts/`)

#### `scripts/build_historical_stats.R`
- **Purpose:** Reads historical CFB play-by-play and games RDS files, computes team-game, team-season, league-season stats and rankings, writes Parquet + compressed CSV outputs.
- **Inputs (env vars):**
  - `PBP_RDS_PATH` → `data/downloads/pbp_2014_2025.RDS`
  - `GAMES_RDS_PATH` → `data/downloads/games_2014_2025.RDS`
  - `OUTPUT_DIR` → `data/processed`
  - `MIN_SEASON` (2014), `MAX_SEASON` (2025)
- **Outputs:**
  - `data/processed/historical_team_game_stats.parquet` + `.csv.gz`
  - `data/processed/historical_team_season_stats.parquet` + `.csv.gz`
  - `data/processed/historical_league_season_stats.parquet` + `.csv.gz`
  - `data/processed/historical_team_rankings.parquet` + `.csv.gz`
  - `data/processed/historical_data_metadata.json`

#### `scripts/build_historical_situational_stats.R`
- **Purpose:** Reads only the PBP RDS and extracts a compact situational stats dataset (down, distance, field position, score state, etc.) with additive totals for DuckDB queries.
- **Inputs:** `data/downloads/pbp_2014_2025.RDS`
- **Outputs:**
  - `data/processed/historical_situational_stats.parquet`
  - `data/processed/historical_situational_metadata.json`

#### `scripts/cfb_data_pipeline.R`
- **Purpose:** Older pipeline using `cfbfastR` and Google Drive RDS downloads to build a 2015+ query dataset. Outputs go to `output/` and `docs/data/`.
- **Inputs:**
  - `data/pbp_2014_2025.RDS`, `data/games_2014_2025.RDS` (downloaded from Google Drive via `GDRIVE_PBP_ID`, `GDRIVE_GAMES_ID` secrets)
  - `data/CFB_GAMEDATA_2026_WEEK1.csv` (from `GDRIVE_GAMEDATA_ID` secret)
  - `CFBD_API_KEY` env var
- **Outputs:**
  - `output/cfb_data_2026_latest.csv`
  - `output/cfb_data_2026_week_00.csv` (week-stamped CSVs)
  - `docs/data/cfb-stats.json`
  - `docs/data/cfb-meta.json`

#### `data-raw/build_combined_data_dictionary.R`
- **Purpose:** Builds the combined data dictionary CSV.
- **Outputs:** `inst/extdata/combined_data_dictionary.csv`

---

### `x_growth/` Package (Python)

A modular Python package run as `python -m x_growth.main`:

| Module | Purpose |
|---|---|
| `main.py` | Orchestrator: loads config, runs search buckets at their configured cadence, scores posts, dispatches Discord alerts |
| `search_x.py` | Wraps X (Twitter) API v2 recent search endpoint (`X_BEARER_TOKEN`) |
| `query_builder.py` | Builds search queries from `config/search_queries.yml` |
| `team_matcher.py` | Identifies CFB teams mentioned in posts; loads `config/cfb_team_crosswalk.csv` |
| `classify_conversation.py` | Classifies post conversation types (e.g., TEAM_HYPE, INJURY, RANKINGS) |
| `score_opportunities.py` | Scores posts using freshness, reach, velocity, relevance, team priority |
| `generate_alert.py` | Builds Discord embed payloads |
| `send_discord.py` | POSTs to `DISCORD_WEBHOOK_URL` |
| `state.py` | Persists state (alerted IDs, query run timestamps) to `x_growth/state/` — cached between runs via `actions/cache` |

**Config inputs:** `config/search_queries.yml`, `config/settings.yml`, `config/priority_accounts.yml`, `config/team_priorities.yml`, `config/cfb_team_crosswalk.csv`

---

### `app/` Package (Python FastAPI)

Not triggered by any workflow — deployed via Dockerfile/Render:

| Module | Purpose |
|---|---|
| `main.py` | FastAPI app entry point |
| `database.py` | DuckDB connection over the processed Parquet files |
| `chart_database.py` | DuckDB queries for chart data |
| `charts.py` | Plotly/Kaleido chart generation |
| `query_parser.py` | Parses natural-language queries |
| `static/` + `templates/` | Frontend (HTML/CSS/JS) |

---

## 3. Configuration and Data Files

| File | Purpose |
|---|---|
| `config/tiktok_seeds.yml` | Seed keywords, scoring params, negative terms for TikTok SEO workflow |
| `config/settings.yml` | X Growth Radar: API limits, alert thresholds, scoring component maxes |
| `config/search_queries.yml` | X Growth Radar: named query buckets with enabled flag, sport, cadence, lookback, and raw query strings |
| `config/priority_accounts.yml` | X Growth Radar: high-priority X accounts |
| `config/team_priorities.yml` | X Growth Radar: per-team priority weights |
| `config/cfb_team_crosswalk.csv` | Maps CFB team names across systems (used by x_growth and cfb_stats) |
| `CFB Teams Full Crosswalk.csv` | Root-level crosswalk used by `build_team_logo_assets.py` and `weekly_matchup_articles_cfb.py` |
| `data-raw/QB Crosswalk.csv` | NFL QB ID/name crosswalk used by `weekly_matchup_articles.py` (line 44) |
| `inst/extdata/combined_data_dictionary.csv` | Data dictionary consumed by `weekly_matchup_articles.py` (line 40) |
| `requirements.txt` | Python dependencies (shared by all workflows) |
| `.python-version` | `3.12` (used by pyenv/actions) |
| `Dockerfile` | Builds the FastAPI app with Chromium for headless chart generation |
| `render.yaml` | Render.com deploy config for the Flask/Gunicorn `api.py` service |
| `_config.yml` | Jekyll config for GitHub Pages publishing of matchup articles |

---

## 4. Workflow → Script → File Dependency Map

```
QB.yml
  └─ scripts/qb_starters_2025.py
        IN:  nfl_data_py (remote download, 2025 PBP)
        OUT: outputs/qb_starter_inference_2025_game_level.csv
             outputs/qb_starter_inference_2025_team_summary.csv

build-historical-database.yml
  ├─ [gdown] → data/downloads/pbp_2014_2025.RDS    (GDRIVE_PBP_ID secret)
  ├─ [gdown] → data/downloads/games_2014_2025.RDS  (GDRIVE_GAMES_ID secret)
  ├─ scripts/build_historical_stats.R
  │     IN:  data/downloads/pbp_2014_2025.RDS
  │          data/downloads/games_2014_2025.RDS
  │     OUT: data/processed/historical_team_game_stats.{parquet,csv.gz}
  │          data/processed/historical_team_season_stats.{parquet,csv.gz}
  │          data/processed/historical_league_season_stats.{parquet,csv.gz}
  │          data/processed/historical_team_rankings.{parquet,csv.gz}
  │          data/processed/historical_data_metadata.json
  └─ scripts/build_historical_situational_stats.R
        IN:  data/downloads/pbp_2014_2025.RDS
        OUT: data/processed/historical_situational_stats.parquet
             data/processed/historical_situational_metadata.json

cfb-data-pipeline.yml  [OLDER / parallel pipeline]
  ├─ [gdown] → data/pbp_2014_2025.RDS, data/games_2014_2025.RDS,
  │             data/CFB_GAMEDATA_2026_WEEK1.csv
  └─ scripts/cfb_data_pipeline.R
        IN:  data/pbp_2014_2025.RDS, data/games_2014_2025.RDS
             data/CFB_GAMEDATA_2026_WEEK1.csv
             CFBD_API_KEY (env)
        OUT: output/cfb_data_2026_latest.csv
             output/cfb_data_2026_week_00.csv
             docs/data/cfb-stats.json
             docs/data/cfb-meta.json

daily_digests.yml (two parallel jobs: NFL + CFB)
  └─ scripts/post_digest.py
        IN:  ESPN API (remote), RapidAPI (remote), OpenAI API (remote)
        OUT: Discord webhook POST (no local files)

generate-cfb-articles.yml
  └─ scripts/weekly_matchup_articles_cfb.py
        IN:  trash-schedule repo: CFB_Odds/Data/spreads_odds.csv
                                  CFB_Odds/Data/CFB Teams Full Crosswalk.csv
             CFBD API (via cfb_stats.py)
             CFBD_API_KEY (env)
        OUT: outputs/cfb_matchup_articles/week_{n}/<matchup>.md
             outputs/cfb_matchup_articles/week_{n}/weekly_matchup_articles.json
             outputs/cfb_matchup_articles/week_{n}/weekly_matchup_articles.md

qb-comparison-visuals.yml (schedule: Tues 1PM UTC)
  └─ scripts/qb_comparison_visuals.py
        IN:  nfl_data_py (remote PBP download)
             ESPN headshot URLs (remote)
        OUT: outputs/qb_comparison/qb_comparison_metrics.csv
             outputs/qb_comparison/qb_headshots_panel.png
             outputs/qb_comparison/qb_metric_bars.png
             outputs/qb_comparison/qb_metrics_table.png
             outputs/qb_comparison/headshots/*.png

tiktok-seo-weekly.yml (schedule: Mon 3PM UTC)
  ├─ scripts/tiktok_seo/weekly_tiktok_seo.py
  │     IN:  config/tiktok_seeds.yml
  │          KeywordTool.io API (KEYWORDTOOL_API_KEY)
  │     OUT: data/tiktok/latest_keywords.csv
  │          data/tiktok/keyword_history.csv
  │          data/tiktok/latest_report.md
  │          data/tiktok/weekly_reports/YYYY-MM-DD.{md,csv}
  └─ scripts/tiktok_seo/email_report.py
        IN:  data/tiktok/latest_report.md  (written by previous step)
             data/tiktok/latest_keywords.csv
             GMAIL_USERNAME, GMAIL_APP_PASSWORD (env)
        OUT: Email to bullytheboard@gmail.com

update-current-season.yml
  └─ scripts/update_current_season.py
        IN:  data/processed/historical_*.parquet (4 files, from build-historical-database.yml)
             CFBD_API_KEY (env)
        OUT: data/processed/combined_*.{parquet,csv.gz} (4 files)
             data/processed/current_update_metadata.json
             data/current/ (per-week Parquet files)

weekly-matchup-articles.yml (schedule: Fri 3PM UTC)
  └─ scripts/weekly_matchup_articles.py
        IN:  trash-schedule repo: NFL_Odds/Data/spreads_odds.csv
                                  Week {n} model pred_updated.csv
             nflverse (remote): games.csv, PBP parquet, player_stats parquet, teams CSV
             inst/extdata/combined_data_dictionary.csv
             data-raw/QB Crosswalk.csv
             GITHUB_TOKEN (env, for trash-schedule access)
        OUT: outputs/matchup_articles/week_{n}/<team>_at_<team>.md
             outputs/matchup_articles/week_{n}/weekly_matchup_articles.json
             outputs/matchup_articles/week_{n}/weekly_matchup_articles.md

x-growth-radar.yml (schedule: every 10 min daytime AZ, hourly overnight)
  └─ python -m x_growth.main
        IN:  config/search_queries.yml
             config/settings.yml
             config/priority_accounts.yml
             config/team_priorities.yml
             config/cfb_team_crosswalk.csv
             X API v2 (X_BEARER_TOKEN)
             x_growth/state/ (via actions/cache, persisted between runs)
        OUT: Discord webhook POSTs (DISCORD_WEBHOOK_URL)
             x_growth/state/alerted_ids.json
             x_growth/state/search_runs.json
             x_growth/state/opportunities.csv
             (state is cached, NOT committed to repo)
```

---

## 5. Key Relationships and Notable Architecture Decisions

**Pipeline dependency chain:** `build-historical-database.yml` → `update-current-season.yml`. The update workflow explicitly verifies the 4 historical Parquet files exist before running. The combined outputs then power the FastAPI app (`app/database.py` using DuckDB over `data/processed/combined_*.parquet`).

**Two parallel CFB data pipelines:** `cfb-data-pipeline.yml` / `cfb_data_pipeline.R` is an older pipeline using `cfbfastR` + R, outputting to `output/` and `docs/data/` for the Jekyll/GitHub Pages site. The newer pipeline (`build-historical-database.yml` + `update-current-season.yml`) uses Python + DuckDB and feeds the FastAPI app and article generators.

**Cross-repo dependency:** Both matchup article workflows (`weekly-matchup-articles.yml` and `generate-cfb-articles.yml`) checkout the sibling `trashduty/trash-schedule` repo to read odds/model CSVs — treating it as a private data store.

**State persistence without commits:** The X Growth Radar uniquely avoids repo bloat by using `actions/cache` (not git commits) to persist its state between the ~100+ daily runs.

**`cfb_stats.py` as a local library:** `weekly_matchup_articles_cfb.py` imports `cfb_stats` as a sibling module (`import cfb_stats`), which is why the `generate-cfb-articles.yml` workflow runs with `working-directory: football-testgrounds` — so the local import path resolves correctly.

**Deployment:** The `app/` package is deployed as a live web service on Render via `render.yaml`, using the older `docs/data/cfb-stats.json` from the R pipeline as its pre-computed dataset, alongside live CFBD API proxying.
