#!/usr/bin/env Rscript
# scripts/cfb_data_pipeline.R
#
# CFB Data Pipeline
# -----------------
# Downloads historical PBP/game data from Google Drive (large files not stored
# in the repo), fetches current-season data from the CFBD API, computes EPA
# and advanced efficiency metrics, then writes:
#   output/cfb_data_2026_week_<N>.csv   <- weekly snapshot committed to repo
#   docs/data/cfb-stats.json            <- consumed by GitHub Pages search UI
#                                          and the Render Flask API
#
# Expects the following files already present in data/:
#   data/pbp_2014_2025.RDS
#   data/games_2014_2025.RDS
#   data/CFB_GAMEDATA_2026_WEEK1.csv
#
# Environment variables:
#   CFBD_API_KEY   – CollegeFootballData.com API key

suppressPackageStartupMessages({
  library(tidyverse)
  library(cfbfastR)
  library(jsonlite)
  library(lubridate)
  library(zoo)
  library(data.table)
  library(janitor)
})

# ---- Configuration -----------------------------------------------------------

CURRENT_SEASON <- 2026
API_KEY        <- Sys.getenv("CFBD_API_KEY")

if (nchar(API_KEY) == 0) {
  stop("CFBD_API_KEY environment variable is not set.")
}

Sys.setenv(CFBD_API_KEY = API_KEY)

dir.create("output",    showWarnings = FALSE, recursive = TRUE)
dir.create("docs/data", showWarnings = FALSE, recursive = TRUE)

# ---- Helpers -----------------------------------------------------------------

safe_fetch <- function(expr, label) {
  tryCatch(expr, error = function(e) {
    message(sprintf("WARNING: failed to fetch %s: %s", label, conditionMessage(e)))
    NULL
  })
}

# Use base parsing only (avoids parse_date_time regex issues on CI)
parse_start_date <- function(x) {
  x <- as.character(x)
  x <- trimws(x)
  x[x == ""] <- NA_character_

  # primary: 4-digit year, e.g. 9/6/2015
  out <- as.Date(x, format = "%m/%d/%Y")

  # optional fallback if any ISO values appear
  need_fallback <- is.na(out) & !is.na(x)
  if (any(need_fallback)) {
    out[need_fallback] <- as.Date(x[need_fallback], format = "%Y-%m-%d")
  }

  out
}

rolling_epa <- function(df, n = 10) {
  df %>%
    arrange(season, week) %>%
    mutate(rolling_epa = rollapply(epa, width = n, FUN = mean,
                                   align = "right", fill = NA, partial = TRUE))
}

# ---- 1. Load historical data -------------------------------------------------

message("Loading historical data ...")

pbp_hist  <- NULL
game_hist <- NULL
gamedata_2026 <- NULL

if (file.exists("data/pbp_2014_2025.RDS")) {
  pbp_hist <- readRDS("data/pbp_2014_2025.RDS")
  message(sprintf("  Loaded PBP: %d rows", nrow(pbp_hist)))
} else {
  warning("data/pbp_2014_2025.RDS not found – historical EPA will be skipped.")
}

if (file.exists("data/games_2014_2025.RDS")) {
  game_hist <- readRDS("data/games_2014_2025.RDS")
  message(sprintf("  Loaded historical games: %d rows", nrow(game_hist)))
} else {
  warning("data/games_2014_2025.RDS not found – historical game context will be limited.")
}

if (file.exists("data/CFB_GAMEDATA_2026_WEEK1.csv")) {
  seed_raw <- read_csv("data/CFB_GAMEDATA_2026_WEEK1.csv", show_col_types = FALSE)
  message(sprintf("  Loaded seed data (raw): %d rows", nrow(seed_raw)))

  # Guard against mislabeled multi-season CSV
  gamedata_2026 <- seed_raw %>%
    mutate(start_date = parse_start_date(start_date)) %>%
    filter(season == CURRENT_SEASON)

  message(sprintf("  Seed rows after season filter (%d): %d",
                  CURRENT_SEASON, nrow(gamedata_2026)))
}

# ---- 2. Determine current week -----------------------------------------------

# cfbfastR helper: check what weeks have data so far this season
weeks_with_data <- safe_fetch(
  cfbd_calendar(year = CURRENT_SEASON),
  "cfbd_calendar"
)

current_week <- if (!is.null(weeks_with_data) && nrow(weeks_with_data) > 0) {
  # Determine current week by latest started date (more robust than max(week))
  cal <- weeks_with_data %>%
    mutate(start_date = parse_start_date(start_date)) %>%
    filter(!is.na(start_date), start_date <= Sys.Date())

  if (nrow(cal) > 0) {
    cal %>%
      arrange(desc(start_date), desc(week)) %>%
      slice(1) %>%
      pull(week) %>%
      as.integer()
  } else {
    1L
  }
} else {
  1L
}

