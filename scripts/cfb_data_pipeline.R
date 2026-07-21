#!/usr/bin/env Rscript
# scripts/cfb_data_pipeline.R
#
# CFB Data Pipeline
# -----------------
# Builds a 2015+ query dataset with historical baseline and in-season updates.
# Preseason-safe: if no played games in CURRENT_SEASON yet, it falls back to
# historical games (2015-2025) as the master spine.

suppressPackageStartupMessages({
  library(tidyverse)
  library(cfbfastR)
  library(jsonlite)
  library(zoo)
  library(data.table)
  library(janitor)
})

# ---- Configuration -----------------------------------------------------------

CURRENT_SEASON <- 2026
API_KEY        <- Sys.getenv("CFBD_API_KEY")

if (nchar(API_KEY) == 0) stop("CFBD_API_KEY environment variable is not set.")
Sys.setenv(CFBD_API_KEY = API_KEY)

dir.create("output", showWarnings = FALSE, recursive = TRUE)
dir.create("docs/data", showWarnings = FALSE, recursive = TRUE)

# ---- Helpers -----------------------------------------------------------------

safe_fetch <- function(expr, label) {
  tryCatch(
    expr,
    error = function(e) {
      message(sprintf("WARNING: failed to fetch %s: %s", label, conditionMessage(e)))
      NULL
    }
  )
}

normalize_frame <- function(x) {
  if (is.null(x) || !is.data.frame(x)) return(x)
  janitor::clean_names(x)
}

has_rows <- function(x) {
  !is.null(x) && is.data.frame(x) && nrow(x) > 0
}

has_columns <- function(x, required) {
  has_rows(x) && all(required %in% names(x))
}

first_existing_col <- function(x, candidates) {
  if (is.null(x) || !is.data.frame(x)) return(NA_character_)
  found <- intersect(candidates, names(x))
  if (length(found) == 0) NA_character_ else found[[1]]
}

report_schema <- function(x, label) {
  if (is.null(x)) {
    message(sprintf("  %s: NULL", label))
  } else if (!is.data.frame(x)) {
    message(sprintf("  %s: class=%s", label, paste(class(x), collapse = "/")))
  } else {
    message(sprintf(
      "  %s: %d rows; columns: %s",
      label,
      nrow(x),
      if (ncol(x) == 0) "<none>" else paste(names(x), collapse = ", ")
    ))
  }
  invisible(x)
}

parse_start_date <- function(x) {
  x <- as.character(x)
  x <- trimws(x)
  x[x == ""] <- NA_character_

  # Primary format in historical files
  out <- as.Date(x, format = "%m/%d/%Y")

  # Fallback for ISO timestamps/dates from API
  idx <- is.na(out) & !is.na(x)
  if (any(idx)) {
    out[idx] <- as.Date(substr(x[idx], 1, 10), format = "%Y-%m-%d")
  }

  out
}

compute_team_epa <- function(pbp) {
  if (!has_rows(pbp)) return(NULL)

  pbp <- normalize_frame(pbp)

  team_col <- first_existing_col(
    pbp,
    c("pos_team", "offense_play", "offense", "possession_team")
  )
  game_col <- first_existing_col(pbp, c("game_id", "id_game"))
  epa_col  <- first_existing_col(pbp, c("epa"))
  pass_col <- first_existing_col(pbp, c("pass", "pass_attempt"))
  rush_col <- first_existing_col(pbp, c("rush", "rush_attempt"))

  required_context <- c("season", "week", "down")

  if (
    is.na(team_col) ||
    is.na(game_col) ||
    is.na(epa_col) ||
    !all(required_context %in% names(pbp))
  ) {
    message("  PBP missing required game/team/EPA/context columns; skipping EPA computation.")
    message("  Available PBP columns: ", paste(names(pbp), collapse = ", "))
    return(NULL)
  }

  pbp_normalized <- pbp %>%
    mutate(
      game_id = .data[[game_col]],
      team = as.character(.data[[team_col]]),
      epa_value = suppressWarnings(as.numeric(.data[[epa_col]])),
      pass_flag = if (!is.na(pass_col)) suppressWarnings(as.integer(.data[[pass_col]])) else NA_integer_,
      rush_flag = if (!is.na(rush_col)) suppressWarnings(as.integer(.data[[rush_col]])) else NA_integer_
    )

  pbp_normalized %>%
    filter(
      !is.na(epa_value),
      !is.na(team),
      team != "",
      down %in% 1:4
    ) %>%
    group_by(game_id, team, season, week) %>%
    summarise(
      off_epa_play = mean(epa_value, na.rm = TRUE),
      off_epa_pass = if (any(pass_flag == 1, na.rm = TRUE)) {
        mean(epa_value[pass_flag == 1], na.rm = TRUE)
      } else {
        NA_real_
      },
      off_epa_rush = if (any(rush_flag == 1, na.rm = TRUE)) {
        mean(epa_value[rush_flag == 1], na.rm = TRUE)
      } else {
        NA_real_
      },
      off_success_rate = mean(epa_value > 0, na.rm = TRUE),
      off_plays = n(),
      .groups = "drop"
    )
}

