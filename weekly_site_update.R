# =============================================================================
# weekly_site_update.R -- BTB Analytics public data page + newsletter feed.
#
# Standalone weekly job for the public CFB metrics site.
#
# POWER RATING:
#   * Week 0 = preseason baseline.
#   * Weeks 1+ blend preseason ratings with CURRENT-SEASON performance only.
#   * The preseason prior fades completely after 8 games.
#   * The in-season rating uses opponent- and venue-adjusted wEPA.
#   * No previous-season PBP is used.
#
# RAW METRICS:
#   * Weighted EPA / Play
#   * Pass EPA / Play
#   * Rush EPA / Play
#   * Eckel Rate
#   * Opponent-adjusted pass/rush EPA
#
# Raw metrics use only data from the target season through each snapshot week.
#
# WEEKLY MOVEMENT:
#   power_change = current power rating - previous snapshot power rating
#   rank_change  = previous power rank - current power rank
#
#   rank_change > 0 = moved up
#   rank_change < 0 = moved down
#
# RUN:
#   Rscript weekly_site_update.R --year=2026
#   Rscript weekly_site_update.R --year=2026 --max-week=6
#
# REQUIRES:
#   CFBD_API_KEY
#
#   data/preseason_ratings_<year>.csv
#     Required columns:
#       team, power_pts
#     Optional:
#       off_pts, def_pts
#
# OPTIONAL:
#   models/final_wepa_weights_6_2_24.RDS
#   models/eckel_mod.RDS
#
# OUTPUT:
#   output/site/<year>/
#     latest.csv
#     latest.json
#     meta.json
#     ratings_history.csv
#     missing_logos.csv   -- teams with missing/invalid logos (empty if none)
#     btb_scatter.png     -- offensive/defensive scatter with team logos
#     weekly/week_00.csv
#     weekly/week_XX.csv
#
# PLOTTING PACKAGES (auto-installed if absent):
#   ggplot2, ggimage
#   ggrepel (optional; used for label fallback when logos are missing)
# =============================================================================


# ---- Libraries --------------------------------------------------------------

ensure_cfbfastR <- function() {
  if (requireNamespace("cfbfastR", quietly = TRUE)) {
    return(invisible(TRUE))
  }

  tryCatch(
    install.packages(
      "cfbfastR",
      repos = "https://cloud.r-project.org"
    ),
    error = function(e) invisible(NULL)
  )

  if (!requireNamespace("cfbfastR", quietly = TRUE)) {
    if (!requireNamespace("remotes", quietly = TRUE)) {
      install.packages(
        "remotes",
        repos = "https://cloud.r-project.org"
      )
    }

    remotes::install_github(
      "sportsdataverse/cfbfastR"
    )
  }

  invisible(TRUE)
}


ensure_ggimage <- function() {
  if (requireNamespace("ggimage", quietly = TRUE) &&
      requireNamespace("ggplot2", quietly = TRUE)) {
    return(invisible(TRUE))
  }

  needed <- character(0)
  if (!requireNamespace("ggplot2", quietly = TRUE)) {
    needed <- c(needed, "ggplot2")
  }
  if (!requireNamespace("ggimage", quietly = TRUE)) {
    needed <- c(needed, "ggimage")
  }

  tryCatch(
    install.packages(
      needed,
      repos = "https://cloud.r-project.org"
    ),
    error = function(e) {
      message(
        sprintf(
          "Could not install plotting dependencies (%s): %s",
          paste(needed, collapse = ", "),
          conditionMessage(e)
        )
      )
    }
  )

  invisible(
    requireNamespace("ggimage", quietly = TRUE) &&
    requireNamespace("ggplot2", quietly = TRUE)
  )
}


suppressMessages({
  library(dplyr)
  library(tidyr)
  library(purrr)
  library(readr)
  library(stringr)
  library(tibble)
  library(jsonlite)
  library(lme4)
  library(lubridate)
})

options(dplyr.summarise.inform = FALSE)


# ---- Logo helpers -----------------------------------------------------------

is_valid_logo <- function(x) {
  !is.na(x) &
    nchar(trimws(x)) > 0 &
    grepl("^https?://", trimws(x))
}


