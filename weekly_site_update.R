# =============================================================================
# weekly_site_update.R -- BTB Analytics public data page + newsletter feed.
#
# Standalone weekly job (GitHub Actions friendly). Independent of the canonical
# betting pipeline (2026_cfb_update.R) but reuses its definitions verbatim:
#   * wEPA        = EPA * prod(1 + weight_k * situation_tag_k), weights from
#                   models/final_wepa_weights_6_2_24.RDS (same tagging block).
#   * Eckel drive = drive starting outside the opp 40 that either scores a TD
#                   (new_drive_pts >= 6) or records a 1st down inside the 40.
#   * pass/rush EPA = mean CFBD ppa on pass==1 / rush==1 plays (same filters).
#
# POWER RATING (new): preseason prior blended with in-season opponent-adjusted
# wEPA. The in-season component is a mixed model on scrimmage plays:
#     wepa ~ off_home + (1 | offense) + (1 | defense)
# Offense/defense BLUPs are shrunken, opponent- and venue-adjusted per-play
# ratings; scaled to points/game and blended with the preseason prior using a
# weight that decays with games played (prior fully out after 8 games by default).
# Conference strength is NOT hand-adjusted: it is identified by the schedule
# graph (opponent adjustment) plus the prior early on, which is the correct
# mechanism and avoids double counting.
# Only TARGET_SEASON play-by-play enters the in-season component; no prior-season
# play-by-play is loaded. Week 0 is the preseason baseline.
#
# RUN:
#   Rscript weekly_site_update.R --year=2026
#   Rscript weekly_site_update.R --year=2025            # full-season backtest
#   Rscript weekly_site_update.R --year=2026 --max-week=6
#
# REQUIRES:
#   * env var CFBD_API_KEY (GitHub Actions: repository secret).
#   * data/preseason_ratings_<year>.csv with columns (REQUIRED for Week 0 + power rating):
#       team, power_pts [, off_pts, def_pts]   (export from preseason system)
#   * models/final_wepa_weights_6_2_24.RDS  (optional; falls back to raw EPA
#     with garbage-time plays excluded from the rating model, loudly).
#   * models/eckel_mod.RDS                  (optional; enables *_eckel_rate_oe).
#
# OUTPUTS (under output/site/<year>/):
#   ratings_history.csv          one row per team-week, all metrics + ranks
#   weekly/week_XX.csv           per-week snapshot
#   latest.csv / latest.json     current standings for the site table
#   meta.json                    run metadata, data freshness, column notes
# =============================================================================

# ---- Libraries --------------------------------------------------------------
# cfbfastR is only needed for live data pulls; install lazily in the main run
# so the functions can be sourced (e.g., by test_synthetic.R) without network.
ensure_cfbfastR <- function() {
  if (requireNamespace("cfbfastR", quietly = TRUE)) return(invisible(TRUE))
  # CRAN first; fall back to GitHub (matches canonical pipeline environment).
  tryCatch(install.packages("cfbfastR", repos = "https://cloud.r-project.org"),
           error = function(e) invisible(NULL))
  if (!requireNamespace("cfbfastR", quietly = TRUE)) {
    if (!requireNamespace("remotes", quietly = TRUE)) {
      install.packages("remotes", repos = "https://cloud.r-project.org")
    }
    remotes::install_github("sportsdataverse/cfbfastR")
  }
  invisible(TRUE)
}
suppressMessages({
  library(dplyr); library(tidyr); library(purrr); library(readr)
  library(stringr); library(tibble); library(jsonlite); library(lme4)
  library(lubridate)
})
options(dplyr.summarise.inform = FALSE)

# ---- Config (edit here only) ------------------------------------------------
.args <- commandArgs(trailingOnly = TRUE)
.get_arg <- function(flag, default) {
  hit <- grep(paste0("^", flag, "="), .args, value = TRUE)
  if (length(hit)) sub(paste0("^", flag, "="), "", hit[1]) else default
}
TARGET_SEASON  <- as.integer(.get_arg("--year", format(Sys.Date(), "%Y")))
MAX_WEEK_ARG   <- suppressWarnings(as.integer(.get_arg("--max-week", NA)))
OUT_ROOT       <- .get_arg("--outdir", "output/site")

MODEL_WEIGHTS_FILE <- "models/final_wepa_weights_6_2_24.RDS"
ECKEL_MODEL_FILE   <- "models/eckel_mod.RDS"
PRESEASON_FILE     <- sprintf("data/preseason_ratings_%d.csv", TARGET_SEASON)

PLAYS_SCALE      <- 65    # scrimmage plays/side/game: epa-per-play -> pts/game
TARGET_SD        <- 12    # points SD the standardized prior is scaled to
STANDARDIZE_PRIOR <- TRUE # z-score the prior file onto the TARGET_SD scale
PRIOR_G_FULL     <- 8     # prior weight hits 0 once a team has played this many
PRIOR_POW        <- 1.5   # decay shape: w = max(0, 1 - g/G_FULL)^POW
RECENCY_DECAY    <- 0.90  # per-week play weight in the RATING model only:
                          # a play from k weeks ago counts DECAY^k. Season
                          # stats (wEPA, EPA splits, Eckel) stay unweighted.
