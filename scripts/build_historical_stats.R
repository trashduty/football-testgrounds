#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(data.table)
  library(arrow)
  library(jsonlite)
})

options(datatable.print.nrows = 20)

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

PBP_FILE   <- Sys.getenv("PBP_RDS_PATH",   "data/downloads/pbp_2014_2025.RDS")
GAMES_FILE <- Sys.getenv("GAMES_RDS_PATH", "data/downloads/games_2014_2025.RDS")
OUTPUT_DIR <- Sys.getenv("OUTPUT_DIR",      "data/processed")

MIN_SEASON <- as.integer(Sys.getenv("MIN_SEASON", "2014"))
MAX_SEASON <- as.integer(Sys.getenv("MAX_SEASON", "2025"))

if (!file.exists(PBP_FILE)) stop("PBP RDS file not found: ", PBP_FILE)
if (!file.exists(GAMES_FILE)) stop("Games RDS file not found: ", GAMES_FILE)

dir.create(OUTPUT_DIR, recursive = TRUE, showWarnings = FALSE)

message("Historical CFB statistics build")
message("  PBP file:   ", PBP_FILE)
message("  Games file: ", GAMES_FILE)
message("  Seasons:    ", MIN_SEASON, "-", MAX_SEASON)
message("  Output:     ", OUTPUT_DIR)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

clean_names_base <- function(x) {
  x <- gsub("([a-z0-9])([A-Z])", "\\1_\\2", x)
  x <- tolower(x)
  x <- gsub("[^a-z0-9]+", "_", x)
  x <- gsub("^_+|_+$", "", x)
  make.unique(x, sep = "_")
}

first_existing <- function(candidates, available) {
  found <- intersect(candidates, available)
  if (length(found) == 0L) NA_character_ else found[[1L]]
}

flag01 <- function(x) {
  if (is.logical(x)) return(as.integer(!is.na(x) & x))
  if (is.numeric(x) || is.integer(x)) return(as.integer(!is.na(x) & x == 1))
  y <- tolower(trimws(as.character(x)))
  as.integer(y %in% c("1", "true", "t", "yes", "y"))
}

safe_divide <- function(numerator, denominator) {
  fifelse(!is.na(denominator) & denominator > 0, numerator / denominator, NA_real_)
}

mode_nonmissing <- function(x) {
  x <- x[!is.na(x) & x != ""]
  if (length(x) == 0L) return(NA_character_)
  names(sort(table(x), decreasing = TRUE))[1L]
}

write_output <- function(dt, stem) {
  parquet_path <- file.path(OUTPUT_DIR, paste0(stem, ".parquet"))
  csv_path <- file.path(OUTPUT_DIR, paste0(stem, ".csv.gz"))

  arrow::write_parquet(
    as.data.frame(dt),
    sink = parquet_path,
    compression = "zstd"
  )
  data.table::fwrite(dt, csv_path, compress = "gzip", na = "")

  message(sprintf(
    "  Wrote %-34s %10s rows | %s",
    basename(parquet_path),
    format(nrow(dt), big.mark = ","),
    format(file.info(parquet_path)$size, big.mark = ",")
  ))
}

require_columns <- function(dt, required, label) {
  missing <- setdiff(required, names(dt))
  if (length(missing) > 0L) {
    stop(label, " is missing required columns: ", paste(missing, collapse = ", "))
  }
}

# -----------------------------------------------------------------------------
# 1. Load and normalize games
# -----------------------------------------------------------------------------

message("\nLoading games RDS ...")
games <- readRDS(GAMES_FILE)
setDT(games)
setnames(games, clean_names_base(names(games)))

require_columns(
  games,
  c(
    "game_id", "season", "week", "season_type", "start_date",
    "home_team", "away_team", "home_points", "away_points"
  ),
  "Games data"
)

game_keep <- intersect(
  c(
    "game_id", "season", "week", "season_type", "start_date", "completed",
    "neutral_site", "conference_game", "venue_id", "venue",
    "home_team", "home_conference", "home_division", "home_points",
    "away_team", "away_conference", "away_division", "away_points"
  ),
  names(games)
)
games[, (setdiff(names(games), game_keep)) := NULL]
games <- games[season >= MIN_SEASON & season <= MAX_SEASON]
games[, game_id := as.character(game_id)]
games[, season := as.integer(season)]
games[, week := as.integer(week)]
games[, start_date := as.IDate(start_date)]
setkey(games, game_id)