# ---- 1. Load historical data -------------------------------------------------

message("Loading historical data ...")

pbp_hist      <- NULL
game_hist     <- NULL
gamedata_2026 <- NULL

if (file.exists("data/pbp_2014_2025.RDS")) {
  pbp_hist <- readRDS("data/pbp_2014_2025.RDS") %>% normalize_frame()
  message(sprintf("  Loaded PBP: %d rows", nrow(pbp_hist)))
} else {
  warning("data/pbp_2014_2025.RDS not found – historical EPA will be skipped.")
}

if (file.exists("data/games_2014_2025.RDS")) {
  game_hist <- readRDS("data/games_2014_2025.RDS") %>% normalize_frame()
  message(sprintf("  Loaded historical games: %d rows", nrow(game_hist)))
} else {
  warning("data/games_2014_2025.RDS not found – historical game context will be limited.")
}

if (file.exists("data/CFB_GAMEDATA_2026_WEEK1.csv")) {
  seed_raw <- read_csv("data/CFB_GAMEDATA_2026_WEEK1.csv", show_col_types = FALSE) %>%
    normalize_frame()

  message(sprintf("  Loaded seed data (raw): %d rows", nrow(seed_raw)))

  if (all(c("start_date", "season") %in% names(seed_raw))) {
    gamedata_2026 <- seed_raw %>%
      mutate(start_date = parse_start_date(start_date)) %>%
      filter(season == CURRENT_SEASON)

    message(sprintf(
      "  Seed rows after season filter (%d): %d",
      CURRENT_SEASON,
      nrow(gamedata_2026)
    ))
  } else {
    message("  Seed file is missing start_date and/or season; skipping seed data.")
  }
}

report_schema(pbp_hist, "pbp_hist")
report_schema(game_hist, "game_hist")

# ---- 2. Determine current week -----------------------------------------------

message("Determining current week ...")

weeks_with_data <- safe_fetch(
  cfbd_calendar(year = CURRENT_SEASON),
  "cfbd_calendar"
) %>% normalize_frame()

current_week <- 0L

if (has_columns(weeks_with_data, "week")) {
  calendar_date_col <- first_existing_col(
    weeks_with_data,
    c("first_game_start", "start_date", "first_day", "game_date", "start")
  )

  if (!is.na(calendar_date_col)) {
    calendar_dates <- parse_start_date(weeks_with_data[[calendar_date_col]])
    week_values <- suppressWarnings(as.integer(weeks_with_data$week))

    started <- !is.na(calendar_dates) & !is.na(week_values) & calendar_dates <= Sys.Date()

    if (any(started)) {
      current_week <- max(week_values[started], na.rm = TRUE)
    }
  } else {
    message("  Calendar response has no recognized start-date column; keeping week at 0.")
  }
}

if (!is.finite(current_week) || is.na(current_week)) current_week <- 0L
current_week <- as.integer(max(current_week, 0L))
message(sprintf("Current week determined: %d", current_week))