NEW_TEAM_PRIOR_Q <- 0.10  # prior percentile assigned to teams missing from file

# Garbage-time thresholds (canonical pipeline definition). Used to EXCLUDE
# plays from the rating model only when the wEPA weights file is absent
# (the trained weights already handle garbage time when present).
GT_THRESH <- c(`1` = 28, `2` = 24, `3` = 21, `4` = 16)

# Team-name recodes applied to the PRESEASON file to match cfbfastR names.
# Extend as needed; unmatched teams are reported loudly every run.
PRIOR_NAME_RECODE <- c(
  # Preseason-file name -> CFBD school name. Directions verified against the
  # canonical pipeline's active name normalization (2026_cfb_update.R ~l.282),
  # whose targets are the cfbd_team_info school keys.
  "Louisiana Monroe"     = "UL Monroe",
  "Southern Mississippi" = "Southern Miss",
  "Sam Houston State"    = "Sam Houston",
  "UMass"                = "Massachusetts",
  "UT San Antonio"       = "UTSA",
  # Live /teams/fbs check: CFBD uses "UConn", the preseason file "Connecticut".
  "Connecticut"          = "UConn",
  # Insurance for accent-stripped exports; inert while the file carries the e-acute.
  "San Jose State"       = "San Jos\u00e9 State",
  "Hawaii"               = "Hawai'i"
)

# ---- Guardrails -------------------------------------------------------------
require_cols <- function(df, cols, where) {
  miss <- setdiff(cols, names(df))
  if (length(miss)) {
    stop(sprintf(
      "[%s] missing columns (upstream API/schema drift?): %s\nColumns present: %s",
      where, paste(miss, collapse = ", "),
      paste(sort(names(df)), collapse = ", ")
    ), call. = FALSE)
  }
  invisible(df)
}

msg <- function(...) message(sprintf(...))

# ---- Data acquisition -------------------------------------------------------
fetch_teams <- function(season) {
  t <- cfbfastR::cfbd_team_info(only_fbs = TRUE, year = season)
  require_cols(t, c("school", "conference"), "cfbd_team_info")
  t %>% transmute(team = school, conference)
}

fetch_games <- function(season) {
  g <- cfbfastR::cfbd_game_info(season, season_type = "regular")
  require_cols(g, c("game_id", "week", "home_team", "away_team",
                    "home_points", "away_points", "neutral_site"),
               "cfbd_game_info")
  g %>%
    mutate(completed = !is.na(home_points) & !is.na(away_points)) %>%
    select(game_id, week, home_team, away_team, home_points, away_points,
           neutral_site, completed)
}

fetch_pbp <- function(season) {
  msg("Downloading play-by-play for %d (~100+ MB; silent and can take several minutes on slow connections)...", season)
  # cfbfastR streams readRDS over url(), which obeys options(timeout); the
  # 60s default kills large downloads on slow links and returns an EMPTY table.
  old_to <- options(timeout = max(1800, getOption("timeout")))
  on.exit(options(old_to), add = TRUE)
  pbp <- tryCatch(
    cfbfastR::load_cfb_pbp(season),
    error = function(e) {
      msg("load_cfb_pbp(%d) unavailable (%s) -- treating as preseason: no PBP.",
          season, conditionMessage(e))
      NULL
    }
  )
  if (is.null(pbp) || nrow(pbp) == 0) return(NULL)
  pbp <- pbp %>%
    distinct(game_id, id_play, .keep_all = TRUE) %>%
    mutate(season = if ("season" %in% names(.)) coalesce(season, year) else year)
  require_cols(pbp, c(
    # tagging block inputs (identical to canonical pipeline)
    "pass", "rush", "play_type", "penalty_detail", "distance", "position_rush",
    "sack", "fumble_vec", "down", "EPA", "wp_before", "wp_after",
    "yards_to_goal", "Goal_To_Go", "score_diff", "period", "pos_team",
    "home", "away",
    # stats / eckel inputs
    "ppa", "offense_play", "defense_play", "game_id", "drive_id", "id_play",
    "week", "new_drive_pts", "TimeSecsRem", "adj_TimeSecsRem",
    "drive_end_yards_to_goal", "pos_team_timeouts", "def_pos_team_timeouts",
    "drive_result", "def_pos_team"
  ), "load_cfb_pbp")
  msg("Play-by-play loaded: %d plays, weeks %d-%d.",
      nrow(pbp), min(pbp$week, na.rm = TRUE), max(pbp$week, na.rm = TRUE))
  pbp
}

