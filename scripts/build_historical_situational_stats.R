#!/usr/bin/env Rscript

# ==============================================================================
# Historical CFB Situational Statistics Builder
# ==============================================================================
#
# Purpose:
#   Convert the historical play-by-play RDS into a compact, queryable
#   situational Parquet dataset for DuckDB, FastAPI, and server-side charts.
#
# Input:
#   data/downloads/pbp_2014_2025.RDS
#
# Outputs:
#   data/processed/historical_situational_stats.parquet
#   data/processed/historical_situational_metadata.json
#
# The output preserves enough dimensions for filters such as:
#   - season and week
#   - offense and defense
#   - conference
#   - home/away and neutral site
#   - quarter and half
#   - down
#   - distance bucket
#   - field-position bucket
#   - score-state bucket
#   - win-probability bucket
#   - play type
#   - red zone and goal-to-go
#   - competitive versus garbage-time plays
#
# The output stores additive totals so DuckDB can correctly calculate:
#   - EPA/play
#   - EPA/rush
#   - EPA/pass
#   - success rate
#   - explosive-play rate
#   - sack rate
#   - completion rate
#   - turnover rate
#
# ==============================================================================

suppressPackageStartupMessages({
  library(data.table)
  library(arrow)
  library(janitor)
  library(jsonlite)
})

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------

PBP_RDS_PATH <- Sys.getenv(
  "PBP_RDS_PATH",
  unset = "data/downloads/pbp_2014_2025.RDS"
)

OUTPUT_DIR <- Sys.getenv(
  "OUTPUT_DIR",
  unset = "data/processed"
)

MIN_SEASON <- suppressWarnings(
  as.integer(Sys.getenv("MIN_SEASON", unset = "2014"))
)

MAX_SEASON <- suppressWarnings(
  as.integer(Sys.getenv("MAX_SEASON", unset = "2025"))
)

OUTPUT_PARQUET <- file.path(
  OUTPUT_DIR,
  "historical_situational_stats.parquet"
)

OUTPUT_METADATA <- file.path(
  OUTPUT_DIR,
  "historical_situational_metadata.json"
)

dir.create(
  OUTPUT_DIR,
  recursive = TRUE,
  showWarnings = FALSE
)

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

message_section <- function(text) {
  message("")
  message("============================================================")
  message(text)
  message("============================================================")
}

first_existing_column <- function(data, candidates, required = FALSE) {
  found <- candidates[candidates %in% names(data)]

  if (length(found) > 0) {
    return(found[[1]])
  }

  if (required) {
    stop(
      sprintf(
        "None of the required columns were found: %s",
        paste(candidates, collapse = ", ")
      )
    )
  }

  NA_character_
}

to_numeric_safe <- function(x) {
  suppressWarnings(as.numeric(as.character(x)))
}

to_flag <- function(x) {
  if (is.logical(x)) {
    return(fifelse(is.na(x), FALSE, x))
  }

  if (is.numeric(x) || is.integer(x)) {
    return(!is.na(x) & x != 0)
  }

  normalized <- tolower(trimws(as.character(x)))

  normalized %in% c(
    "true",
    "t",
    "1",
    "yes",
    "y"
  )
}

add_numeric_column <- function(data, output_name, source_name) {
  if (!is.na(source_name) && source_name %in% names(data)) {
    set(
      data,
      j = output_name,
      value = to_numeric_safe(data[[source_name]])
    )
  } else {
    set(
      data,
      j = output_name,
      value = rep(NA_real_, nrow(data))
    )
  }
}

add_character_column <- function(data, output_name, source_name) {
  if (!is.na(source_name) && source_name %in% names(data)) {
    set(
      data,
      j = output_name,
      value = as.character(data[[source_name]])
    )
  } else {
    set(
      data,
      j = output_name,
      value = rep(NA_character_, nrow(data))
    )
  }
}