message("  Loaded games: ", format(nrow(games), big.mark = ","))

home_context <- games[, .(
  game_id,
  season,
  week,
  season_type,
  start_date,
  team = home_team,
  opponent = away_team,
  home_away = "home",
  team_conference = if ("home_conference" %in% names(games)) home_conference else NA_character_,
  team_division = if ("home_division" %in% names(games)) home_division else NA_character_,
  opponent_conference = if ("away_conference" %in% names(games)) away_conference else NA_character_,
  opponent_division = if ("away_division" %in% names(games)) away_division else NA_character_,
  points_for = as.numeric(home_points),
  points_against = as.numeric(away_points),
  neutral_site = if ("neutral_site" %in% names(games)) neutral_site else NA,
  conference_game = if ("conference_game" %in% names(games)) conference_game else NA,
  venue = if ("venue" %in% names(games)) venue else NA_character_
)]

away_context <- games[, .(
  game_id,
  season,
  week,
  season_type,
  start_date,
  team = away_team,
  opponent = home_team,
  home_away = "away",
  team_conference = if ("away_conference" %in% names(games)) away_conference else NA_character_,
  team_division = if ("away_division" %in% names(games)) away_division else NA_character_,
  opponent_conference = if ("home_conference" %in% names(games)) home_conference else NA_character_,
  opponent_division = if ("home_division" %in% names(games)) home_division else NA_character_,
  points_for = as.numeric(away_points),
  points_against = as.numeric(home_points),
  neutral_site = if ("neutral_site" %in% names(games)) neutral_site else NA,
  conference_game = if ("conference_game" %in% names(games)) conference_game else NA,
  venue = if ("venue" %in% names(games)) venue else NA_character_
)]

game_context <- rbindlist(list(home_context, away_context), use.names = TRUE)
game_context <- game_context[!is.na(team) & team != ""]
setkey(game_context, season, week, game_id, team)

rm(home_context, away_context)
gc(verbose = FALSE)

# -----------------------------------------------------------------------------
# 2. Load, normalize, and reduce play-by-play
# -----------------------------------------------------------------------------

message("\nLoading 4.2 GB play-by-play RDS ...")
message("  This is the memory-intensive step.")
pbp <- readRDS(PBP_FILE)
setDT(pbp)
setnames(pbp, clean_names_base(names(pbp)))
message("  Loaded PBP rows: ", format(nrow(pbp), big.mark = ","))

# Resolve known schema variants before dropping columns.
epa_col <- first_existing(c("epa", "ppa"), names(pbp))
offense_col <- first_existing(c("pos_team", "offense_play", "offense", "team"), names(pbp))
defense_col <- first_existing(c("def_pos_team", "defense_play", "defense", "opponent"), names(pbp))
pass_col <- first_existing(c("pass", "pass_attempt"), names(pbp))
rush_col <- first_existing(c("rush", "rush_attempt"), names(pbp))
sack_col <- first_existing(c("sack", "sack_vec"), names(pbp))
success_col <- first_existing(c("success", "epa_success"), names(pbp))
no_play_col <- first_existing(c("penalty_no_play", "no_play"), names(pbp))

resolved <- c(
  epa = epa_col,
  offense_team = offense_col,
  defense_team = defense_col,
  pass = pass_col,
  rush = rush_col,
  sack = sack_col,
  success = success_col,
  no_play = no_play_col
)
message("  Resolved PBP fields:")
for (nm in names(resolved)) message("    ", nm, ": ", resolved[[nm]])

if (is.na(epa_col)) stop("Could not find EPA or PPA column in PBP data.")
if (is.na(offense_col)) stop("Could not find offensive team column in PBP data.")
if (is.na(defense_col)) stop("Could not find defensive team column in PBP data.")
if (is.na(rush_col) && is.na(pass_col)) stop("Could not find rush or pass indicators in PBP data.")

pbp_keep <- unique(na.omit(c(
  "game_id", "season", "week", "season_type", "down",
  epa_col, offense_col, defense_col,
  pass_col, rush_col, sack_col, success_col, no_play_col
)))
missing_core <- setdiff(c("game_id", "season", "week"), names(pbp))
if (length(missing_core) > 0L) {
  stop("PBP data is missing required identifiers: ", paste(missing_core, collapse = ", "))
}

# Drop hundreds of unused columns in place to reduce memory pressure.
pbp[, (setdiff(names(pbp), pbp_keep)) := NULL]
gc(verbose = FALSE)