# ---- wEPA: situation tagging + learned weights (canonical definitions) ------
tag_wepa_features <- function(pbp) {
  tagged <- pbp %>%
    mutate(
      passing_epa = ifelse(pass == 1, 1, 0),
      passing_ex_int_epa = ifelse(pass == 1 & !(grepl("Interception", play_type)), 1, 0),
      rush_epa = ifelse(rush == 1, 1, 0),
      pos_rush_epa = ifelse(rush == 1 & EPA > 0, 1, 0),
      neg_rush_epa = ifelse(rush == 1 & EPA <= 0, 1, 0),
      short_yd_rush_epa = ifelse(rush == 1 & distance < 5, 1, 0),
      long_yd_rush_epa = ifelse(rush == 1 & distance >= 5, 1, 0),
      qb_rush_epa = ifelse(rush == 1 & position_rush != "QB", 1, 0),
      completed_passing_epa = ifelse(play_type %in% c("Pass Reception", "Passing Touchdown"), 1, 0),
      incompleted_passing_epa = ifelse(play_type == "Pass Incompletion", 1, 0),
      off_holding_epa = ifelse(penalty_detail == "Offensive Holding", 1, 0),
      defensive_pi_epa = ifelse(penalty_detail == "Pass Interference" & EPA > 0, 1, 0),
      false_start_epa = ifelse(penalty_detail == "False Start", 1, 0),
      roughing_epa = ifelse(penalty_detail == "Roughing the Passer", 1, 0),
      defensive_holding_epa = ifelse(penalty_detail == "Defensive Holding", 1, 0),
      offensive_unnecessary_roughness_epa = ifelse(penalty_detail %in% c("Unnecessary Roughness") & EPA < 0, 1, 0),
      offensive_unsportsmanlike_epa = ifelse(penalty_detail %in% c("Unsportsmanlike Conduct") & EPA < 0, 1, 0),
      defensive_unnecessary_roughness_epa = ifelse(penalty_detail %in% c("Unnecessary Roughness") & EPA >= 0, 1, 0),
      defensive_unsportsmanlike_epa = ifelse(penalty_detail %in% c("Unsportsmanlike Conduct") & EPA >= 0, 1, 0),
      offensive_pi_epa = ifelse(penalty_detail == "Pass Interference" & EPA <= 0, 1, 0),
      off_hold_or_false_start_epa = ifelse(penalty_detail %in% c("Offensive Holding", "False Start"), 1, 0),
      sack_epa = ifelse(sack == 1, 1, 0),
      fumble_epa = ifelse(fumble_vec == 1, 1, 0),
      sack_fumble_epa = ifelse(sack == 1 & fumble_vec == 1, 1, 0),
      non_sack_fumble_epa = ifelse(sack == 0 & fumble_vec == 1, 1, 0),
      non_fumble_sack_epa = ifelse(sack == 1 & fumble_vec == 0, 1, 0),
      int_epa = ifelse(pass == 1 & (grepl("Interception", play_type)), 1, 0),
      return_td_epa = ifelse((grepl("Return", play_type) | grepl("Recovery", play_type)) & grepl("Touchdown", play_type), 1, 0),
      punt_epa = ifelse(grepl("Punt", play_type), 1, 0),
      blocked_punt_epa = ifelse(grepl("Blocked Punt", play_type), 1, 0),
      fg_epa = ifelse(grepl("Field Goal", play_type), 1, 0),
      kickoff_epa = ifelse(grepl("Kickoff", play_type), 1, 0),
      first_down_epa = ifelse(down == 1, 1, 0),
      second_down_epa = ifelse(down == 2, 1, 0),
      third_down_epa = ifelse(down == 3, 1, 0),
      fourth_down_epa = ifelse((rush == 1 | pass == 1) & down == 4, 1, 0),
      third_down_ex_sack_int_epa = ifelse(down == 3 & !(grepl("Interception", play_type)) & !(grepl("Sack", play_type)), 1, 0),
      third_down_pos_epa = ifelse(down == 3 & EPA > 0, 1, 0),
      third_down_long_ex_sack_int_epa = ifelse(down == 3 & distance > 5 & !(grepl("Interception", play_type)) & !(grepl("Sack", play_type)), 1, 0),
      first_down_rush_epa = ifelse(down == 1 & rush == 1, 1, 0),
      second_down_rush_epa = ifelse(down == 2 & rush == 1, 1, 0),
      third_down_rush_epa = ifelse(down == 3 & rush == 1, 1, 0),
      fourth_down_rush_epa = ifelse(down == 4 & rush == 1, 1, 0),
      first_down_pass_epa = ifelse(down == 1 & pass == 1, 1, 0),
      second_down_pass_epa = ifelse(down == 2 & pass == 1, 1, 0),
      third_down_pass_epa = ifelse(down == 3 & pass == 1, 1, 0),
      fourth_down_pass_epa = ifelse(down == 4 & pass == 1, 1, 0),
      neutral_second_down_rush_epa = ifelse(down == 2 & rush == 1 & wp_before > 0.05 & wp_after < 0.95, 1, 0),
      early_down_rush_epa = ifelse(down <= 2, 1, 0),
      early_down_sack_epa = ifelse(down <= 2 & sack == 1 & fumble_vec == 0, 1, 0),
      red_zone_epa = ifelse(yards_to_goal <= 20, 1, 0),
      goal_to_go_epa = ifelse(Goal_To_Go == TRUE, 1, 0),
      goalline_epa = ifelse(yards_to_goal <= 3, 1, 0),
      plus_territory_epa = ifelse(yards_to_goal <= 50, 1, 0),
      low_wp_epa = ifelse((wp_before <= 0.05 | wp_before >= 0.95), 1, 0),
      garbage_time_epa = ifelse(
        (score_diff >= GT_THRESH["1"] & period == 1) |
          (score_diff >= GT_THRESH["2"] & period == 2) |
          (score_diff >= GT_THRESH["3"] & period == 3) |
          (score_diff >= GT_THRESH["4"] & period == 4), 1, 0),
      asym_low_wp_epa = ifelse((wp_before <= 0.2 | wp_before >= 0.95), 1, 0),
      asym_garbage_time_epa = ifelse((wp_before <= 0.2 | wp_before >= 0.95) & period == 4, 1, 0),
      offense_home_epa = ifelse(pos_team == home, 1, 0),
      offense_away_epa = ifelse(pos_team == away, 1, 0)
    ) %>%
    rename_with(~ paste0(str_remove(.x, "_epa"), "_weight"),
                ends_with("_epa", ignore.case = FALSE)) %>%
    mutate(across(ends_with("_weight"), ~.x, .names = "def_{.col}")) %>%
    rename_with(~ paste0("off_", .x),
                (ends_with("_weight", ignore.case = FALSE) &
                   !starts_with("def_", ignore.case = FALSE))) %>%
    select(ends_with("_weight"))
  tagged
}