normalize_team_key <- function(x) {
  x %>%
    str_trim() %>%
    str_squish() %>%
    str_to_lower() %>%
    str_replace_all(
      "\u00e9|\u00e8|\u00ea",
      "e"
    ) %>%
    str_replace_all(
      "\u00e1|\u00e0|\u00e2",
      "a"
    ) %>%
    str_replace_all(
      "[\u02bb\u2018\u2019\u0060']",
      "'"
    ) %>%
    str_replace_all(
      "[^a-z0-9 ']",
      " "
    ) %>%
    str_squish()
}


manual_logo_aliases <- c(
  "UTSA" = "UT San Antonio",
  "UConn" = "Connecticut",
  "Southern Miss" = "Southern Mississippi",
  "Sam Houston" = "Sam Houston State",
  "UL Monroe" = "Louisiana Monroe",
  "Massachusetts" = "UMass"
)


# ---- Config -----------------------------------------------------------------

.args <- commandArgs(trailingOnly = TRUE)

.get_arg <- function(flag, default) {
  hit <- grep(
    paste0("^", flag, "="),
    .args,
    value = TRUE
  )

  if (length(hit)) {
    sub(
      paste0("^", flag, "="),
      "",
      hit[1]
    )
  } else {
    default
  }
}


TARGET_SEASON <- as.integer(
  .get_arg(
    "--year",
    format(Sys.Date(), "%Y")
  )
)

MAX_WEEK_ARG <- suppressWarnings(
  as.integer(
    .get_arg(
      "--max-week",
      NA
    )
  )
)

OUT_ROOT <- .get_arg(
  "--outdir",
  "output/site"
)

MODEL_WEIGHTS_FILE <- "models/final_wepa_weights_6_2_24.RDS"
ECKEL_MODEL_FILE <- "models/eckel_mod.RDS"

PRESEASON_FILE <- sprintf(
  "data/preseason_ratings_%d.csv",
  TARGET_SEASON
)

PLAYS_SCALE <- 65
TARGET_SD <- 12
STANDARDIZE_PRIOR <- TRUE

PRIOR_G_FULL <- 8
PRIOR_POW <- 1.5

RECENCY_DECAY <- 0.90

NEW_TEAM_PRIOR_Q <- 0.10

GT_THRESH <- c(
  `1` = 28,
  `2` = 24,
  `3` = 21,
  `4` = 16
)

PRIOR_NAME_RECODE <- c(
  "Louisiana Monroe" = "UL Monroe",
  "Southern Mississippi" = "Southern Miss",
  "Sam Houston State" = "Sam Houston",
  "UMass" = "Massachusetts",
  "UT San Antonio" = "UTSA",
  "Connecticut" = "UConn",
  "San Jose State" = "San José State",
  "Hawaii" = "Hawai'i"
)


# ---- Guardrails -------------------------------------------------------------

require_cols <- function(df, cols, where) {
  miss <- setdiff(
    cols,
    names(df)
  )

  if (length(miss)) {
    stop(
      sprintf(
        paste0(
          "[%s] missing columns: %s\n",
          "Columns present: %s"
        ),
        where,
        paste(miss, collapse = ", "),
        paste(sort(names(df)), collapse = ", ")
      ),
      call. = FALSE
    )
  }

  invisible(df)
}


msg <- function(...) {
  message(
    sprintf(...)
  )
}


isTRUE_v <- function(x) {
  !is.na(x) & x
}


# ---- Logo quality reporting -------------------------------------------------

report_logo_quality <- function(
    teams_df,
    out_dir,
    threshold = 0.05
) {
  require_cols(
    teams_df,
    c("team", "conference", "logo"),
    "report_logo_quality"
  )

  n_total   <- nrow(teams_df)
  valid_idx <- is_valid_logo(teams_df$logo)
  n_valid   <- sum(valid_idx)
  n_missing <- n_total - n_valid
  pct       <- if (n_total > 0) n_missing / n_total else 0

  msg(
    "Logo quality: %d/%d teams have valid logos (%.1f%% missing).",
    n_valid,
    n_total,
    100 * pct
  )

  missing_df <- teams_df %>%
    filter(!is_valid_logo(logo)) %>%
    select(team, conference, logo)

  write_csv(
    missing_df,
    file.path(out_dir, "missing_logos.csv")
  )

  if (n_missing > 0) {
    msg(
      "Missing/invalid logos written to %s",
      file.path(out_dir, "missing_logos.csv")
    )
  }

  if (pct > threshold) {
    stop(
      sprintf(
        paste0(
          "Logo coverage too low: %.1f%% of teams (%d/%d) are missing ",
          "valid logos (threshold: %.0f%%). ",
          "Check %s and ensure cfbfastr_team names match the API school names."
        ),
        100 * pct,
        n_missing,
        n_total,
        100 * threshold,
        file.path(out_dir, "missing_logos.csv")
      ),
      call. = FALSE
    )
  }

  invisible(missing_df)
}