# Canonical columns.
pbp[, game_id := as.character(game_id)]
pbp[, season := as.integer(season)]
pbp[, week := as.integer(week)]
pbp[, offense_team := as.character(get(offense_col))]
pbp[, defense_team := as.character(get(defense_col))]
pbp[, epa_value := suppressWarnings(as.numeric(get(epa_col)))]
pbp[, rush_flag := if (!is.na(rush_col)) flag01(get(rush_col)) else 0L]
pbp[, pass_flag := if (!is.na(pass_col)) flag01(get(pass_col)) else 0L]
if (!is.na(sack_col)) pbp[, pass_flag := pmax(pass_flag, flag01(get(sack_col)), na.rm = TRUE)]
pbp[, no_play_flag := if (!is.na(no_play_col)) flag01(get(no_play_col)) else 0L]
pbp[, success_flag := if (!is.na(success_col)) flag01(get(success_col)) else as.integer(epa_value > 0)]

# Restrict to usable offensive scrimmage plays. Rush/pass indicators determine
# the play universe, and accepted no-play penalties are excluded.
pbp <- pbp[
  season >= MIN_SEASON & season <= MAX_SEASON &
    !is.na(game_id) &
    !is.na(offense_team) & offense_team != "" &
    !is.na(defense_team) & defense_team != "" &
    !is.na(epa_value) &
    no_play_flag == 0L &
    (rush_flag == 1L | pass_flag == 1L)
]

message("  Usable offensive scrimmage plays: ", format(nrow(pbp), big.mark = ","))
if (nrow(pbp) == 0L) stop("No usable scrimmage plays remained after filtering.")

# -----------------------------------------------------------------------------
# 3. Team-game offense and defense aggregates
# -----------------------------------------------------------------------------

message("\nBuilding team-game statistics ...")

off_game <- pbp[, .(
  off_plays = .N,
  off_epa_total = sum(epa_value),
  off_successes = sum(success_flag, na.rm = TRUE),
  off_rush_plays = sum(rush_flag),
  off_rush_epa_total = sum(epa_value[rush_flag == 1L]),
  off_rush_successes = sum(success_flag[rush_flag == 1L], na.rm = TRUE),
  off_pass_plays = sum(pass_flag),
  off_pass_epa_total = sum(epa_value[pass_flag == 1L]),
  off_pass_successes = sum(success_flag[pass_flag == 1L], na.rm = TRUE)
), by = .(
  season,
  week,
  game_id,
  team = offense_team
)]

def_game <- pbp[, .(
  def_plays = .N,
  def_epa_allowed_total = sum(epa_value),
  def_successes_allowed = sum(success_flag, na.rm = TRUE),
  def_rush_plays = sum(rush_flag),
  def_rush_epa_allowed_total = sum(epa_value[rush_flag == 1L]),
  def_rush_successes_allowed = sum(success_flag[rush_flag == 1L], na.rm = TRUE),
  def_pass_plays = sum(pass_flag),
  def_pass_epa_allowed_total = sum(epa_value[pass_flag == 1L]),
  def_pass_successes_allowed = sum(success_flag[pass_flag == 1L], na.rm = TRUE)
), by = .(
  season,
  week,
  game_id,
  team = defense_team
)]

# The raw play-level object is no longer needed.
rm(pbp)
gc(verbose = FALSE)

team_game <- merge(
  off_game,
  def_game,
  by = c("season", "week", "game_id", "team"),
  all = TRUE
)
team_game <- merge(
  game_context,
  team_game,
  by = c("season", "week", "game_id", "team"),
  all.y = TRUE
)

team_game[, `:=`(
  off_epa_per_play = safe_divide(off_epa_total, off_plays),
  off_epa_per_rush = safe_divide(off_rush_epa_total, off_rush_plays),
  off_epa_per_pass = safe_divide(off_pass_epa_total, off_pass_plays),
  off_success_rate = safe_divide(off_successes, off_plays),
  off_rush_success_rate = safe_divide(off_rush_successes, off_rush_plays),
  off_pass_success_rate = safe_divide(off_pass_successes, off_pass_plays),
  def_epa_allowed_per_play = safe_divide(def_epa_allowed_total, def_plays),
  def_epa_allowed_per_rush = safe_divide(def_rush_epa_allowed_total, def_rush_plays),
  def_epa_allowed_per_pass = safe_divide(def_pass_epa_allowed_total, def_pass_plays),
  def_success_rate_allowed = safe_divide(def_successes_allowed, def_plays),
  def_rush_success_rate_allowed = safe_divide(def_rush_successes_allowed, def_rush_plays),
  def_pass_success_rate_allowed = safe_divide(def_pass_successes_allowed, def_pass_plays)
)]