apply_wepa_weights <- function(tagged, epa_vec, model_weights) {
  if (is.null(model_weights)) {
    # Fallback: zero weights == multiplier of 1 everywhere -> wepa is raw EPA.
    return(tibble(off_wepa = epa_vec, def_wepa = epa_vec))
  }
  # Canonical code relies on positional alignment between the tagged columns
  # and the weights list. Enforce name alignment when names are available;
  # otherwise require identical length and warn about the order assumption.
  if (!is.null(names(model_weights)) && all(nzchar(names(model_weights)))) {
    missing_w <- setdiff(names(tagged), names(model_weights))
    if (length(missing_w)) {
      stop("wEPA weights file lacks entries for: ",
           paste(missing_w, collapse = ", "), call. = FALSE)
    }
    model_weights <- model_weights[names(tagged)]
  } else if (length(model_weights) != ncol(tagged)) {
    stop(sprintf("wEPA weights length (%d) != tagged feature count (%d).",
                 length(model_weights), ncol(tagged)), call. = FALSE)
  } else {
    warning("wEPA weights are unnamed; relying on canonical column order.")
  }
  tagged %>%
    map2(model_weights, `*`) %>%
    bind_cols() %>%
    mutate(
      across(ends_with("_weight"), ~ as.numeric(replace_na(.x, 0) + 1)),
      epa = epa_vec,
      off_wepa = pmap_dbl(pick(starts_with("off_"), epa), prod),
      def_wepa = pmap_dbl(pick(starts_with("def_"), epa), prod)
    ) %>%
    select(off_wepa, def_wepa)
}

# ---- Eckel drives (canonical definition) ------------------------------------
build_drives <- function(pbp, eckel_model = NULL) {
  drives <- pbp %>%
    arrange(game_id, drive_id, id_play) %>%
    mutate(eck_ind_no_td = ifelse(down == 1 & yards_to_goal < 40, 1, 0)) %>%
    group_by(game_id, week, drive_id, pos_team, def_pos_team) %>%
    summarise(
      across(c(new_drive_pts, TimeSecsRem, adj_TimeSecsRem, yards_to_goal,
               drive_end_yards_to_goal, pos_team_timeouts,
               def_pos_team_timeouts, drive_result, period),
             ~ .[1]),
      eck_ind_no_td = ifelse(sum(eck_ind_no_td, na.rm = TRUE) > 0, 1, 0),
      .groups = "drop"
    ) %>%
    rename(start_yards_to_goal = yards_to_goal,
           half_secs_rem = TimeSecsRem,
           game_secs_rem = adj_TimeSecsRem,
           drive_start_period = period) %>%
    filter(start_yards_to_goal > 40,
           !is.na(pos_team_timeouts), !is.na(def_pos_team_timeouts)) %>%
    # Deviation from canonical: lag within game_id (canonical lags across the
    # whole frame, so a game's first drive picks up the previous game's last).
    group_by(game_id) %>%
    mutate(prev_drive_result = lag(drive_result)) %>%
    ungroup() %>%
    mutate(eckel = case_when(new_drive_pts >= 6 ~ 1,
                             eck_ind_no_td == 1 ~ 1,
                             TRUE ~ 0))
  if (!is.null(eckel_model)) {
    drives <- drives %>%
      mutate(
        eckel_prediction = suppressWarnings(
          predict.glm(eckel_model, ., type = "response")),
        eckel_oe = eckel - eckel_prediction
      )
  } else {
    drives <- drives %>% mutate(eckel_prediction = NA_real_, eckel_oe = NA_real_)
  }
  drives
}