# ---- BTB scatter plot -------------------------------------------------------

plot_btb_scatter <- function(
    df,
    out_dir,
    season   = NA_integer_,
    width    = 12,
    height   = 10
) {
  required <- c("team", "off_pts", "def_pts", "logo")
  if (!all(required %in% names(df))) {
    msg(
      "Skipping scatter plot: missing columns (%s).",
      paste(setdiff(required, names(df)), collapse = ", ")
    )
    return(invisible(NULL))
  }

  can_image <- ensure_ggimage()

  if (!requireNamespace("ggplot2", quietly = TRUE)) {
    msg("Skipping scatter plot: ggplot2 not available.")
    return(invisible(NULL))
  }

  library(ggplot2)

  title_str <- if (!is.na(season)) {
    sprintf("%d BTB Power Rating", as.integer(season))
  } else {
    "BTB Power Rating"
  }

  df_plot <- df %>%
    mutate(
      logo_valid = is_valid_logo(logo),
      logo_safe  = if_else(logo_valid, logo, NA_character_)
    )

  p <- ggplot(
    df_plot,
    aes(x = off_pts, y = def_pts)
  ) +
    geom_point(
      color = "grey70",
      size  = 1.5,
      alpha = 0.4
    ) +
    geom_hline(
      yintercept = 0,
      linetype   = "dashed",
      color      = "grey50"
    ) +
    geom_vline(
      xintercept = 0,
      linetype   = "dashed",
      color      = "grey50"
    ) +
    labs(
      title = title_str,
      x     = "Offensive Rating (pts vs avg)",
      y     = "Defensive Rating (pts prevented vs avg)"
    ) +
    theme_minimal(base_size = 14)

  if (can_image) {
    library(ggimage)
    p <- p +
      ggimage::geom_image(
        data    = df_plot %>% filter(logo_valid),
        aes(image = logo_safe),
        size    = 0.05,
        na.rm   = TRUE
      )
  }

  no_logo_df <- df_plot %>% filter(!logo_valid)
  if (nrow(no_logo_df) > 0) {
    if (!requireNamespace("ggrepel", quietly = TRUE)) {
      p <- p +
        geom_text(
          data  = no_logo_df,
          aes(label = team),
          size  = 2.5,
          color = "grey40"
        )
    } else {
      library(ggrepel)
      p <- p +
        ggrepel::geom_text_repel(
          data  = no_logo_df,
          aes(label = team),
          size  = 2.5,
          color = "grey40"
        )
    }
  }

  out_path <- file.path(out_dir, "btb_scatter.png")

  tryCatch(
    {
      ggsave(
        out_path,
        plot   = p,
        width  = width,
        height = height,
        dpi    = 150
      )
      msg("Scatter plot written to %s", out_path)
    },
    error = function(e) {
      msg(
        "WARNING: Could not save scatter plot (%s): %s",
        out_path,
        conditionMessage(e)
      )
    }
  )

  invisible(p)
}