setorder(team_game, season, week, game_id, team)

# -----------------------------------------------------------------------------
# 4. Team-season weighted aggregates
# -----------------------------------------------------------------------------

message("Building team-season statistics ...")

team_season <- team_game[, .(
  conference = mode_nonmissing(team_conference),
  division = mode_nonmissing(tolower(team_division)),
  games = uniqueN(game_id),
  points_for = sum(points_for, na.rm = TRUE),
  points_against = sum(points_against, na.rm = TRUE),

  off_plays = sum(off_plays, na.rm = TRUE),
  off_epa_total = sum(off_epa_total, na.rm = TRUE),
  off_successes = sum(off_successes, na.rm = TRUE),
  off_rush_plays = sum(off_rush_plays, na.rm = TRUE),
  off_rush_epa_total = sum(off_rush_epa_total, na.rm = TRUE),
  off_rush_successes = sum(off_rush_successes, na.rm = TRUE),
  off_pass_plays = sum(off_pass_plays, na.rm = TRUE),
  off_pass_epa_total = sum(off_pass_epa_total, na.rm = TRUE),
  off_pass_successes = sum(off_pass_successes, na.rm = TRUE),

  def_plays = sum(def_plays, na.rm = TRUE),
  def_epa_allowed_total = sum(def_epa_allowed_total, na.rm = TRUE),
  def_successes_allowed = sum(def_successes_allowed, na.rm = TRUE),
  def_rush_plays = sum(def_rush_plays, na.rm = TRUE),
  def_rush_epa_allowed_total = sum(def_rush_epa_allowed_total, na.rm = TRUE),
  def_rush_successes_allowed = sum(def_rush_successes_allowed, na.rm = TRUE),
  def_pass_plays = sum(def_pass_plays, na.rm = TRUE),
  def_pass_epa_allowed_total = sum(def_pass_epa_allowed_total, na.rm = TRUE),
  def_pass_successes_allowed = sum(def_pass_successes_allowed, na.rm = TRUE)
), by = .(season, team)]

team_season[, `:=`(
  points_per_game = safe_divide(points_for, games),
  points_allowed_per_game = safe_divide(points_against, games),

  off_epa_per_play = safe_divide(off_epa_total, off_plays),
  off_epa_per_rush = safe_divide(off_rush_epa_total, off_rush_plays),
  off_epa_per_pass = safe_divide(off_pass_epa_total, off_pass_plays),
  off_success_rate = safe_divide(off_successes, off_plays),
  off_rush_success_rate = safe_divide(off_rush_successes, off_rush_plays),
  off_pass_success_rate = safe_divide(off_pass_successes, off_pass_plays),

  def_epa_allowed_per_play = safe_divide(def_epa_allowed_total, def_plays),
  def_epa_allowed_per_rush = safe_divide(def_rush_epa_allowed_total, def_rush_plays),
  def_epa_allowed_per_pass = safe_divide(def_pass_epa_allowed_total, def_pass_plays),
  def_success_rate_allowed = safe_divide(def_successes_allowed, def_plays),
  def_rush_success_rate_allowed = safe_divide(def_rush_successes_allowed, def_rush_plays),
  def_pass_success_rate_allowed = safe_divide(def_pass_successes_allowed, def_pass_plays)
)]

setorder(team_season, season, team)

# -----------------------------------------------------------------------------
# 5. FBS league averages and rankings
# -----------------------------------------------------------------------------

message("Building FBS league averages ...")
fbs_team_season <- team_season[tolower(division) == "fbs"]
if (nrow(fbs_team_season) == 0L) {
  stop("No FBS team-season rows were identified. Check games division fields.")
}