# ---- 3. Fetch current-season data from CFBD API ------------------------------

message("Fetching current-season data from CFBD API ...")

games_2026 <- safe_fetch(
  cfbd_game_info(year = CURRENT_SEASON, division = "fbs"),
  "cfbd_game_info"
) %>% normalize_frame()

pbp_2026 <- NULL
if (current_week > 0) {
  pbp_2026 <- safe_fetch({
    map_dfr(seq_len(current_week), function(w) {
      Sys.sleep(0.5)
      out <- safe_fetch(
        cfbd_pbp_data(year = CURRENT_SEASON, week = w, epa_wpa = TRUE),
        sprintf("pbp week %d", w)
      )
      if (is.null(out)) tibble() else normalize_frame(out)
    })
  }, "pbp_2026") %>% normalize_frame()
} else {
  message("No played weeks detected for current season yet; skipping current-season PBP pull.")
}

# Talent may not yet be published for the current season. Use previous season as
# a preseason fallback, while retaining the year used for transparency.
talent_year <- CURRENT_SEASON

talent <- safe_fetch(
  cfbd_team_talent(year = talent_year),
  sprintf("cfbd_team_talent %d", talent_year)
) %>% normalize_frame()

if (!has_rows(talent)) {
  talent_year <- CURRENT_SEASON - 1L
  message(sprintf(
    "  No usable %d talent data; trying %d as a preseason fallback.",
    CURRENT_SEASON,
    talent_year
  ))

  talent <- safe_fetch(
    cfbd_team_talent(year = talent_year),
    sprintf("cfbd_team_talent %d", talent_year)
  ) %>% normalize_frame()
}

lines <- safe_fetch(
  cfbd_betting_lines(year = CURRENT_SEASON),
  "cfbd_betting_lines"
) %>% normalize_frame()

weather <- safe_fetch(
  cfbd_game_weather(year = CURRENT_SEASON),
  "cfbd_game_weather"
) %>% normalize_frame()

report_schema(weeks_with_data, "calendar")
report_schema(games_2026, "games_2026")
report_schema(pbp_2026, "pbp_2026")
report_schema(talent, sprintf("talent_%d", talent_year))
report_schema(lines, "lines")
report_schema(weather, "weather")

# ---- 4. Compute EPA / advanced stats -----------------------------------------

message("Computing EPA and advanced stats ...")

epa_2026 <- compute_team_epa(pbp_2026)
epa_hist <- compute_team_epa(pbp_hist)

rolling_hist_epa <- if (has_rows(epa_hist)) {
  epa_hist %>%
    group_by(team) %>%
    arrange(season, week, .by_group = TRUE) %>%
    mutate(
      rolling_off_epa = rollapply(
        off_epa_play,
        width = 10,
        FUN = mean,
        align = "right",
        fill = NA,
        partial = TRUE,
        na.rm = TRUE
      )
    ) %>%
    ungroup() %>%
    filter(season == max(season, na.rm = TRUE)) %>%
    group_by(team) %>%
    slice_max(week, n = 1, with_ties = FALSE) %>%
    ungroup() %>%
    select(team, prior_rolling_off_epa = rolling_off_epa)
} else {
  NULL
}

# ---- 5. Build master game-level dataset -------------------------------------

message("Building master game-level dataset ...")

# Normalize current-season games from API
# Note: home_score/away_score are final scores, not quarter-by-quarter line scores.
games_cur <- NULL
if (has_rows(games_2026)) {
  games_cur <- games_2026

  if ("start_date" %in% names(games_cur)) {
    games_cur <- games_cur %>% mutate(start_date = parse_start_date(start_date))
  } else {
    games_cur <- games_cur %>% mutate(start_date = as.Date(NA))
  }

  if ("season" %in% names(games_cur)) {
    games_cur <- games_cur %>% filter(season == CURRENT_SEASON)
  }
}