add_flag_column <- function(data, output_name, source_name) {
  if (!is.na(source_name) && source_name %in% names(data)) {
    set(
      data,
      j = output_name,
      value = to_flag(data[[source_name]])
    )
  } else {
    set(
      data,
      j = output_name,
      value = rep(FALSE, nrow(data))
    )
  }
}

safe_rate <- function(numerator, denominator) {
  fifelse(
    !is.na(denominator) & denominator > 0,
    numerator / denominator,
    NA_real_
  )
}

# ------------------------------------------------------------------------------
# Validate input
# ------------------------------------------------------------------------------

message_section("Validating historical PBP source")

if (!file.exists(PBP_RDS_PATH)) {
  stop(
    sprintf(
      "Historical PBP RDS was not found: %s",
      PBP_RDS_PATH
    )
  )
}

pbp_size <- file.info(PBP_RDS_PATH)$size

message(
  sprintf(
    "Historical PBP file: %s",
    PBP_RDS_PATH
  )
)

message(
  sprintf(
    "Compressed file size: %.1f MB",
    pbp_size / 1024^2
  )
)

# ------------------------------------------------------------------------------
# Load RDS
# ------------------------------------------------------------------------------

message_section("Loading historical PBP RDS")

pbp <- readRDS(PBP_RDS_PATH)
setDT(pbp)

message(
  sprintf(
    "Loaded %s rows and %s columns.",
    format(nrow(pbp), big.mark = ","),
    format(ncol(pbp), big.mark = ",")
  )
)

# Normalize names such as:
# EPA              -> epa
# TimeSecsRem      -> time_secs_rem
# Goal_To_Go       -> goal_to_go
# offense_EPA      -> offense_epa

setnames(
  pbp,
  janitor::make_clean_names(names(pbp))
)

# ------------------------------------------------------------------------------
# Resolve actual source fields
# ------------------------------------------------------------------------------

message_section("Resolving PBP schema")

season_col <- first_existing_column(
  pbp,
  c("season", "year"),
  required = TRUE
)

week_col <- first_existing_column(
  pbp,
  c("week"),
  required = TRUE
)

game_id_col <- first_existing_column(
  pbp,
  c("game_id"),
  required = TRUE
)

offense_col <- first_existing_column(
  pbp,
  c("pos_team", "offense_play", "team"),
  required = TRUE
)

defense_col <- first_existing_column(
  pbp,
  c("def_pos_team", "defense_play", "opponent"),
  required = TRUE
)

offense_conference_col <- first_existing_column(
  pbp,
  c("offense_conference", "conference")
)

defense_conference_col <- first_existing_column(
  pbp,
  c("defense_conference")
)

epa_col <- first_existing_column(
  pbp,
  c("epa", "ppa"),
  required = TRUE
)

success_col <- first_existing_column(
  pbp,
  c("success", "epa_success")
)

period_col <- first_existing_column(
  pbp,
  c("period"),
  required = TRUE
)

half_col <- first_existing_column(
  pbp,
  c("half")
)

clock_minutes_col <- first_existing_column(
  pbp,
  c("clock_minutes", "clock_minutes_2")
)

down_col <- first_existing_column(
  pbp,
  c("down"),
  required = TRUE
)

distance_col <- first_existing_column(
  pbp,
  c("distance"),
  required = TRUE
)

yards_to_goal_col <- first_existing_column(
  pbp,
  c("yards_to_goal"),
  required = TRUE
)

yards_gained_col <- first_existing_column(
  pbp,
  c("yards_gained"),
  required = TRUE
)

wp_col <- first_existing_column(
  pbp,
  c("wp_before", "home_wp_before")
)

score_diff_col <- first_existing_column(
  pbp,
  c(
    "pos_score_diff",
    "pos_score_diff_start",
    "score_diff_start",
    "score_diff"
  )
)

rush_col <- first_existing_column(
  pbp,
  c("rush")
)

pass_col <- first_existing_column(
  pbp,
  c("pass")
)

pass_attempt_col <- first_existing_column(
  pbp,
  c("pass_attempt")
)

completion_col <- first_existing_column(
  pbp,
  c("completion")
)