league_season <- fbs_team_season[, .(
  teams = uniqueN(team),
  games = sum(games, na.rm = TRUE),

  off_plays = sum(off_plays, na.rm = TRUE),
  off_epa_total = sum(off_epa_total, na.rm = TRUE),
  off_rush_plays = sum(off_rush_plays, na.rm = TRUE),
  off_rush_epa_total = sum(off_rush_epa_total, na.rm = TRUE),
  off_pass_plays = sum(off_pass_plays, na.rm = TRUE),
  off_pass_epa_total = sum(off_pass_epa_total, na.rm = TRUE),
  off_successes = sum(off_successes, na.rm = TRUE),
  off_rush_successes = sum(off_rush_successes, na.rm = TRUE),
  off_pass_successes = sum(off_pass_successes, na.rm = TRUE),

  def_plays = sum(def_plays, na.rm = TRUE),
  def_epa_allowed_total = sum(def_epa_allowed_total, na.rm = TRUE),
  def_rush_plays = sum(def_rush_plays, na.rm = TRUE),
  def_rush_epa_allowed_total = sum(def_rush_epa_allowed_total, na.rm = TRUE),
  def_pass_plays = sum(def_pass_plays, na.rm = TRUE),
  def_pass_epa_allowed_total = sum(def_pass_epa_allowed_total, na.rm = TRUE),
  def_successes_allowed = sum(def_successes_allowed, na.rm = TRUE),
  def_rush_successes_allowed = sum(def_rush_successes_allowed, na.rm = TRUE),
  def_pass_successes_allowed = sum(def_pass_successes_allowed, na.rm = TRUE)
), by = season]

league_season[, `:=`(
  off_epa_per_play = safe_divide(off_epa_total, off_plays),
  off_epa_per_rush = safe_divide(off_rush_epa_total, off_rush_plays),
  off_epa_per_pass = safe_divide(off_pass_epa_total, off_pass_plays),
  off_success_rate = safe_divide(off_successes, off_plays),
  off_rush_success_rate = safe_divide(off_rush_successes, off_rush_plays),
  off_pass_success_rate = safe_divide(off_pass_successes, off_pass_plays),
  def_epa_allowed_per_play = safe_divide(def_epa_allowed_total, def_plays),
  def_epa_allowed_per_rush = safe_divide(def_rush_epa_allowed_total, def_rush_plays),
  def_epa_allowed_per_pass = safe_divide(def_pass_epa_allowed_total, def_pass_plays),
  def_success_rate_allowed = safe_divide(def_successes_allowed, def_plays),
  def_rush_success_rate_allowed = safe_divide(def_rush_successes_allowed, def_rush_plays),
  def_pass_success_rate_allowed = safe_divide(def_pass_successes_allowed, def_pass_plays)
)]
setorder(league_season, season)

message("Building long-format rankings ...")
metric_config <- data.table(
  metric = c(
    "off_epa_per_play", "off_epa_per_rush", "off_epa_per_pass",
    "off_success_rate", "off_rush_success_rate", "off_pass_success_rate",
    "def_epa_allowed_per_play", "def_epa_allowed_per_rush", "def_epa_allowed_per_pass",
    "def_success_rate_allowed", "def_rush_success_rate_allowed", "def_pass_success_rate_allowed"
  ),
  sample_col = c(
    "off_plays", "off_rush_plays", "off_pass_plays",
    "off_plays", "off_rush_plays", "off_pass_plays",
    "def_plays", "def_rush_plays", "def_pass_plays",
    "def_plays", "def_rush_plays", "def_pass_plays"
  ),
  higher_is_better = c(
    TRUE, TRUE, TRUE, TRUE, TRUE, TRUE,
    FALSE, FALSE, FALSE, FALSE, FALSE, FALSE
  ),
  minimum_sample = c(
    100L, 50L, 50L, 100L, 50L, 50L,
    100L, 50L, 50L, 100L, 50L, 50L
  )
)

ranking_parts <- vector("list", nrow(metric_config))
for (i in seq_len(nrow(metric_config))) {
  cfg <- metric_config[i]
  metric_name <- cfg$metric
  sample_name <- cfg$sample_col

  part <- fbs_team_season[, .(
    season,
    team,
    conference,
    division,
    metric = metric_name,
    value = get(metric_name),
    sample_size = as.integer(get(sample_name)),
    minimum_sample = cfg$minimum_sample,
    higher_is_better = cfg$higher_is_better
  )]

  league_lookup <- league_season[, .(
    season,
    league_average = get(metric_name)
  )]
  part <- merge(part, league_lookup, by = "season", all.x = TRUE)
  part[, difference_from_average := value - league_average]
  part[, qualifies := !is.na(value) & sample_size >= minimum_sample]

  if (isTRUE(cfg$higher_is_better)) {
    part[qualifies == TRUE, rank := frank(-value, ties.method = "min"), by = season]
  } else {
    part[qualifies == TRUE, rank := frank(value, ties.method = "min"), by = season]
  }

  part[, teams_ranked := sum(qualifies), by = season]
  part[qualifies == TRUE, percentile :=
         fifelse(teams_ranked <= 1L, 100,
                 100 * (teams_ranked - rank) / (teams_ranked - 1L))]

  ranking_parts[[i]] <- part
}