message(sprintf("Current week determined: %d", current_week))

# ---- 3. Fetch current-season data from CFBD API ------------------------------

message("Fetching current-season data from CFBD API ...")

games_2026 <- safe_fetch(
  cfbd_game_info(year = CURRENT_SEASON, division = "fbs"),
  "cfbd_game_info"
)

# Play-by-play EPA for current season (week by week to avoid timeout)
pbp_2026 <- safe_fetch({
  weeks <- seq_len(current_week)
  map_dfr(weeks, function(w) {
    Sys.sleep(0.5)  # rate limit
    safe_fetch(
      cfbd_pbp_data(year = CURRENT_SEASON, week = w, epa_wpa = TRUE),
      sprintf("pbp week %d", w)
    )
  })
}, "pbp_2026")

# Team talent / SP+ ratings
talent <- safe_fetch(
  cfbd_team_talent(year = CURRENT_SEASON),
  "cfbd_team_talent"
)

# Coaches
coaches <- safe_fetch(
  cfbd_coaches(year = CURRENT_SEASON),
  "cfbd_coaches"
)

# Betting lines
lines <- safe_fetch(
  cfbd_betting_lines(year = CURRENT_SEASON),
  "cfbd_betting_lines"
)

# Weather
weather <- safe_fetch(
  cfbd_game_weather(year = CURRENT_SEASON),
  "cfbd_game_weather"
)

# ---- 4. Compute EPA / advanced stats -----------------------------------------

message("Computing EPA and advanced stats ...")

compute_team_epa <- function(pbp) {
  if (is.null(pbp) || nrow(pbp) == 0) return(NULL)

  required <- c("game_id", "pos_team", "season", "week", "epa",
                "down", "yards_to_goal", "pass", "rush")
  if (!all(required %in% names(pbp))) {
    message("  PBP missing expected columns; skipping EPA computation.")
    return(NULL)
  }

  pbp %>%
    filter(!is.na(epa), down %in% 1:4) %>%
    group_by(game_id, pos_team, season, week) %>%
    summarise(
      off_epa_play     = mean(epa, na.rm = TRUE),
      off_epa_pass     = mean(epa[pass == 1], na.rm = TRUE),
      off_epa_rush     = mean(epa[rush == 1], na.rm = TRUE),
      off_success_rate = mean(epa > 0, na.rm = TRUE),
      off_plays        = n(),
      .groups = "drop"
    ) %>%
    rename(team = pos_team)
}

# Current season EPA
epa_2026 <- compute_team_epa(pbp_2026)

# Historical team-game EPA from PBP (2014-2025) – used for rolling context
epa_hist <- compute_team_epa(pbp_hist)

# Rolling 10-game EPA from historical data (gives prior-season baseline)
rolling_hist_epa <- if (!is.null(epa_hist)) {
  epa_hist %>%
    group_by(team) %>%
    arrange(season, week) %>%
    mutate(
      rolling_off_epa = rollapply(off_epa_play, 10, mean, align = "right",
                                  fill = NA, partial = TRUE)
    ) %>%
    ungroup() %>%
    filter(season == max(season, na.rm = TRUE)) %>%
    group_by(team) %>%
    slice_max(week, n = 1) %>%
    ungroup() %>%
    select(team, prior_rolling_off_epa = rolling_off_epa)
} else {
  NULL
}

# ---- 5. Build master game-level dataset -------------------------------------

message("Building master game-level dataset ...")

# Start from 2026 games as the spine
if (!is.null(games_2026) && nrow(games_2026) > 0) {
  master <- games_2026 %>%
    filter(season == CURRENT_SEASON) %>%
    mutate(start_date = parse_start_date(start_date)) %>%
    select(
      game_id, season, week, season_type,
      home_team, away_team, home_conference, away_conference,
      home_points, away_points,
      home_line_scores, away_line_scores,
      start_date, neutral_site, conference_game,
      venue_id, venue
    )
} else if (!is.null(gamedata_2026) && nrow(gamedata_2026) > 0) {
  master <- gamedata_2026
  message("  Falling back to filtered CFB_GAMEDATA_2026_WEEK1.csv as game spine.")
} else {
  stop("No game data available – cannot build master dataset.")
}