fetch_teams <- function(season) {
  t <- cfbfastR::cfbd_team_info(
    only_fbs = TRUE,
    year = season
  )

  require_cols(
    t,
    c(
      "school",
      "conference"
    ),
    "cfbd_team_info"
  )

  crosswalk_file <- "CFB Teams Full Crosswalk.csv"

  if (!file.exists(crosswalk_file)) {
    stop(
      sprintf(
        "Required team crosswalk file not found: %s",
        crosswalk_file
      ),
      call. = FALSE
    )
  }

  crosswalk <- read_csv(
    crosswalk_file,
    show_col_types = FALSE
  )

  require_cols(
    crosswalk,
    c(
      "cfbfastr_team",
      "logo"
    ),
    "CFB Teams Full Crosswalk.csv"
  )

  crosswalk <- crosswalk %>%
    transmute(
      team = as.character(cfbfastr_team),
      logo = as.character(logo)
    ) %>%
    mutate(
      team = str_trim(team),
      logo = str_trim(logo),
      logo = str_replace(
        logo,
        "^http://",
        "https://"
      ),
      logo = if_else(
        is_valid_logo(logo),
        logo,
        NA_character_
      )
    ) %>%
    filter(
      !is.na(team),
      team != ""
    ) %>%
    mutate(
      join_key = normalize_team_key(team)
    ) %>%
    distinct(
      join_key,
      .keep_all = TRUE
    )

  manual_matches <- tibble(
    team = names(manual_logo_aliases),
    manual_team = unname(manual_logo_aliases)
  ) %>%
    mutate(
      join_key = normalize_team_key(manual_team)
    ) %>%
    left_join(
      crosswalk %>% select(join_key, logo),
      by = "join_key"
    ) %>%
    select(team, logo)

  out <- t %>%
    transmute(
      team = as.character(school),
      conference
    ) %>%
    mutate(
      team = str_trim(team),
      join_key = normalize_team_key(team)
    ) %>%
    left_join(
      crosswalk %>% select(join_key, logo),
      by = "join_key"
    ) %>%
    select(-join_key) %>%
    left_join(
      manual_matches,
      by = "team",
      suffix = c("", ".manual")
    ) %>%
    mutate(
      logo = coalesce(logo, logo.manual)
    ) %>%
    select(-logo.manual)

  missing_logos <- out %>%
    filter(
      !is_valid_logo(logo)
    )

  if (nrow(missing_logos) > 0) {
    msg(
      "WARNING: %d FBS teams do not have a matched logo in %s: %s",
      nrow(missing_logos),
      crosswalk_file,
      paste(
        missing_logos$team,
        collapse = ", "
      )
    )
  }

  out
}

fetch_games <- function(season) {
  g <- cfbfastR::cfbd_game_info(
    season,
    season_type = "regular"
  )

  require_cols(
    g,
    c(
      "game_id",
      "week",
      "home_team",
      "away_team",
      "home_points",
      "away_points",
      "neutral_site"
    ),
    "cfbd_game_info"
  )

  g %>%
    mutate(
      completed =
        !is.na(home_points) &
        !is.na(away_points)
    ) %>%
    select(
      game_id,
      week,
      home_team,
      away_team,
      home_points,
      away_points,
      neutral_site,
      completed
    )
}


fetch_pbp <- function(season) {
  msg(
    paste0(
      "Downloading play-by-play for %d ",
      "(~100+ MB; silent and can take several minutes on slow connections)..."
    ),
    season
  )

  old_to <- options(
    timeout = max(
      1800,
      getOption("timeout")
    )
  )

  on.exit(
    options(old_to),
    add = TRUE
  )

  pbp <- tryCatch(
    cfbfastR::load_cfb_pbp(season),
    error = function(e) {
      msg(
        paste0(
          "load_cfb_pbp(%d) unavailable (%s) ",
          "-- treating as preseason: no PBP."
        ),
        season,
        conditionMessage(e)
      )

      NULL
    }
  )

  if (
    is.null(pbp) ||
    nrow(pbp) == 0
  ) {
    return(NULL)
  }

  pbp <- pbp %>%
    distinct(
      game_id,
      id_play,
      .keep_all = TRUE
    ) %>%
    mutate(
      season = if ("season" %in% names(.)) {
        coalesce(season, year)
      } else {
        year
      }
    )

  require_cols(
    pbp,
    c(
      "pass",
      "rush",
      "play_type",
      "penalty_detail",
      "distance",
      "position_rush",
      "sack",
      "fumble_vec",
      "down",
      "EPA",
      "wp_before",
      "wp_after",
      "yards_to_goal",
      "Goal_To_Go",
      "score_diff",
      "period",
      "pos_team",
      "home",
      "away",
      "ppa",
      "offense_play",
      "defense_play",
      "game_id",
      "drive_id",
      "id_play",
      "week",
      "new_drive_pts",
      "TimeSecsRem",
      "adj_TimeSecsRem",
      "drive_end_yards_to_goal",
      "pos_team_timeouts",
      "def_pos_team_timeouts",
      "drive_result",
      "def_pos_team"
    ),
    "load_cfb_pbp"
  )

  msg(
    "Play-by-play loaded: %d plays, weeks %d-%d.",
    nrow(pbp),
    min(pbp$week, na.rm = TRUE),
    max(pbp$week, na.rm = TRUE)
  )

  pbp
}


# ---- wEPA -------------------------------------------------------------------

# ... rest of file unchanged ...