team_rankings <- rbindlist(ranking_parts, use.names = TRUE)
setorder(team_rankings, season, metric, rank, team, na.last = TRUE)

# Add commonly requested ranks directly to the wide team-season table.
wide_rank_metrics <- metric_config$metric
for (metric_name in wide_rank_metrics) {
  metric_rows <- team_rankings[
    metric == metric_name,
    .(season, team, rank_value = rank, percentile_value = percentile)
  ]
  setnames(
    metric_rows,
    c("rank_value", "percentile_value"),
    c(paste0(metric_name, "_rank"), paste0(metric_name, "_percentile"))
  )
  team_season <- merge(team_season, metric_rows, by = c("season", "team"), all.x = TRUE)
}
setorder(team_season, season, team)

# -----------------------------------------------------------------------------
# 6. Validate and write outputs
# -----------------------------------------------------------------------------

message("\nValidating outputs ...")
required_team_season <- c(
  "season", "team", "division", "games",
  "off_epa_per_play", "off_epa_per_rush", "off_epa_per_pass",
  "def_epa_allowed_per_play", "def_epa_allowed_per_rush", "def_epa_allowed_per_pass"
)
require_columns(team_season, required_team_season, "Team-season output")

if (nrow(team_game) < 1000L) stop("Team-game output unexpectedly has fewer than 1,000 rows.")
if (nrow(team_season) < 500L) stop("Team-season output unexpectedly has fewer than 500 rows.")
if (nrow(team_rankings) < 1000L) stop("Rankings output unexpectedly has fewer than 1,000 rows.")

message("  Team-game rows:   ", format(nrow(team_game), big.mark = ","))
message("  Team-season rows: ", format(nrow(team_season), big.mark = ","))
message("  League rows:      ", format(nrow(league_season), big.mark = ","))
message("  Ranking rows:     ", format(nrow(team_rankings), big.mark = ","))

message("\nExample: Alabama offensive EPA/rush")
print(
  team_season[
    tolower(team) == "alabama",
    .(
      season,
      team,
      games,
      off_rush_plays,
      off_epa_per_rush,
      off_epa_per_rush_rank,
      off_epa_per_rush_percentile
    )
  ][order(-season)][1:min(.N, 5L)]
)

message("\nWriting processed files ...")
write_output(team_game, "historical_team_game_stats")
write_output(team_season, "historical_team_season_stats")
write_output(league_season, "historical_league_season_stats")
write_output(team_rankings, "historical_team_rankings")

metadata <- list(
  generated_at_utc = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
  min_season = MIN_SEASON,
  max_season = MAX_SEASON,
  definitions = list(
    play_universe = "Plays with non-missing EPA, an identified offense and defense, no accepted no-play flag, and either a rush or pass indicator.",
    pass_definition = "Pass flag, pass-attempt flag, or sack flag when available.",
    epa_per_rush = "Total EPA on qualifying rush plays divided by qualifying rush plays.",
    league_average = "Play-weighted FBS average, not an unweighted average of team averages.",
    offensive_ranking = "Higher is better.",
    defensive_epa_allowed_ranking = "Lower is better.",
    minimum_samples = as.list(setNames(metric_config$minimum_sample, metric_config$metric))
  ),
  source_columns = as.list(resolved),
  row_counts = list(
    team_game = nrow(team_game),
    team_season = nrow(team_season),
    league_season = nrow(league_season),
    team_rankings = nrow(team_rankings)
  ),
  files = list(
    team_game = "historical_team_game_stats.parquet",
    team_season = "historical_team_season_stats.parquet",
    league_season = "historical_league_season_stats.parquet",
    team_rankings = "historical_team_rankings.parquet"
  )
)

write_json(
  metadata,
  file.path(OUTPUT_DIR, "historical_data_metadata.json"),
  pretty = TRUE,
  auto_unbox = TRUE,
  na = "null"
)

message("\nHistorical CFB statistics build completed successfully.")