sack_col <- first_existing_column(
  pbp,
  c("sack", "sack_vec")
)

turnover_col <- first_existing_column(
  pbp,
  c("turnover", "turnover_vec", "turnover_indicator")
)

penalty_no_play_col <- first_existing_column(
  pbp,
  c("penalty_no_play")
)

red_zone_col <- first_existing_column(
  pbp,
  c("rz_play")
)

goal_to_go_col <- first_existing_column(
  pbp,
  c("goal_to_go")
)

scoring_opportunity_col <- first_existing_column(
  pbp,
  c("scoring_opp")
)

stuffed_run_col <- first_existing_column(
  pbp,
  c("stuffed_run")
)

first_down_col <- first_existing_column(
  pbp,
  c(
    "first_d_by_yards",
    "first_d_by_poss",
    "new_series"
  )
)

touchdown_col <- first_existing_column(
  pbp,
  c("touchdown", "td_play")
)

points_col <- first_existing_column(
  pbp,
  c("pos_score_pts", "score_pts", "new_drive_pts")
)

home_team_col <- first_existing_column(
  pbp,
  c("home_team", "home")
)

away_team_col <- first_existing_column(
  pbp,
  c("away_team", "away")
)

neutral_site_col <- first_existing_column(
  pbp,
  c("neutral_site")
)

season_type_col <- first_existing_column(
  pbp,
  c("season_type")
)

resolved_fields <- list(
  season = season_col,
  week = week_col,
  game_id = game_id_col,
  offense = offense_col,
  defense = defense_col,
  offense_conference = offense_conference_col,
  defense_conference = defense_conference_col,
  epa = epa_col,
  success = success_col,
  period = period_col,
  half = half_col,
  clock_minutes = clock_minutes_col,
  down = down_col,
  distance = distance_col,
  yards_to_goal = yards_to_goal_col,
  yards_gained = yards_gained_col,
  win_probability = wp_col,
  score_difference = score_diff_col,
  rush = rush_col,
  pass = pass_col,
  pass_attempt = pass_attempt_col,
  completion = completion_col,
  sack = sack_col,
  turnover = turnover_col,
  penalty_no_play = penalty_no_play_col,
  red_zone = red_zone_col,
  goal_to_go = goal_to_go_col,
  scoring_opportunity = scoring_opportunity_col,
  stuffed_run = stuffed_run_col,
  first_down = first_down_col,
  touchdown = touchdown_col,
  points = points_col,
  home_team = home_team_col,
  away_team = away_team_col,
  neutral_site = neutral_site_col,
  season_type = season_type_col
)

for (field_name in names(resolved_fields)) {
  message(
    sprintf(
      "  %-24s %s",
      paste0(field_name, ":"),
      ifelse(
        is.na(resolved_fields[[field_name]]),
        "<not available>",
        resolved_fields[[field_name]]
      )
    )
  )
}

# ------------------------------------------------------------------------------
# Retain only required source columns
# ------------------------------------------------------------------------------

message_section("Dropping unused PBP columns")

source_columns <- unique(
  na.omit(
    unlist(resolved_fields, use.names = FALSE)
  )
)

source_columns <- intersect(
  source_columns,
  names(pbp)
)

drop_columns <- setdiff(
  names(pbp),
  source_columns
)

if (length(drop_columns) > 0) {
  set(
    pbp,
    j = drop_columns,
    value = NULL
  )
}

gc(verbose = FALSE)

message(
  sprintf(
    "Retained %d source columns.",
    ncol(pbp)
  )
)

# ------------------------------------------------------------------------------
# Create canonical columns
# ------------------------------------------------------------------------------

message_section("Creating canonical situational fields")

add_numeric_column(pbp, "season_value", season_col)
add_numeric_column(pbp, "week_value", week_col)
add_character_column(pbp, "game_id_value", game_id_col)

add_character_column(pbp, "team", offense_col)
add_character_column(pbp, "opponent", defense_col)

add_character_column(
  pbp,
  "offense_conference_value",
  offense_conference_col
)