# ---- Through-week team stats (canonical filters, vectorized) ----------------
team_week_stats <- function(pbp_aug, drives, fbs_teams, thru_week) {
  p <- pbp_aug %>% filter(week <= thru_week, !is.na(ppa))
  d <- drives  %>% filter(week <= thru_week)

  off <- p %>%
    filter(offense_play %in% fbs_teams) %>%
    group_by(team = offense_play) %>%
    summarise(
      off_wepa     = mean(off_wepa, na.rm = TRUE),
      off_pass_epa = mean(ppa[pass == 1], na.rm = TRUE),
      off_rush_epa = mean(ppa[rush == 1], na.rm = TRUE),
      .groups = "drop"
    )
  def <- p %>%
    filter(defense_play %in% fbs_teams) %>%
    group_by(team = defense_play) %>%
    summarise(
      def_wepa     = mean(def_wepa, na.rm = TRUE),
      def_pass_epa = mean(ppa[pass == 1], na.rm = TRUE),
      def_rush_epa = mean(ppa[rush == 1], na.rm = TRUE),
      .groups = "drop"
    )
  eck_off <- d %>%
    filter(pos_team %in% fbs_teams) %>%
    group_by(team = pos_team) %>%
    summarise(off_eckel_rate    = mean(eckel, na.rm = TRUE),
              off_eckel_rate_oe = mean(eckel_oe, na.rm = TRUE),
              .groups = "drop")
  eck_def <- d %>%
    filter(def_pos_team %in% fbs_teams) %>%
    group_by(team = def_pos_team) %>%
    summarise(def_eckel_rate    = mean(eckel, na.rm = TRUE),
              def_eckel_rate_oe = mean(eckel_oe, na.rm = TRUE),
              .groups = "drop")

  reduce(list(off, def, eck_off, eck_def), full_join, by = "team")
}

# ---- Opponent adjustment: mixed model on scrimmage plays --------------------
fit_adjustment <- function(model_df, label) {
  # model_df: offense, defense (FCS pooled to "NON_FBS"), off_home in
  # {-1, 0, 1}, response y (wepa), wt = recency weight. Returns per-team
  # off/def epa-per-play BLUPs (shrunken, opponent- and venue-adjusted).
  fit <- tryCatch(
    lmer(y ~ off_home + (1 | offense) + (1 | defense),
         data = model_df, weights = wt, REML = TRUE,
         control = lmerControl(check.conv.singular = "ignore",
                               calc.derivs = FALSE)),
    error = function(e) {
      msg("[%s] lmer failed (%s); falling back to raw centered means.",
          label, conditionMessage(e))
      NULL
    }
  )
  if (is.null(fit)) {
    off <- model_df %>% group_by(offense) %>%
      summarise(v = weighted.mean(y, wt, na.rm = TRUE), .groups = "drop") %>%
      mutate(v = v - mean(v, na.rm = TRUE))
    def <- model_df %>% group_by(defense) %>%
      summarise(v = weighted.mean(y, wt, na.rm = TRUE), .groups = "drop") %>%
      mutate(v = v - mean(v, na.rm = TRUE))
    return(list(
      off = off %>% transmute(team = offense, off_adj = v),
      def = def %>% transmute(team = defense, def_adj = v),
      hfa = NA_real_
    ))
  }
  re <- ranef(fit)
  list(
    off = tibble(team = rownames(re$offense), off_adj = re$offense[[1]]),
    def = tibble(team = rownames(re$defense), def_adj = re$defense[[1]]),
    hfa = tryCatch(unname(fixef(fit)["off_home"]), error = function(e) NA_real_)
  )
}

# ---- Preseason prior --------------------------------------------------------
load_prior <- function(path, teams_tbl) {
  fbs <- teams_tbl$team
  if (!file.exists(path)) {
    stop(sprintf(paste0(
      "Required preseason prior file is missing: %s. ",
      "Week 0 must be the preseason baseline, and Weeks 1+ blend that baseline ",
      "with current-season data. Create/commit the file before publishing."), path),
      call. = FALSE)
  }
  raw <- read_csv(path, show_col_types = FALSE)
  team_col  <- intersect(c("team", "school", "Team", "TEAM"), names(raw))[1]
  power_col <- intersect(c("power_pts", "power", "power_rating", "rating",
                           "btb_power", "preseason_power"), names(raw))[1]
  if (is.na(team_col) || is.na(power_col)) {
    stop("Preseason file needs a team column and a power column. Found: ",
         paste(names(raw), collapse = ", "), call. = FALSE)
  }
  off_col <- intersect(c("off_pts", "off", "off_rating"), names(raw))[1]
  def_col <- intersect(c("def_pts", "def", "def_rating"), names(raw))[1]

  pri <- raw %>%
    transmute(
      team  = recode(.data[[team_col]], !!!PRIOR_NAME_RECODE),
      power = as.numeric(.data[[power_col]]),
      off   = if (!is.na(off_col)) as.numeric(.data[[off_col]]) else NA_real_,
      def   = if (!is.na(def_col)) as.numeric(.data[[def_col]]) else NA_real_
    )

  unmatched <- setdiff(pri$team, fbs)
  if (length(unmatched)) {
    msg("PRESEASON TEAMS NOT MATCHED TO CFBD NAMES (add to PRIOR_NAME_RECODE): %s",
        paste(unmatched, collapse = ", "))
  }
  pri <- pri %>% filter(team %in% fbs)

  # Center; optionally rescale total power to TARGET_SD; scale off/def by the
  # same factor so off + def == power is preserved.
  pri <- pri %>% mutate(power = power - mean(power, na.rm = TRUE))
  scl <- 1
  if (isTRUE(STANDARDIZE_PRIOR)) {
    s <- sd(pri$power, na.rm = TRUE)
    if (is.finite(s) && s > 0) scl <- TARGET_SD / s
  }
  pri <- pri %>%
    mutate(
      power = power * scl,
      off = if (all(is.na(off))) power / 2 else (off - mean(off, na.rm = TRUE)) * scl,
      def = if (all(is.na(def))) power / 2 else (def - mean(def, na.rm = TRUE)) * scl,
      power = off + def  # enforce identity after any rescaling
    )
  if (is.na(off_col) || is.na(def_col)) {
    msg("Preseason file has no off/def split; splitting the prior 50/50. Export off_pts/def_pts from the preseason system for better early-season off/def ranks.")
  }

  missing <- setdiff(fbs, pri$team)
  if (length(missing)) {
    fill <- quantile(pri$power, NEW_TEAM_PRIOR_Q, na.rm = TRUE, names = FALSE)
    msg("Teams missing from preseason file get the %d%%ile prior (%.1f): %s",
        round(100 * NEW_TEAM_PRIOR_Q), fill, paste(missing, collapse = ", "))
    pri <- bind_rows(pri, tibble(team = missing, power = fill,
                                 off = fill / 2, def = fill / 2))
  }
  pri %>% transmute(team, prior_power = power, prior_off = off, prior_def = def)
}