games_cur_played <- if (has_rows(games_cur)) {
  played_filter <- games_cur %>%
    filter(!is.na(start_date), start_date <= Sys.Date())

  if ("completed" %in% names(played_filter)) {
    played_filter <- played_filter %>% filter(completed %in% TRUE)
  }

  played_filter
} else {
  NULL
}

master_columns <- c(
  "game_id", "season", "week", "season_type",
  "home_team", "away_team", "home_conference", "away_conference",
  "home_points", "away_points",
  "start_date", "neutral_site", "conference_game",
  "venue_id", "venue"
)

if (has_rows(games_cur_played)) {
  master <- games_cur_played %>%
    select(any_of(master_columns))

  message(sprintf(
    "  Using %d played games from %d season API.",
    nrow(master),
    CURRENT_SEASON
  ))
} else if (has_rows(game_hist)) {
  master <- game_hist

  if ("start_date" %in% names(master)) {
    master <- master %>% mutate(start_date = parse_start_date(start_date))
  } else {
    master <- master %>% mutate(start_date = as.Date(NA))
  }

  master <- master %>%
    filter(season >= 2015, season <= CURRENT_SEASON - 1L) %>%
    select(any_of(master_columns))

  message(sprintf(
    "  No played %d games yet; using historical spine (%d rows).",
    CURRENT_SEASON,
    nrow(master)
  ))
} else if (has_rows(gamedata_2026)) {
  master <- gamedata_2026
  message("  Falling back to filtered seed file as spine.")
} else {
  stop("No game data available – cannot build master dataset.")
}

# Attach current-season EPA only when current-season games are actually the spine.
if (
  has_rows(epa_2026) &&
  has_rows(master) &&
  all(c("game_id", "season", "week", "home_team", "away_team") %in% names(master)) &&
  any(master$season == CURRENT_SEASON, na.rm = TRUE)
) {
  master <- master %>%
    left_join(
      epa_2026 %>%
        rename_with(~ paste0("home_", .), -c(game_id, season, week)),
      by = c("game_id", "home_team" = "team", "season", "week")
    ) %>%
    left_join(
      epa_2026 %>%
        rename_with(~ paste0("away_", .), -c(game_id, season, week)),
      by = c("game_id", "away_team" = "team", "season", "week")
    )
}

# Team talent: support both raw API `team` and wrapper-created `school`.
if (has_rows(talent) && "talent" %in% names(talent) && has_rows(master)) {
  talent_team_col <- first_existing_col(talent, c("team", "school"))

  if (!is.na(talent_team_col)) {
    talent_clean <- talent %>%
      transmute(
        team = as.character(.data[[talent_team_col]]),
        talent = suppressWarnings(as.numeric(talent)),
        talent_year = talent_year
      ) %>%
      filter(!is.na(team), team != "") %>%
      distinct(team, .keep_all = TRUE)

    master <- master %>%
      left_join(
        talent_clean %>%
          select(team, home_talent = talent, home_talent_year = talent_year),
        by = c("home_team" = "team")
      ) %>%
      left_join(
        talent_clean %>%
          select(team, away_talent = talent, away_talent_year = talent_year),
        by = c("away_team" = "team")
      )

    message(sprintf(
      "  Joined %d talent records using %d data.",
      nrow(talent_clean),
      talent_year
    ))
  } else {
    message("  Talent data contains neither 'team' nor 'school'; skipping talent joins.")
  }
} else {
  message("  No usable talent data available; skipping talent joins.")
}

# Betting lines: choose one provider deterministically rather than arbitrary slice(1).
if (has_columns(lines, "game_id") && has_rows(master) && "game_id" %in% names(master)) {
  preferred_providers <- c(
    "consensus",
    "DraftKings",
    "ESPN Bet",
    "Caesars",
    "Bovada"
  )

  if ("provider" %in% names(lines)) {
    lines <- lines %>%
      mutate(
        provider_rank = match(provider, preferred_providers),
        provider_rank = if_else(
          is.na(provider_rank),
          length(preferred_providers) + 1L,
          provider_rank
        )
      ) %>%
      arrange(game_id, provider_rank)
  }

  lines_clean <- lines %>%
    group_by(game_id) %>%
    slice_head(n = 1) %>%
    ungroup() %>%
    select(any_of(c(
      "game_id", "provider",
      "spread", "spread_open",
      "over_under", "over_under_open",
      "formatted_spread",
      "home_moneyline", "away_moneyline"
    )))

  master <- master %>% left_join(lines_clean, by = "game_id")
} else {
  message("  No usable betting-line data available; skipping line joins.")
}