add_character_column(
  pbp,
  "defense_conference_value",
  defense_conference_col
)

add_numeric_column(pbp, "epa_value", epa_col)
add_numeric_column(pbp, "period_value", period_col)
add_numeric_column(pbp, "half_value", half_col)
add_numeric_column(pbp, "clock_minutes_value", clock_minutes_col)
add_numeric_column(pbp, "down_value", down_col)
add_numeric_column(pbp, "distance_value", distance_col)
add_numeric_column(pbp, "yards_to_goal_value", yards_to_goal_col)
add_numeric_column(pbp, "yards_gained_value", yards_gained_col)
add_numeric_column(pbp, "wp_before_value", wp_col)
add_numeric_column(pbp, "score_diff_value", score_diff_col)
add_numeric_column(pbp, "points_value", points_col)

add_flag_column(pbp, "rush_source_flag", rush_col)
add_flag_column(pbp, "pass_source_flag", pass_col)
add_flag_column(pbp, "pass_attempt_source_flag", pass_attempt_col)
add_flag_column(pbp, "completion_flag", completion_col)
add_flag_column(pbp, "sack_flag", sack_col)
add_flag_column(pbp, "turnover_flag", turnover_col)
add_flag_column(pbp, "penalty_no_play_flag", penalty_no_play_col)
add_flag_column(pbp, "red_zone_source_flag", red_zone_col)
add_flag_column(pbp, "goal_to_go_flag", goal_to_go_col)
add_flag_column(
  pbp,
  "scoring_opportunity_flag",
  scoring_opportunity_col
)
add_flag_column(pbp, "stuffed_run_flag", stuffed_run_col)
add_flag_column(pbp, "first_down_source_flag", first_down_col)
add_flag_column(pbp, "touchdown_flag", touchdown_col)
add_flag_column(pbp, "neutral_site_flag", neutral_site_col)

add_character_column(pbp, "home_team_value", home_team_col)
add_character_column(pbp, "away_team_value", away_team_col)
add_character_column(pbp, "season_type_value", season_type_col)

# Some sources store win probability as 0-100 instead of 0-1.
pbp[
  !is.na(wp_before_value) & wp_before_value > 1,
  wp_before_value := wp_before_value / 100
]

pbp[
  !is.na(wp_before_value),
  wp_before_value := pmin(
    pmax(wp_before_value, 0),
    1
  )
]

# Treat sacks as passing plays.
pbp[
  ,
  pass_flag := (
    pass_source_flag |
    pass_attempt_source_flag |
    sack_flag
  )
]

pbp[
  ,
  rush_flag := (
    rush_source_flag &
    !pass_flag
  )
]

pbp[
  ,
  play_type_value := fifelse(
    pass_flag,
    "pass",
    fifelse(
      rush_flag,
      "rush",
      NA_character_
    )
  )
]

# If an explicit success variable is unavailable, EPA > 0 is used.
if (!is.na(success_col)) {
  add_flag_column(
    pbp,
    "success_source_flag",
    success_col
  )

  pbp[
    ,
    success_flag := success_source_flag
  ]
} else {
  pbp[
    ,
    success_flag := !is.na(epa_value) & epa_value > 0
  ]
}

# Recalculate red zone from yards-to-goal when necessary.
pbp[
  ,
  red_zone_flag := (
    red_zone_source_flag |
    (
      !is.na(yards_to_goal_value) &
      yards_to_goal_value <= 20
    )
  )
]

# Explosive-play definitions:
#   Rush: 10 or more yards
#   Pass: 20 or more yards
pbp[
  ,
  explosive_flag := (
    (
      play_type_value == "rush" &
      !is.na(yards_gained_value) &
      yards_gained_value >= 10
    ) |
    (
      play_type_value == "pass" &
      !is.na(yards_gained_value) &
      yards_gained_value >= 20
    )
  )
]

# Derive first downs from explicit flags or yards gained.
pbp[
  ,
  first_down_flag := (
    first_down_source_flag |
    (
      !is.na(down_value) &
      down_value %in% 1:4 &
      !is.na(distance_value) &
      !is.na(yards_gained_value) &
      yards_gained_value >= distance_value
    )
  )
]