make_week0_snapshot <- function(teams_tbl, prior) {
  teams_tbl %>%
    left_join(prior, by = "team") %>%
    transmute(
      season = TARGET_SEASON,
      week = 0L,
      team,
      conference,
      games = 0L,
      prior_weight = 1,
      power_pts = prior_power,
      off_pts = prior_off,
      def_pts = prior_def,
      power_rank = min_rank(desc(power_pts)),
      off_rank = min_rank(desc(off_pts)),
      def_rank = min_rank(desc(def_pts))
    )
}

prior_weight <- function(games_played) {
  pmax(0, 1 - games_played / PRIOR_G_FULL)^PRIOR_POW
}

# ---- One through-week snapshot ---------------------------------------------
snapshot_week <- function(w, pbp_aug, drives, games, teams_tbl, prior,
                          have_weights) {
  fbs <- teams_tbl$team

  # Games played per team through week w (completed games only).
  gp <- games %>%
    filter(completed, week <= w) %>%
    select(home_team, away_team) %>%
    pivot_longer(everything(), values_to = "team") %>%
    filter(team %in% fbs) %>%
    count(team, name = "games")

  # Rating-model input: scrimmage plays through week w, FCS pooled.
  mdl <- pbp_aug %>%
    filter(week <= w, rush == 1 | pass == 1, !is.na(off_wepa)) %>%
    transmute(
      offense = ifelse(offense_play %in% fbs, offense_play, "NON_FBS"),
      defense = ifelse(defense_play %in% fbs, defense_play, "NON_FBS"),
      off_home = case_when(
        isTRUE_v(neutral_site) ~ 0,
        offense_play == home   ~ 1,
        TRUE                   ~ -1
      ),
      y = off_wepa,
      y_epa = ppa,
      wt = RECENCY_DECAY^(w - week),
      is_pass = pass == 1,
      is_rush = rush == 1,
      garbage = garbage_flag
    )
  if (!have_weights) mdl <- mdl %>% filter(!garbage)

  # wEPA units are canonical (multiplicative weights inflate scale ~5-10x with
  # the production weights file). The prior blend needs points, so convert the
  # power-model BLUPs with an SD-ratio anchor to EPA-equivalent units. The
  # rush/pass adjusted columns are fit on raw ppa directly (their canonical
  # unadjusted counterparts are ppa means), so they need no conversion.
  lam <- {
    cc <- !is.na(mdl$y) & !is.na(mdl$y_epa)
    s_w <- sd(mdl$y[cc]); s_e <- sd(mdl$y_epa[cc])
    if (is.finite(s_w) && is.finite(s_e) && s_w > 0) s_e / s_w else 1
  }

  adj_all  <- fit_adjustment(mdl, sprintf("wk%02d all", w))
  adj_rush <- fit_adjustment(
    mdl %>% filter(is_rush, !is.na(y_epa)) %>% mutate(y = y_epa),
    sprintf("wk%02d rush", w))
  adj_pass <- fit_adjustment(
    mdl %>% filter(is_pass, !is.na(y_epa)) %>% mutate(y = y_epa),
    sprintf("wk%02d pass", w))

  stats <- team_week_stats(pbp_aug, drives, fbs, w)

  out <- teams_tbl %>%
    left_join(gp, by = "team") %>%
    mutate(games = coalesce(games, 0L)) %>%
    left_join(adj_all$off,  by = "team") %>%
    left_join(adj_all$def,  by = "team") %>%
    left_join(adj_rush$off %>% rename(adj_off_rush_epa = off_adj), by = "team") %>%
    left_join(adj_rush$def %>% rename(adj_def_rush_epa = def_adj), by = "team") %>%
    left_join(adj_pass$off %>% rename(adj_off_pass_epa = off_adj), by = "team") %>%
    left_join(adj_pass$def %>% rename(adj_def_pass_epa = def_adj), by = "team") %>%
    left_join(prior, by = "team") %>%
    left_join(stats, by = "team") %>%
    mutate(
      data_off_pts = coalesce(off_adj, 0) * lam * PLAYS_SCALE,
      data_def_pts = -coalesce(def_adj, 0) * lam * PLAYS_SCALE,  # points prevented
      prior_weight = prior_weight(games),
      off_pts   = prior_weight * prior_off + (1 - prior_weight) * data_off_pts,
      def_pts   = prior_weight * prior_def + (1 - prior_weight) * data_def_pts,
      power_pts = off_pts + def_pts,
      season = TARGET_SEASON, week = w
    ) %>%
    mutate(
      power_rank = min_rank(desc(power_pts)),
      off_rank   = min_rank(desc(off_pts)),
      def_rank   = min_rank(desc(def_pts)),
      off_wepa_rank       = min_rank(desc(off_wepa)),
      off_pass_epa_rank   = min_rank(desc(off_pass_epa)),
      off_rush_epa_rank   = min_rank(desc(off_rush_epa)),
      off_eckel_rate_rank = min_rank(desc(off_eckel_rate)),
      def_wepa_rank       = min_rank(def_wepa),
      def_pass_epa_rank   = min_rank(def_pass_epa),
      def_rush_epa_rank   = min_rank(def_rush_epa),
      def_eckel_rate_rank = min_rank(def_eckel_rate)
    ) %>%
    select(season, week, team, conference, games, prior_weight,
           power_pts, off_pts, def_pts, power_rank, off_rank, def_rank,
           off_wepa, off_pass_epa, off_rush_epa, off_eckel_rate, off_eckel_rate_oe,
           def_wepa, def_pass_epa, def_rush_epa, def_eckel_rate, def_eckel_rate_oe,
           adj_off_rush_epa, adj_def_rush_epa, adj_off_pass_epa, adj_def_pass_epa,
           ends_with("_rank"))
  attr(out, "hfa_epa") <- adj_all$hfa * lam  # wEPA-units effect converted to EPA/play
  attr(out, "wepa_scale") <- lam
  out
}