# Weather is optional and may return 401 depending on account/API tier.
if (has_columns(weather, "game_id") && has_rows(master) && "game_id" %in% names(master)) {
  weather_clean <- weather %>%
    select(any_of(c(
      "game_id",
      "temperature",
      "dew_point",
      "humidity",
      "precipitation",
      "snowfall",
      "wind_speed",
      "wind_direction",
      "pressure",
      "weather_condition_code",
      "weather_condition",
      "weather_description"
    )))

  if (
    !"weather_condition" %in% names(weather_clean) &&
    "weather_description" %in% names(weather_clean)
  ) {
    weather_clean <- weather_clean %>%
      rename(weather_condition = weather_description)
  }

  weather_clean <- weather_clean %>%
    distinct(game_id, .keep_all = TRUE)

  master <- master %>% left_join(weather_clean, by = "game_id")
} else {
  message("  No usable weather data available; skipping weather join.")
}

# Historical rolling EPA is a team-level prior and can be joined to either spine.
if (has_rows(rolling_hist_epa) && has_rows(master)) {
  master <- master %>%
    left_join(
      rolling_hist_epa %>%
        rename(home_prior_rolling_epa = prior_rolling_off_epa),
      by = c("home_team" = "team")
    ) %>%
    left_join(
      rolling_hist_epa %>%
        rename(away_prior_rolling_epa = prior_rolling_off_epa),
      by = c("away_team" = "team")
    )
}

# Ensure score columns exist before result calculations.
if (!"home_points" %in% names(master)) master$home_points <- NA_real_
if (!"away_points" %in% names(master)) master$away_points <- NA_real_

master <- master %>%
  mutate(
    result = case_when(
      is.na(home_points) | is.na(away_points) ~ "scheduled",
      home_points > away_points ~ "home_win",
      away_points > home_points ~ "away_win",
      TRUE ~ "tie"
    ),
    point_diff = home_points - away_points,
    total_points = home_points + away_points
  ) %>%
  clean_names()

# ---- 6. Export outputs -------------------------------------------------------

message("Exporting outputs ...")

csv_file <- sprintf("output/cfb_data_2026_week_%02d.csv", current_week)
write_csv(master, csv_file)
write_csv(master, "output/cfb_data_2026_latest.csv")
message(sprintf(
  "  Wrote %s (%d rows, %d cols)",
  csv_file,
  nrow(master),
  ncol(master)
))

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
    "home_talent_year", "away_talent_year",
    "home_prior_rolling_epa", "away_prior_rolling_epa",
    "provider", "spread", "spread_open",
    "over_under", "over_under_open", "formatted_spread",
    "home_moneyline", "away_moneyline",
    "temperature", "dew_point", "humidity", "precipitation", "snowfall",
    "wind_speed", "wind_direction", "pressure",
    "weather_condition_code", "weather_condition"
  ),
  names(master)
)

json_data <- master %>% select(all_of(json_cols))
write_json(json_data, "docs/data/cfb-stats.json", pretty = FALSE, na = "null")
message(sprintf(
  "  Wrote docs/data/cfb-stats.json (%d records)",
  nrow(json_data)
))

meta <- list(
  generated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
  season = CURRENT_SEASON,
  current_week = current_week,
  talent_year_used = if (has_rows(talent)) talent_year else NULL,
  total_games = nrow(master),
  columns = names(json_data)
)

write_json(
  meta,
  "docs/data/cfb-meta.json",
  pretty = TRUE,
  auto_unbox = TRUE,
  null = "null"
)
message("  Wrote docs/data/cfb-meta.json")

message("CFB data pipeline complete.")