pbp[
  ,
  pass_attempt_flag := (
    play_type_value == "pass"
  )
]

pbp[
  ,
  rush_attempt_flag := (
    play_type_value == "rush"
  )
]

pbp[
  ,
  points_value := fifelse(
    is.na(points_value),
    0,
    pmax(points_value, 0)
  )
]

# ------------------------------------------------------------------------------
# Filter qualifying offensive plays
# ------------------------------------------------------------------------------

message_section("Filtering qualifying plays")

before_filter <- nrow(pbp)

pbp <- pbp[
  season_value >= MIN_SEASON &
    season_value <= MAX_SEASON &
    !is.na(team) &
    team != "" &
    !is.na(opponent) &
    opponent != "" &
    !is.na(epa_value) &
    !is.na(play_type_value) &
    down_value %in% 1:4 &
    !penalty_no_play_flag
]

gc(verbose = FALSE)

message(
  sprintf(
    "Qualifying plays: %s of %s rows.",
    format(nrow(pbp), big.mark = ","),
    format(before_filter, big.mark = ",")
  )
)

if (nrow(pbp) == 0) {
  stop("No qualifying plays remained after filtering.")
}

# ------------------------------------------------------------------------------
# Create filter buckets
# ------------------------------------------------------------------------------

message_section("Creating filter buckets")

pbp[
  ,
  distance_bucket := fcase(
    is.na(distance_value), "Unknown",
    distance_value <= 1, "1 yard",
    distance_value <= 3, "2-3 yards",
    distance_value <= 6, "4-6 yards",
    distance_value <= 10, "7-10 yards",
    default = "11+ yards"
  )
]

pbp[
  ,
  field_position_bucket := fcase(
    is.na(yards_to_goal_value), "Unknown",
    yards_to_goal_value <= 5, "Opponent 1-5",
    yards_to_goal_value <= 20, "Red zone 6-20",
    yards_to_goal_value <= 40, "Opponent 21-40",
    yards_to_goal_value <= 60, "Midfield",
    yards_to_goal_value <= 80, "Own 21-40",
    default = "Own 1-20"
  )
]

pbp[
  ,
  score_state := fcase(
    is.na(score_diff_value), "Unknown",
    score_diff_value <= -15, "Trailing 15+",
    score_diff_value <= -8, "Trailing 8-14",
    score_diff_value <= -1, "Trailing 1-7",
    score_diff_value == 0, "Tied",
    score_diff_value <= 7, "Leading 1-7",
    score_diff_value <= 14, "Leading 8-14",
    default = "Leading 15+"
  )
]

pbp[
  ,
  wp_bucket := fcase(
    is.na(wp_before_value), "Unknown",
    wp_before_value < 0.05, "0-5%",
    wp_before_value < 0.20, "5-20%",
    wp_before_value < 0.40, "20-40%",
    wp_before_value < 0.60, "40-60%",
    wp_before_value < 0.80, "60-80%",
    wp_before_value <= 0.95, "80-95%",
    default = "95-100%"
  )
]

# Default garbage-time definition:
# plays where the offense's pre-play win probability is below 5% or above 95%.
pbp[
  ,
  garbage_time := (
    !is.na(wp_before_value) &
    (
      wp_before_value < 0.05 |
      wp_before_value > 0.95
    )
  )
]

pbp[
  ,
  clock_bucket := fcase(
    is.na(clock_minutes_value), "Unknown",
    clock_minutes_value >= 10, "10:00-15:00",
    clock_minutes_value >= 5, "5:00-9:59",
    default = "0:00-4:59"
  )
]

pbp[
  ,
  home_away := fcase(
    !is.na(home_team_value) &
      team == home_team_value, "home",
    !is.na(away_team_value) &
      team == away_team_value, "away",
    neutral_site_flag, "neutral",
    default = "unknown"
  )
]