# helper: vectorized isTRUE for possibly-NA logical
isTRUE_v <- function(x) !is.na(x) & x

# ---- Main -------------------------------------------------------------------
run_main <- !nzchar(Sys.getenv("BTB_SOURCE_ONLY"))
if (run_main) {

  if (!nzchar(Sys.getenv("CFBD_API_KEY"))) {
    stop("CFBD_API_KEY is not set. In GitHub Actions, add it as a repository secret and export it in the workflow env.", call. = FALSE)
  }
  ensure_cfbfastR()

  out_dir <- file.path(OUT_ROOT, TARGET_SEASON)
  dir.create(file.path(out_dir, "weekly"), recursive = TRUE, showWarnings = FALSE)

  teams_tbl <- fetch_teams(TARGET_SEASON)
  games     <- fetch_games(TARGET_SEASON)
  prior     <- load_prior(PRESEASON_FILE, teams_tbl)

  model_weights <- if (file.exists(MODEL_WEIGHTS_FILE)) {
    read_rds(MODEL_WEIGHTS_FILE)
  } else {
    msg("wEPA WEIGHTS FILE MISSING (%s): wEPA falls back to raw EPA and garbage-time plays are excluded from the rating model. Commit the weights RDS to restore canonical wEPA.", MODEL_WEIGHTS_FILE)
    NULL
  }
  eckel_model <- if (file.exists(ECKEL_MODEL_FILE)) {
    read_rds(ECKEL_MODEL_FILE)
  } else {
    msg("Eckel model file missing (%s): *_eckel_rate_oe will be NA (raw eckel rates still produced).", ECKEL_MODEL_FILE)
    NULL
  }

  pbp <- fetch_pbp(TARGET_SEASON)

  completed_wk <- games %>% filter(completed) %>% pull(week)
  max_completed <- if (length(completed_wk)) max(completed_wk) else 0L
  max_pbp_wk    <- if (!is.null(pbp)) max(pbp$week, na.rm = TRUE) else 0L
  thru_week <- min(max_completed, max_pbp_wk)
  if (!is.na(MAX_WEEK_ARG)) thru_week <- min(thru_week, MAX_WEEK_ARG)
  if (max_pbp_wk < max_completed) {
    msg("DATA LAG: games show completed week %d but PBP only reaches week %d. Running through week %d; rerun after cfbfastR data catches up.",
        max_completed, max_pbp_wk, thru_week)
  }

  if ((is.null(pbp) || max_pbp_wk < 1) && max_completed >= 1) {
    stop(sprintf(paste0(
      "Play-by-play came back empty while %d completed week(s) exist. This is a ",
      "data-load failure, NOT preseason; refusing to publish a prior-only ",
      "snapshot mid-season. Most common cause: the ~110MB pbp download timing ",
      "out or dropping (look for a cfbfastR warning 'Failed to readRDS' above). ",
      "Check the connection and rerun."), max_completed), call. = FALSE)
  }

  if (thru_week < 1 || is.null(pbp)) {
    # Preseason: publish the prior as week 0 so the site has content.
    msg("No completed weeks with PBP. Writing week-0 (preseason prior) snapshot.")
    snap0 <- make_week0_snapshot(teams_tbl, prior) %>%
      mutate(power_change = 0, rank_change = 0L)
    write_csv(snap0, file.path(out_dir, "weekly", "week_00.csv"))
    write_csv(snap0, file.path(out_dir, "latest.csv"))
    write_csv(snap0, file.path(out_dir, "ratings_history.csv"))
    write_json(snap0, file.path(out_dir, "latest.json"),
               dataframe = "rows", pretty = TRUE, na = "null")
    write_json(list(season = TARGET_SEASON, thru_week = 0,
                    generated_utc = format(Sys.time(), tz = "UTC"),
                    note = "preseason prior only"),
               file.path(out_dir, "meta.json"), auto_unbox = TRUE, pretty = TRUE)
    msg("Done. Preseason (week 0) outputs in %s", out_dir)
  } else {
  # quit() is forbidden here: sourced interactively (RStudio), it kills the
  # whole R session, which RStudio reports as a fatal abort.

  # Precompute per-play frames once.
  neutral_lu <- games %>% select(game_id, neutral_site)
  tagged <- tag_wepa_features(pbp)
  wepa   <- apply_wepa_weights(tagged, pbp$EPA, model_weights)
  pbp_aug <- bind_cols(pbp, wepa) %>%
    # Real load_cfb_pbp data can carry its own neutral_site; the games feed is
    # authoritative, and a duplicate would suffix both copies out of existence.
    select(-any_of("neutral_site")) %>%
    left_join(neutral_lu, by = "game_id") %>%
    mutate(garbage_flag =
             (score_diff >= GT_THRESH["1"] & period == 1) |
             (score_diff >= GT_THRESH["2"] & period == 2) |
             (score_diff >= GT_THRESH["3"] & period == 3) |
             (score_diff >= GT_THRESH["4"] & period == 4))
  drives <- build_drives(pbp, eckel_model)

  # Full consistent history: refit through every week each run so late data
  # corrections propagate. ~seconds per week; stateless and self-healing.
  week0 <- make_week0_snapshot(teams_tbl, prior)
  inseason_history <- map(seq_len(thru_week), function(w) {
    msg("Snapshot: through week %d", w)
    snapshot_week(w, pbp_aug, drives, games, teams_tbl, prior,
                  have_weights = !is.null(model_weights))
  })
  hfa_epa <- attr(inseason_history[[length(inseason_history)]], "hfa_epa")
  wepa_scale <- attr(inseason_history[[length(inseason_history)]], "wepa_scale")
  history <- bind_rows(c(list(week0), inseason_history)) %>%
    arrange(week, power_rank) %>%
    group_by(team) %>%
    arrange(week, .by_group = TRUE) %>%
    mutate(power_change = power_pts - lag(power_pts),
           rank_change  = lag(power_rank) - power_rank) %>%
    ungroup() %>%
    arrange(week, power_rank)

  write_csv(history, file.path(out_dir, "ratings_history.csv"))
  for (w in unique(history$week)) {
    write_csv(history %>% filter(week == w),
              file.path(out_dir, "weekly", sprintf("week_%02d.csv", w)))
  }
  latest <- history %>% filter(week == max(week))
  write_csv(latest, file.path(out_dir, "latest.csv"))
  write_json(latest, file.path(out_dir, "latest.json"),
             dataframe = "rows", pretty = TRUE, na = "null")

  write_json(list(
    season = TARGET_SEASON,
    thru_week = thru_week,
    generated_utc = format(Sys.time(), tz = "UTC"),
    games_max_completed_week = max_completed,
    pbp_max_week = max_pbp_wk,
    wepa_weights_loaded = !is.null(model_weights),
    eckel_model_loaded = !is.null(eckel_model),
    hfa_epa_per_play = hfa_epa,
    wepa_to_epa_scale = wepa_scale,
    n_teams = nrow(teams_tbl),
    config = list(PLAYS_SCALE = PLAYS_SCALE, TARGET_SD = TARGET_SD,
                  PRIOR_G_FULL = PRIOR_G_FULL, PRIOR_POW = PRIOR_POW),
    column_notes = list(
      power_off_def_pts = "points vs average FBS team per game; higher = better; def_pts = points prevented",
      off_stats = "per-play offense; higher = better",
      def_stats = "per-play allowed; lower = better (def_wepa, def_pass_epa, def_rush_epa, def_eckel_rate)",
      adj_rush_pass = "opponent+venue adjusted EPA/play; off higher = better, def lower = better",
      prior_weight = "share of the rating still coming from the preseason prior"
    )
  ), file.path(out_dir, "meta.json"), auto_unbox = TRUE, pretty = TRUE)

  msg("Done. Through week %d. Outputs in %s", thru_week, out_dir)
  }
}