# Attach home-team EPA
if (!is.null(epa_2026)) {
  master <- master %>%
    left_join(
      epa_2026 %>% rename_with(~ paste0("home_", .), -c(game_id, season, week)),
      by = c("game_id", "home_team" = "team", "season", "week")
    ) %>%
    left_join(
      epa_2026 %>% rename_with(~ paste0("away_", .), -c(game_id, season, week)),
      by = c("game_id", "away_team" = "team", "season", "week")
    )
}

# Attach talent
if (!is.null(talent)) {
  talent_clean <- talent %>%
    select(school, talent) %>%
    rename(team = school)

  master <- master %>%
    left_join(talent_clean %>% rename(home_talent = talent),
              by = c("home_team" = "team")) %>%
    left_join(talent_clean %>% rename(away_talent = talent),
              by = c("away_team" = "team"))
}

# Attach betting lines (use first line available for each game)
if (!is.null(lines) && nrow(lines) > 0) {
  lines_clean <- lines %>%
    group_by(game_id) %>%
    slice(1) %>%
    ungroup() %>%
    select(game_id, spread, over_under, formatted_spread)

  master <- master %>%
    left_join(lines_clean, by = "game_id")
}

# Attach weather
if (!is.null(weather) && nrow(weather) > 0) {
  weather_clean <- weather %>%
    select(game_id, temperature, wind_speed, wind_direction,
           weather_condition = weather_description)

  master <- master %>%
    left_join(weather_clean, by = "game_id")
}

# Attach prior-season rolling EPA for context
if (!is.null(rolling_hist_epa)) {
  master <- master %>%
    left_join(rolling_hist_epa %>% rename(home_prior_rolling_epa = prior_rolling_off_epa),
              by = c("home_team" = "team")) %>%
    left_join(rolling_hist_epa %>% rename(away_prior_rolling_epa = prior_rolling_off_epa),
              by = c("away_team" = "team"))
}

# Add derived columns
master <- master %>%
  mutate(
    result       = case_when(
      is.na(home_points) | is.na(away_points) ~ "scheduled",
      home_points > away_points               ~ "home_win",
      away_points > home_points               ~ "away_win",
      TRUE                                    ~ "tie"
    ),
    point_diff   = home_points - away_points,
    total_points = home_points + away_points
  )

# Incorporate gamedata_2026 columns not already present
if (!is.null(gamedata_2026) && nrow(gamedata_2026) > 0) {
  extra_cols <- setdiff(names(gamedata_2026), names(master))
  if (length(extra_cols) > 0 && "game_id" %in% names(gamedata_2026)) {
    master <- master %>%
      left_join(gamedata_2026 %>% select(game_id, all_of(extra_cols)),
                by = "game_id")
  }
}

master <- master %>% clean_names()

# ---- 6. Export outputs -------------------------------------------------------

message("Exporting outputs ...")

# CSV: weekly snapshot
csv_file <- sprintf("output/cfb_data_2026_week_%02d.csv", current_week)
write_csv(master, csv_file)
message(sprintf("  Wrote %s (%d rows, %d cols)", csv_file,
                nrow(master), ncol(master)))

# Also write a "latest" pointer
write_csv(master, "output/cfb_data_2026_latest.csv")

# JSON: for GitHub Pages search UI and Flask API
json_cols <- intersect(
  c(
    "game_id", "season", "week", "season_type",
    "home_team", "away_team", "home_conference", "away_conference",
    "home_points", "away_points", "result", "point_diff", "total_points",
    "start_date", "neutral_site", "conference_game", "venue",
    "home_off_epa_play", "away_off_epa_play",
    "home_off_epa_pass", "away_off_epa_pass",
    "home_off_epa_rush", "away_off_epa_rush",
    "home_off_success_rate", "away_off_success_rate",
    "home_off_plays", "away_off_plays",
    "home_talent", "away_talent",
    "home_prior_rolling_epa", "away_prior_rolling_epa",
    "spread", "over_under", "formatted_spread",
    "temperature", "wind_speed", "weather_condition"
  ),
  names(master)
)

json_data <- master %>% select(all_of(json_cols))
write_json(json_data, "docs/data/cfb-stats.json", pretty = FALSE, na = "null")
message(sprintf("  Wrote docs/data/cfb-stats.json (%d records)", nrow(json_data)))

# Metadata file for the API / UI
meta <- list(
  generated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
  season       = CURRENT_SEASON,
  current_week = current_week,
  total_games  = nrow(master),
  columns      = names(json_data)
)
write_json(meta, "docs/data/cfb-meta.json", pretty = TRUE, auto_unbox = TRUE)
message("  Wrote docs/data/cfb-meta.json")

message("CFB data pipeline complete.")