pbp[
  ,
  season_type_value := fifelse(
    is.na(season_type_value) |
      trimws(season_type_value) == "",
    "regular",
    tolower(trimws(season_type_value))
  )
]

pbp[
  ,
  half_value := fifelse(
    !is.na(half_value),
    half_value,
    fifelse(
      period_value <= 2,
      1,
      2
    )
  )
]

# ------------------------------------------------------------------------------
# Aggregate situational totals
# ------------------------------------------------------------------------------

message_section("Aggregating situational statistics")

situational_stats <- pbp[
  ,
  .(
    plays = .N,

    epa_total = sum(
      epa_value,
      na.rm = TRUE
    ),

    successes = sum(
      success_flag,
      na.rm = TRUE
    ),

    yards_total = sum(
      yards_gained_value,
      na.rm = TRUE
    ),

    explosive_plays = sum(
      explosive_flag,
      na.rm = TRUE
    ),

    first_downs = sum(
      first_down_flag,
      na.rm = TRUE
    ),

    turnovers = sum(
      turnover_flag,
      na.rm = TRUE
    ),

    sacks = sum(
      sack_flag,
      na.rm = TRUE
    ),

    pass_attempts = sum(
      pass_attempt_flag,
      na.rm = TRUE
    ),

    completions = sum(
      completion_flag & pass_attempt_flag,
      na.rm = TRUE
    ),

    rush_attempts = sum(
      rush_attempt_flag,
      na.rm = TRUE
    ),

    stuffed_runs = sum(
      stuffed_run_flag & rush_attempt_flag,
      na.rm = TRUE
    ),

    touchdowns = sum(
      touchdown_flag,
      na.rm = TRUE
    ),

    points = sum(
      points_value,
      na.rm = TRUE
    ),

    scoring_opportunity_plays = sum(
      scoring_opportunity_flag,
      na.rm = TRUE
    )
  ),
  by = .(
    season = as.integer(season_value),
    week = as.integer(week_value),
    game_id = game_id_value,

    team,
    opponent,

    offense_conference = offense_conference_value,
    defense_conference = defense_conference_value,

    home_away,
    neutral_site = neutral_site_flag,
    season_type = season_type_value,

    period = as.integer(period_value),
    half = as.integer(half_value),
    clock_bucket,

    down = as.integer(down_value),
    distance_bucket,
    field_position_bucket,
    score_state,
    wp_bucket,

    play_type = play_type_value,

    red_zone = red_zone_flag,
    goal_to_go = goal_to_go_flag,
    garbage_time
  )
]

setorder(
  situational_stats,
  season,
  week,
  team,
  opponent,
  period,
  down,
  play_type
)

message(
  sprintf(
    "Created %s aggregated situational rows.",
    format(nrow(situational_stats), big.mark = ",")
  )
)

# ------------------------------------------------------------------------------
# Add convenience rates
# ------------------------------------------------------------------------------

# These rates are included for inspection, but downstream DuckDB queries should
# recalculate rates from summed totals whenever multiple rows are combined.

situational_stats[
  ,
  epa_per_play := safe_rate(
    epa_total,
    plays
  )
]

situational_stats[
  ,
  success_rate := safe_rate(
    successes,
    plays
  )
]

situational_stats[
  ,
  yards_per_play := safe_rate(
    yards_total,
    plays
  )
]

situational_stats[
  ,
  explosive_rate := safe_rate(
    explosive_plays,
    plays
  )
]

situational_stats[
  ,
  turnover_rate := safe_rate(
    turnovers,
    plays
  )
]

situational_stats[
  ,
  sack_rate := safe_rate(
    sacks,
    pass_attempts
  )
]

situational_stats[
  ,
  completion_rate := safe_rate(
    completions,
    pass_attempts
  )
]

situational_stats[
  ,
  stuffed_run_rate := safe_rate(
    stuffed_runs,
    rush_attempts
  )
]

# ------------------------------------------------------------------------------
# Validate output
# ------------------------------------------------------------------------------

message_section("Validating situational output")

required_output_columns <- c(
  "season",
  "week",
  "game_id",
  "team",
  "opponent",
  "period",
  "down",
  "play_type",
  "plays",
  "epa_total",
  "successes"
)

missing_output_columns <- setdiff(
  required_output_columns,
  names(situational_stats)
)

if (length(missing_output_columns) > 0) {
  stop(
    sprintf(
      "Situational output is missing required columns: %s",
      paste(missing_output_columns, collapse = ", ")
    )
  )
}

if (any(situational_stats$plays <= 0, na.rm = TRUE)) {
  stop("Situational output contains non-positive play counts.")
}

season_summary <- situational_stats[
  ,
  .(
    rows = .N,
    teams = uniqueN(team),
    plays = sum(plays),
    epa_per_play = sum(epa_total) / sum(plays)
  ),
  by = season
][order(season)]

print(season_summary)

alabama_example <- situational_stats[
  team == "Alabama" &
    season == MAX_SEASON &
    play_type == "rush",
  .(
    rush_plays = sum(plays),
    epa_per_rush = sum(epa_total) / sum(plays),
    success_rate = sum(successes) / sum(plays)
  )
]

message("Alabama example:")
print(alabama_example)

# ------------------------------------------------------------------------------
# Write output
# ------------------------------------------------------------------------------

message_section("Writing situational Parquet output")

arrow::write_parquet(
  as.data.frame(situational_stats),
  sink = OUTPUT_PARQUET,
  compression = "zstd"
)

output_size <- file.info(OUTPUT_PARQUET)$size

message(
  sprintf(
    "Wrote %s",
    OUTPUT_PARQUET
  )
)

message(
  sprintf(
    "Parquet size: %.1f MB",
    output_size / 1024^2
  )
)

metadata <- list(
  generated_at = format(
    Sys.time(),
    "%Y-%m-%dT%H:%M:%SZ",
    tz = "UTC"
  ),
  source_file = basename(PBP_RDS_PATH),
  min_season = MIN_SEASON,
  max_season = MAX_SEASON,
  source_rows = before_filter,
  qualifying_plays = sum(situational_stats$plays),
  aggregated_rows = nrow(situational_stats),
  teams = uniqueN(situational_stats$team),
  output_file = basename(OUTPUT_PARQUET),
  output_bytes = unname(output_size),
  epa_source_column = epa_col,
  offense_source_column = offense_col,
  defense_source_column = defense_col,
  garbage_time_definition = paste(
    "Offense pre-play win probability below 5%",
    "or above 95%"
  ),
  explosive_rush_definition = "10 or more yards",
  explosive_pass_definition = "20 or more yards",
  pass_definition = paste(
    "Pass flag, pass-attempt flag, or sack flag;",
    "sacks are treated as passing plays"
  ),
  dimensions = c(
    "season",
    "week",
    "game_id",
    "team",
    "opponent",
    "offense_conference",
    "defense_conference",
    "home_away",
    "neutral_site",
    "season_type",
    "period",
    "half",
    "clock_bucket",
    "down",
    "distance_bucket",
    "field_position_bucket",
    "score_state",
    "wp_bucket",
    "play_type",
    "red_zone",
    "goal_to_go",
    "garbage_time"
  ),
  additive_measures = c(
    "plays",
    "epa_total",
    "successes",
    "yards_total",
    "explosive_plays",
    "first_downs",
    "turnovers",
    "sacks",
    "pass_attempts",
    "completions",
    "rush_attempts",
    "stuffed_runs",
    "touchdowns",
    "points",
    "scoring_opportunity_plays"
  )
)

jsonlite::write_json(
  metadata,
  path = OUTPUT_METADATA,
  pretty = TRUE,
  auto_unbox = TRUE,
  na = "null"
)

message(
  sprintf(
    "Wrote %s",
    OUTPUT_METADATA
  )
)

# ------------------------------------------------------------------------------
# Release memory
# ------------------------------------------------------------------------------

rm(pbp)
rm(situational_stats)
gc(verbose = FALSE)

message_section("Historical situational statistics build complete")
