# ============================================================================
# nfl_site_update.R -- weekly NFL data build for the BTB public site
#
# Stateless build using nflverse schedules + play-by-play. The rating engine is
# the existing BTB two-stage NFL model: recency-weighted team model plus the
# current starter's QB value. Outputs are written to output/nfl/<season>/.
# ============================================================================

ensure_nflverse <- function() {
  if (requireNamespace("nflreadr", quietly = TRUE)) return(invisible(TRUE))

  tryCatch(
    install.packages(
      "nflreadr",
      repos = "https://cloud.r-project.org"
    ),
    error = function(e) invisible(NULL)
  )

  if (!requireNamespace("nflreadr", quietly = TRUE)) {
    stop(
      "nflreadr is required and could not be installed.",
      call. = FALSE
    )
  }

  invisible(TRUE)
}


suppressMessages({
  library(dplyr)
  library(tidyr)
  library(purrr)
  library(readr)
  library(stringr)
  library(tibble)
  library(jsonlite)
  library(zoo)
})


msg <- function(...) {
  cat(
    sprintf(...),
    "\n"
  )
}


# ---- Config ----------------------------------------------------------------

argv <- commandArgs(
  trailingOnly = TRUE
)


arg_val <- function(
    flag,
    default
) {

  hit <- grep(
    paste0(
      "^",
      flag,
      "="
    ),
    argv,
    value = TRUE
  )

  if (length(hit)) {

    sub(
      paste0(
        "^",
        flag,
        "="
      ),
      "",
      hit[1]
    )

  } else {

    default

  }
}


TARGET_SEASON <- as.integer(
  arg_val(
    "--year",
    "2026"
  )
)


MAX_WEEK_ARG <- suppressWarnings(
  as.integer(
    arg_val(
      "--max-week",
      NA
    )
  )
)


OUT_ROOT <- arg_val(
  "--outdir",
  "output/nfl"
)


WEIGHTS_FILE <-
  "models/weights_all.csv"


ECKEL_FILE <-
  "models/eckel_mod_nfl.RDS"


QB_OVERRIDES <-
  "data/qb_overrides.csv"


HIST_SEASONS <- 10

TEAM_HL <- 30

QB_DROP_FIRST <- 14

QB_HL <- 9999

QB_K <- 12

SNAP_REGRESS <- 0.5


QB_TIER_A <- 7

QB_TIER_B <- 14

QB_TIER_C <- 32


CARRY_WEPA <- 17

CARRY_OTHER <- 16


VERIFY_REGISTER <- c(

  paste0(
    "ma15/def_ma15: implemented as the canonical ",
    "18-game rolling epa/play (off_ma18/def_ma18); SAS name kept"
  ),

  paste0(
    "epa/pass/rush/proe/eckel carryover week defaulted ",
    "to 16 pending canonical export diff"
  ),

  paste0(
    "pass_1st_down = pass rate on 1st down; ",
    "succ_1d_pass = success rate on 1st-down passes ",
    "(pattern-derived)"
  ),

  paste0(
    "turnovers = interception + fumble_lost on offensive plays ",
    "(def mirror = takeaways)"
  ),

  paste0(
    "NFL eckel_mod_nfl.RDS uses the five drive-state ",
    "inputs expected by build_drive_frame()"
  ),

  paste0(
    "display def_wepa attributed by defteam (team's own defense); ",
    "canonical rolling_def_wepa groups by posteam"
  )
)


# ---- Guardrails ------------------------------------------------------------

require_cols <- function(
    df,
    cols,
    where
) {

  miss <- setdiff(
    cols,
    names(df)
  )


  if (length(miss)) {

    stop(
      sprintf(
        paste0(
          "[%s] missing columns (upstream schema drift?): %s\n",
          "  present: %s"
        ),

        where,

        paste(
          miss,
          collapse = ", "
        ),

        paste(
          sort(
            names(df)
          ),
          collapse = ", "
        )
      ),

      call. = FALSE
    )
  }


  invisible(TRUE)
}


# ---- Public-site team metadata ---------------------------------------------

NFL_TEAMS <- tibble::tribble(

  ~team,
  ~team_name,
  ~conference,
  ~division,
  ~logo,

  "ARI",
  "Arizona Cardinals",
  "NFC",
  "NFC West",
  "https://a.espncdn.com/i/teamlogos/nfl/500/ari.png",

  "ATL",
  "Atlanta Falcons",
  "NFC",
  "NFC South",
  "https://a.espncdn.com/i/teamlogos/nfl/500/atl.png",

  "BAL",
  "Baltimore Ravens",
  "AFC",
  "AFC North",
  "https://a.espncdn.com/i/teamlogos/nfl/500/bal.png",

  "BUF",
  "Buffalo Bills",
  "AFC",
  "AFC East",
  "https://a.espncdn.com/i/teamlogos/nfl/500/buf.png",

  "CAR",
  "Carolina Panthers",
  "NFC",
  "NFC South",
  "https://a.espncdn.com/i/teamlogos/nfl/500/car.png",

  "CHI",
  "Chicago Bears",
  "NFC",
  "NFC North",
  "https://a.espncdn.com/i/teamlogos/nfl/500/chi.png",

  "CIN",
  "Cincinnati Bengals",
  "AFC",
  "AFC North",
  "https://a.espncdn.com/i/teamlogos/nfl/500/cin.png",

  "CLE",
  "Cleveland Browns",
  "AFC",
  "AFC North",
  "https://a.espncdn.com/i/teamlogos/nfl/500/cle.png",

  "DAL",
  "Dallas Cowboys",
  "NFC",
  "NFC East",
  "https://a.espncdn.com/i/teamlogos/nfl/500/dal.png",

  "DEN",
  "Denver Broncos",
  "AFC",
  "AFC West",
  "https://a.espncdn.com/i/teamlogos/nfl/500/den.png",

  "DET",
  "Detroit Lions",
  "NFC",
  "NFC North",
  "https://a.espncdn.com/i/teamlogos/nfl/500/det.png",

  "GB",
  "Green Bay Packers",
  "NFC",
  "NFC North",
  "https://a.espncdn.com/i/teamlogos/nfl/500/gb.png",

  "HOU",
  "Houston Texans",
  "AFC",
  "AFC South",
  "https://a.espncdn.com/i/teamlogos/nfl/500/hou.png",

  "IND",
  "Indianapolis Colts",
  "AFC",
  "AFC South",
  "https://a.espncdn.com/i/teamlogos/nfl/500/ind.png",

  "JAX",
  "Jacksonville Jaguars",
  "AFC",
  "AFC South",
  "https://a.espncdn.com/i/teamlogos/nfl/500/jax.png",

  "KC",
  "Kansas City Chiefs",
  "AFC",
  "AFC West",
  "https://a.espncdn.com/i/teamlogos/nfl/500/kc.png",

  "LA",
  "Los Angeles Rams",
  "NFC",
  "NFC West",
  "https://a.espncdn.com/i/teamlogos/nfl/500/lar.png",

  "LAC",
  "Los Angeles Chargers",
  "AFC",
  "AFC West",
  "https://a.espncdn.com/i/teamlogos/nfl/500/lac.png",

  "LV",
  "Las Vegas Raiders",
  "AFC",
  "AFC West",
  "https://a.espncdn.com/i/teamlogos/nfl/500/lv.png",

  "MIA",
  "Miami Dolphins",
  "AFC",
  "AFC East",
  "https://a.espncdn.com/i/teamlogos/nfl/500/mia.png",

  "MIN",
  "Minnesota Vikings",
  "NFC",
  "NFC North",
  "https://a.espncdn.com/i/teamlogos/nfl/500/min.png",

  "NE",
  "New England Patriots",
  "AFC",
  "AFC East",
  "https://a.espncdn.com/i/teamlogos/nfl/500/ne.png",

  "NO",
  "New Orleans Saints",
  "NFC",
  "NFC South",
  "https://a.espncdn.com/i/teamlogos/nfl/500/no.png",

  "NYG",
  "New York Giants",
  "NFC",
  "NFC East",
  "https://a.espncdn.com/i/teamlogos/nfl/500/nyg.png",

  "NYJ",
  "New York Jets",
  "AFC",
  "AFC East",
  "https://a.espncdn.com/i/teamlogos/nfl/500/nyj.png",

  "PHI",
  "Philadelphia Eagles",
  "NFC",
  "NFC East",
  "https://a.espncdn.com/i/teamlogos/nfl/500/phi.png",

  "PIT",
  "Pittsburgh Steelers",
  "AFC",
  "AFC North",
  "https://a.espncdn.com/i/teamlogos/nfl/500/pit.png",

  "SEA",
  "Seattle Seahawks",
  "NFC",
  "NFC West",
  "https://a.espncdn.com/i/teamlogos/nfl/500/sea.png",

  "SF",
  "San Francisco 49ers",
  "NFC",
  "NFC West",
  "https://a.espncdn.com/i/teamlogos/nfl/500/sf.png",

  "TB",
  "Tampa Bay Buccaneers",
  "NFC",
  "NFC South",
  "https://a.espncdn.com/i/teamlogos/nfl/500/tb.png",

  "TEN",
  "Tennessee Titans",
  "AFC",
  "AFC South",
  "https://a.espncdn.com/i/teamlogos/nfl/500/ten.png",

  "WAS",
  "Washington Commanders",
  "NFC",
  "NFC East",
  "https://a.espncdn.com/i/teamlogos/nfl/500/was.png"
)


games_entering_week <- function(
    sched,
    season,
    target_week
) {

  sched %>%
    filter(
      .data$season ==
        .env$season,

      .data$week <
        .env$target_week,

      !is.na(
        home_score
      ),

      !is.na(
        away_score
      )
    ) %>%
    select(
      home_team,
      away_team
    ) %>%
    pivot_longer(
      everything(),
      values_to = "team"
    ) %>%
    count(
      team,
      name = "games"
    )
}


# ---- Fetchers --------------------------------------------------------------

fetch_schedules_all <- function() {

  g <-
    nflreadr::load_schedules()


  require_cols(
    g,

    c(
      "game_id",
      "season",
      "week",
      "game_type",
      "home_team",
      "away_team",
      "home_score",
      "away_score",
      "home_qb_id",
      "home_qb_name",
      "away_qb_id",
      "away_qb_name"
    ),

    "load_schedules"
  )


  g %>%
    mutate(
      home_team =
        nflreadr::clean_team_abbrs(
          home_team
        ),

      away_team =
        nflreadr::clean_team_abbrs(
          away_team
        )
    )
}


fetch_pbp <- function(
    seasons
) {

  msg(
    paste0(
      "Downloading play-by-play for %d-%d ",
      "(large; silent minutes possible)..."
    ),
    min(seasons),
    max(seasons)
  )


  old_to <- options(
    timeout =
      max(
        1800,
        getOption(
          "timeout"
        )
      )
  )


  on.exit(
    options(
      old_to
    ),
    add = TRUE
  )


  pbp <-
    nflreadr::load_pbp(
      seasons
    )


  require_cols(
    pbp,

    c(
      "game_id",
      "season",
      "week",
      "posteam",
      "defteam",
      "home_team",
      "away_team",
      "epa",
      "wp",
      "pass",
      "rush",
      "success",
      "down",
      "yardline_100",
      "qb_scramble",
      "qb_dropback",
      "fumble_lost",
      "interception",
      "incomplete_pass",
      "complete_pass",
      "sack",
      "air_yards",
      "play_type",
      "rush_attempt",
      "xpass",
      "first_down",
      "touchdown",
      "yards_gained",
      "drive",
      "half_seconds_remaining",
      "game_seconds_remaining",
      "posteam_timeouts_remaining",
      "defteam_timeouts_remaining",
      "name",
      "id"
    ),

    "load_pbp"
  )


  pbp %>%
    filter(
      game_id !=
        "2022_17_BUF_CIN"
    ) %>%
    mutate(
      across(
        c(
          posteam,
          defteam,
          home_team,
          away_team
        ),

        ~ nflreadr::clean_team_abbrs(
          .x
        )
      )
    )
}


# ---- NFL wEPA --------------------------------------------------------------

WEPA_NAMES <- c(
  "qb_rush",
  "neutral_second_down_rush",
  "incompletion_depth_s",
  "non_sack_fumble",
  "int",
  "goalline",
  "scaled_win_prob",
  "d_qb_rush",
  "d_neutral_second_down_rush",
  "d_incompletion_depth_s",
  "d_sack_fumble",
  "d_int",
  "d_fg",
  "d_third_down_pos",
  "defense_adj"
)


load_wepa_weights <- function(
    path
) {

  if (!file.exists(path)) {

    msg(
      paste0(
        "wEPA WEIGHTS MISSING (%s): ",
        "wEPA falls back to raw EPA."
      ),
      path
    )


    return(
      tibble::as_tibble_row(
        setNames(
          as.list(
            rep(
              0,
              length(
                WEPA_NAMES
              )
            )
          ),

          WEPA_NAMES
        )
      )
    )
  }


  raw <-
    readLines(
      path,
      warn = FALSE
    )


  values <-
    unlist(
      strsplit(
        paste(
          raw,
          collapse = ","
        ),

        ",",

        fixed = TRUE
      )
    )


  values <-
    trimws(
      values
    )


  values <-
    values[
      nzchar(
        values
      )
    ]


  values <-
    suppressWarnings(
      as.numeric(
        values
      )
    )


  if (
    length(values) !=
      length(WEPA_NAMES) ||
      anyNA(values)
  ) {

    stop(
      sprintf(
        paste0(
          "weights_all.csv has %d usable numeric values; ",
          "expected exactly %d."
        ),

        length(values),

        length(
          WEPA_NAMES
        )
      ),

      call. = FALSE
    )
  }


  tibble::as_tibble_row(
    setNames(
      as.list(
        values
      ),

      WEPA_NAMES
    )
  )
}


calculate_wepa <- function(
    pbp_data,
    wepa_weights
) {

  pbp_data %>%
    mutate(
      play_call =
        case_when(
          qb_dropback == 1 ~ "Pass",
          rush_attempt == 1 ~ "Run",
          TRUE ~ NA_character_
        ),

      qb_rush_weight =
        ifelse(
          qb_scramble == 1 &
            fumble_lost != 1,

          1 +
            wepa_weights$qb_rush,

          1
        ),

      neutral_second_down_rush_weight =
        ifelse(
          down == 2 &
            play_call == "Run" &
            yardline_100 > 20 &
            yardline_100 < 85 &
            (
              wp < 0.90 |
                wp > 0.10
            ) &
            qb_scramble != 1 &
            fumble_lost != 1 &
            epa < 0,

          1 +
            wepa_weights$neutral_second_down_rush,

          1
        ),

      incompletion_depth_s_weight =
        ifelse(
          incomplete_pass == 1 &
            interception != 1,

          1 +
            wepa_weights$incompletion_depth_s *
            (
              2 *
                (
                  1 /
                    (
                      1 +
                        exp(
                          -0.1 *
                            air_yards +
                            0.75
                        )
                    ) -
                    0.5
                )
            ),

          1
        ),

      non_sack_fumble_weight =
        ifelse(
          sack != 1 &
            fumble_lost == 1,

          1 +
            wepa_weights$non_sack_fumble,

          1
        ),

      int_weight =
        ifelse(
          interception == 1,

          1 +
            wepa_weights$int,

          1
        ),

      goalline_weight =
        ifelse(
          yardline_100 < 3 &
            down < 4,

          1 +
            wepa_weights$goalline,

          1
        ),

      scaled_win_prob_weight =
        1 +
        (
          -wepa_weights$scaled_win_prob *
            ifelse(
              wp <= 0.5,

              1 /
                (
                  1 +
                    exp(
                      -10 *
                        (
                          2 *
                            wp -
                            0.5
                        )
                    )
                ) -
                0.5,

              1 /
                (
                  1 +
                    exp(
                      -10 *
                        (
                          2 *
                            (
                              1 -
                                wp
                            ) -
                            0.5
                        )
                    )
                ) -
                0.5
            )
        ),

      wepa =
        pmin(
          pmax(
            epa *
              qb_rush_weight *
              neutral_second_down_rush_weight *
              incompletion_depth_s_weight *
              non_sack_fumble_weight *
              int_weight *
              goalline_weight *
              scaled_win_prob_weight,

            -10
          ),

          10
        ),

      d_wepa =
        pmin(
          pmax(
            epa *
              (
                1 +
                  wepa_weights$defense_adj
              ) *
              ifelse(
                qb_scramble == 1 &
                  fumble_lost != 1,

                1 +
                  wepa_weights$d_qb_rush,

                1
              ) *
              ifelse(
                down == 2 &
                  play_call == "Run" &
                  yardline_100 > 20 &
                  yardline_100 < 85 &
                  (
                    wp < 0.90 |
                      wp > 0.10
                  ) &
                  qb_scramble != 1 &
                  fumble_lost != 1 &
                  epa < 0,

                1 +
                  wepa_weights$d_neutral_second_down_rush,

                1
              ) *
              ifelse(
                incomplete_pass == 1 &
                  interception != 1,

                1 +
                  wepa_weights$d_incompletion_depth_s *
                  (
                    2 *
                      (
                        1 /
                          (
                            1 +
                              exp(
                                -0.1 *
                                  air_yards +
                                  0.75
                              )
                          ) -
                          0.5
                      )
                  ),

                1
              ) *
              ifelse(
                sack == 1 &
                  fumble_lost == 1,

                1 +
                  wepa_weights$d_sack_fumble,

                1
              ) *
              ifelse(
                interception == 1,

                1 +
                  wepa_weights$d_int,

                1
              ) *
              ifelse(
                play_type ==
                  "field_goal",

                1 +
                  wepa_weights$d_fg,

                1
              ) *
              ifelse(
                down == 3 &
                  epa > 0,

                1 +
                  wepa_weights$d_third_down_pos,

                1
              ),

            -10
          ),

          10
        )
    )
}


# ---- Generic into-week engine ----------------------------------------------

panel_grid <- NULL


into_week <- function(
    game_tbl,
    team_col,
    num,
    den,
    out,
    carry_week
) {

  g <- game_tbl %>%
    rename(
      team =
        all_of(
          team_col
        )
    ) %>%
    group_by(
      team,
      season,
      week
    ) %>%
    summarize(
      n_ =
        sum(
          coalesce(
            .data[[num]],
            0
          )
        ),

      d_ =
        sum(
          coalesce(
            .data[[den]],
            0
          )
        ),

      .groups =
        "drop"
    )


  p <- panel_grid %>%
    left_join(
      g,
      by =
        c(
          "team",
          "season",
          "week"
        )
    ) %>%
    mutate(
      n_ =
        coalesce(
          n_,
          0
        ),

      d_ =
        coalesce(
          d_,
          0
        )
    ) %>%
    arrange(
      team,
      season,
      week
    ) %>%
    group_by(
      team,
      season
    ) %>%
    mutate(
      cn =
        cumsum(
          n_
        ) -
        n_,

      cd =
        cumsum(
          d_
        ) -
        d_,

      val =
        ifelse(
          cd > 0,
          cn / cd,
          NA_real_
        )
    ) %>%
    ungroup()


  carry <- p %>%
    filter(
      week ==
        carry_week
    ) %>%
    transmute(
      team,

      season =
        season +
        1,

      carry_val =
        val
    )


  p %>%
    left_join(
      carry,
      by =
        c(
          "team",
          "season"
        )
    ) %>%
    mutate(
      !!out :=
        ifelse(
          week == 1 |
            is.na(
              val
            ),

          carry_val,

          val
        )
    ) %>%
    select(
      team,
      season,
      week,
      all_of(
        out
      )
    )
}


into_week_mean <- function(
    game_tbl,
    team_col,
    valcol,
    out,
    carry_week
) {

  g <- game_tbl %>%
    rename(
      team =
        all_of(
          team_col
        )
    ) %>%
    group_by(
      team,
      season,
      week
    ) %>%
    summarize(
      v_ =
        sum(
          coalesce(
            .data[[valcol]],
            0
          )
        ),

      k_ =
        sum(
          !is.na(
            .data[[valcol]]
          )
        ),

      .groups =
        "drop"
    )


  p <- panel_grid %>%
    left_join(
      g,
      by =
        c(
          "team",
          "season",
          "week"
        )
    ) %>%
    mutate(
      v_ =
        coalesce(
          v_,
          0
        ),

      k_ =
        coalesce(
          k_,
          0
        )
    ) %>%
    arrange(
      team,
      season,
      week
    ) %>%
    group_by(
      team,
      season
    ) %>%
    mutate(
      cs =
        cumsum(
          v_
        ) -
        v_,

      ck =
        cumsum(
          k_
        ) -
        k_,

      val =
        ifelse(
          ck > 0,
          cs / ck,
          NA_real_
        )
    ) %>%
    ungroup()


  carry <- p %>%
    filter(
      week ==
        carry_week
    ) %>%
    transmute(
      team,

      season =
        season +
        1,

      carry_val =
        val
    )


  p %>%
    left_join(
      carry,
      by =
        c(
          "team",
          "season"
        )
    ) %>%
    mutate(
      !!out :=
        ifelse(
          week == 1 |
            is.na(
              val
            ),

          carry_val,

          val
        )
    ) %>%
    select(
      team,
      season,
      week,
      all_of(
        out
      )
    )
}


# ---- Drive/Eckel frame -----------------------------------------------------

build_drive_frame <- function(
    pbp,
    eckel_model
) {

  dd <- pbp %>%
    filter(
      pass == 1 |
        rush == 1,

      !is.na(
        posteam
      ),

      !is.na(
        defteam
      )
    ) %>%
    mutate(
      touchdown_yards =
        ifelse(
          touchdown == 1 &
            yards_gained >= 40,
          1,
          0
        ),

      opp_play =
        ifelse(
          yardline_100 <= 40 &
            down == 1,
          1,
          0
        )
    ) %>%
    group_by(
      game_id,
      posteam,
      defteam,
      season,
      week,
      drive
    ) %>%
    summarize(
      eckel_plays =
        sum(
          opp_play,
          na.rm = TRUE
        ) +
        sum(
          touchdown_yards,
          na.rm = TRUE
        ),

      half_seconds_remaining =
        first(
          half_seconds_remaining
        ),

      game_seconds_remaining =
        first(
          game_seconds_remaining
        ),

      yardline_100 =
        first(
          yardline_100
        ),

      posteam_timeouts_remaining =
        first(
          posteam_timeouts_remaining
        ),

      defteam_timeouts_remaining =
        first(
          defteam_timeouts_remaining
        ),

      .groups =
        "drop"
    ) %>%
    mutate(
      eckel =
        ifelse(
          eckel_plays > 0,
          1,
          0
        )
    ) %>%
    distinct()


  if (!is.null(eckel_model)) {

    nd <- dd %>%
      mutate(

        # Native NFL variable names are retained above.

        # Compatibility aliases for models using the
        # older/CFB-style drive-state names.
        half_secs_rem =
          half_seconds_remaining,

        game_secs_rem =
          game_seconds_remaining,

        start_yards_to_goal =
          yardline_100,

        pos_team_timeouts =
          posteam_timeouts_remaining,

        def_pos_team_timeouts =
          defteam_timeouts_remaining
      )


    model_terms <-
      all.vars(
        stats::delete.response(
          stats::terms(
            eckel_model
          )
        )
      )


    missing_terms <-
      setdiff(
        model_terms,
        names(
          nd
        )
      )


    if (length(missing_terms)) {

      stop(
        sprintf(
          paste0(
            "NFL Eckel model requires variables not available ",
            "in the drive frame: %s"
          ),
          paste(
            missing_terms,
            collapse = ", "
          )
        ),
        call. = FALSE
      )
    }


    msg(
      "NFL Eckel model terms: %s",
      paste(
        model_terms,
        collapse = ", "
      )
    )


    dd$expected_eckel_prob <-
      suppressWarnings(
        predict(
          eckel_model,
          newdata = nd,
          type = "response"
        )
      )

  } else {

    dd$expected_eckel_prob <-
      NA_real_

  }


  dd
}

# ---- Feature table ---------------------------------------------------------

build_features <- function(
    pbp,
    wepa_pbp,
    drives
) {

  scrim <- pbp %>%
    filter(
      pass == 1 |
        rush == 1,

      !is.na(
        epa
      )
    )


  g_off <- scrim %>%
    filter(
      !is.na(
        posteam
      )
    ) %>%
    group_by(
      game_id,
      season,
      week,
      team =
        posteam
    ) %>%
    summarize(
      plays =
        n(),

      tot_epa =
        sum(
          epa
        ),

      succ =
        sum(
          success,
          na.rm = TRUE
        ),

      tos =
        sum(
          coalesce(
            interception,
            0
          ) +
          coalesce(
            fumble_lost,
            0
          )
        ),

      passes =
        sum(
          pass == 1
        ),

      pass_epa =
        sum(
          epa[
            pass == 1
          ]
        ),

      p_succ =
        sum(
          success[
            pass == 1
          ],
          na.rm = TRUE
        ),

      rushes =
        sum(
          rush == 1
        ),

      rush_epa =
        sum(
          epa[
            rush == 1
          ]
        ),

      r_succ =
        sum(
          success[
            rush == 1
          ],
          na.rm = TRUE
        ),

      early_n =
        sum(
          down %in%
            1:2
        ),

      early_s =
        sum(
          success[
            down %in%
              1:2
          ],
          na.rm = TRUE
        ),

      late_n =
        sum(
          down %in%
            3:4
        ),

      late_s =
        sum(
          success[
            down %in%
              3:4
          ],
          na.rm = TRUE
        ),

      d1_n =
        sum(
          down == 1
        ),

      d1_pass =
        sum(
          down == 1 &
            pass == 1
        ),

      d1p_s =
        sum(
          success[
            down == 1 &
              pass == 1
          ],
          na.rm = TRUE
        ),

      td_n =
        sum(
          down == 3
        ),

      td_conv =
        sum(
          down == 3 &
            first_down == 1,
          na.rm = TRUE
        ),

      proe_g =
        mean(
          pass -
            xpass,
          na.rm = TRUE
        ),

      .groups =
        "drop"
    )


  g_def <- scrim %>%
    filter(
      !is.na(
        defteam
      )
    ) %>%
    group_by(
      game_id,
      season,
      week,
      team =
        defteam
    ) %>%
    summarize(
      plays =
        n(),

      tot_epa =
        sum(
          epa
        ),

      succ =
        sum(
          success,
          na.rm = TRUE
        ),

      tos =
        sum(
          coalesce(
            interception,
            0
          ) +
          coalesce(
            fumble_lost,
            0
          )
        ),

      passes =
        sum(
          pass == 1
        ),

      pass_epa =
        sum(
          epa[
            pass == 1
          ]
        ),

      p_succ =
        sum(
          success[
            pass == 1
          ],
          na.rm = TRUE
        ),

      rushes =
        sum(
          rush == 1
        ),

      rush_epa =
        sum(
          epa[
            rush == 1
          ]
        ),

      r_succ =
        sum(
          success[
            rush == 1
          ],
          na.rm = TRUE
        ),

      early_n =
        sum(
          down %in%
            1:2
        ),

      early_s =
        sum(
          success[
            down %in%
              1:2
          ],
          na.rm = TRUE
        ),

      late_n =
        sum(
          down %in%
            3:4
        ),

      late_s =
        sum(
          success[
            down %in%
              3:4
          ],
          na.rm = TRUE
        ),

      proe_g =
        mean(
          pass -
            xpass,
          na.rm = TRUE
        ),

      games =
        1,

      .groups =
        "drop"
    )


  g_wepa <- wepa_pbp %>%
    filter(
      !is.na(
        posteam
      )
    ) %>%
    group_by(
      game_id,
      season,
      week,
      team =
        posteam
    ) %>%
    summarize(
      off_wepa_g =
        sum(
          wepa,
          na.rm = TRUE
        ),

      one =
        1,

      .groups =
        "drop"
    )


  g_wepa_def <- wepa_pbp %>%
    filter(
      !is.na(
        defteam
      )
    ) %>%
    group_by(
      game_id,
      season,
      week,
      team =
        defteam
    ) %>%
    summarize(
      dwepa_allowed_g =
        sum(
          d_wepa,
          na.rm = TRUE
        ),

      one =
        1,

      .groups =
        "drop"
    )


  g_eck <- drives %>%
    group_by(
      game_id,
      season,
      week,
      team =
        posteam
    ) %>%
    summarize(
      drives =
        n_distinct(
          drive
        ),

      eckels =
        sum(
          eckel
        ),

      oe_g =
        mean(
          eckel
        ) -
        mean(
          expected_eckel_prob
        ),

      .groups =
        "drop"
    )


  g_eck_d <- drives %>%
    group_by(
      game_id,
      season,
      week,
      team =
        defteam
    ) %>%
    summarize(
      drives =
        n_distinct(
          drive
        ),

      eckels =
        sum(
          eckel
        ),

      oe_g =
        mean(
          eckel
        ) -
        mean(
          expected_eckel_prob
        ),

      .groups =
        "drop"
    )


  f <- list(

    into_week(
      g_off,
      "team",
      "tot_epa",
      "plays",
      "off_epa_into_week",
      CARRY_OTHER
    ),

    into_week(
      g_off,
      "team",
      "pass_epa",
      "passes",
      "off_pass_epa_into_week",
      CARRY_OTHER
    ),

    into_week(
      g_off,
      "team",
      "rush_epa",
      "rushes",
      "off_rush_epa_into_week",
      CARRY_OTHER
    ),

    into_week(
      g_off,
      "team",
      "succ",
      "plays",
      "succsess_rate_into_week",
      CARRY_OTHER
    ),

    into_week(
      g_eck,
      "team",
      "eckels",
      "drives",
      "eckel_rate_into_week",
      CARRY_OTHER
    ),

    into_week(
      g_eck %>%
        mutate(
          oe_sum =
            oe_g
        ),
      "team",
      "oe_sum",
      "drives",
      "eckel_rate_oe",
      CARRY_OTHER
    ),

    into_week(
      g_off,
      "team",
      "td_conv",
      "td_n",
      "third_down_convert",
      CARRY_OTHER
    ),

    into_week_mean(
      g_off,
      "team",
      "proe_g",
      "proe",
      CARRY_OTHER
    ),

    into_week(
      g_off,
      "team",
      "r_succ",
      "rushes",
      "succ_rate_run",
      CARRY_OTHER
    ),

    into_week(
      g_off,
      "team",
      "p_succ",
      "passes",
      "succ_rate_pass",
      CARRY_OTHER
    ),

    into_week(
      g_off,
      "team",
      "early_s",
      "early_n",
      "succ_early",
      CARRY_OTHER
    ),

    into_week(
      g_off,
      "team",
      "late_s",
      "late_n",
      "succ_late",
      CARRY_OTHER
    ),

    into_week(
      g_off,
      "team",
      "d1_pass",
      "d1_n",
      "pass_1st_down",
      CARRY_OTHER
    ),

    into_week(
      g_off,
      "team",
      "d1p_s",
      "d1_pass",
      "succ_1d_pass",
      CARRY_OTHER
    ),

    into_week(
      g_wepa,
      "team",
      "off_wepa_g",
      "one",
      "off_wepa",
      CARRY_WEPA
    ),

    into_week(
      g_wepa_def,
      "team",
      "dwepa_allowed_g",
      "one",
      "def_wepa_own",
      CARRY_WEPA
    ),

    into_week(
      g_def,
      "team",
      "tot_epa",
      "plays",
      "def_epa_into_week",
      CARRY_OTHER
    ),

    into_week(
      g_def,
      "team",
      "pass_epa",
      "passes",
      "def_pass_epa_into_week",
      CARRY_OTHER
    ),

    into_week(
      g_def,
      "team",
      "rush_epa",
      "rushes",
      "def_rush_epa_into_week",
      CARRY_OTHER
    ),

    into_week(
      g_def,
      "team",
      "succ",
      "plays",
      "Def_succsess_rate_into_week",
      CARRY_OTHER
    ),

    into_week(
      g_eck_d,
      "team",
      "eckels",
      "drives",
      "def_eckel",
      CARRY_OTHER
    ),

    into_week(
      g_eck_d %>%
        mutate(
          oe_sum =
            oe_g
        ),
      "team",
      "oe_sum",
      "drives",
      "def_eckel_oe",
      CARRY_OTHER
    ),

    into_week_mean(
      g_def,
      "team",
      "proe_g",
      "def_proe",
      CARRY_OTHER
    ),

    into_week(
      g_def,
      "team",
      "r_succ",
      "rushes",
      "def_succ_rate_run",
      CARRY_OTHER
    ),

    into_week(
      g_def,
      "team",
      "p_succ",
      "passes",
      "def_succ_rate_pass",
      CARRY_OTHER
    ),

    into_week(
      g_def,
      "team",
      "early_s",
      "early_n",
      "def_succ_early",
      CARRY_OTHER
    ),

    into_week(
      g_def,
      "team",
      "late_s",
      "late_n",
      "def_succ_late",
      CARRY_OTHER
    ),

    into_week(
      g_def,
      "team",
      "tos",
      "games",
      "def_turnovers_per_game",
      CARRY_OTHER
    ),

    into_week(
      g_off %>%
        mutate(
          t65 =
            65 *
            tos
        ),
      "team",
      "t65",
      "plays",
      "turnovers_per_game",
      CARRY_OTHER
    )
  )


  ma18_panel <- function(
      g
  ) {

    played <- g %>%
      arrange(
        team,
        season,
        week
      ) %>%
      group_by(
        team
      ) %>%
      mutate(
        ma_thru =
          rollapplyr(
            tot_epa,
            18,
            mean,
            fill = NA
          ) /
          rollapplyr(
            plays,
            18,
            mean,
            fill = NA
          )
      ) %>%
      ungroup() %>%
      select(
        team,
        season,
        week,
        ma_thru
      )


    panel_grid %>%
      left_join(
        played,
        by =
          c(
            "team",
            "season",
            "week"
          )
      ) %>%
      arrange(
        team,
        season,
        week
      ) %>%
      group_by(
        team
      ) %>%
      mutate(
        ma =
          lag(
            zoo::na.locf(
              ma_thru,
              na.rm = FALSE
            )
          )
      ) %>%
      ungroup() %>%
      select(
        team,
        season,
        week,
        ma
      )
  }


  f <- c(
    f,

    list(
      ma18_panel(
        g_off
      ) %>%
        rename(
          ma15 =
            ma
        ),

      ma18_panel(
        g_def
      ) %>%
        rename(
          def_ma15 =
            ma
        )
    )
  )


  reduce(
    f,

    function(
        a,
        b
    ) {

      full_join(
        a,
        b,
        by =
          c(
            "team",
            "season",
            "week"
          )
      )
    }
  )
}


# ---- Starter QB rolling form -----------------------------------------------

build_qb_ma10 <- function(
    pbp
) {

  qg <- pbp %>%
    filter(
      pass == 1 |
        rush == 1,

      !is.na(
        posteam
      )
    ) %>%
    group_by(
      id,
      game_id,
      season,
      week,
      posteam
    ) %>%
    summarize(
      name =
        first(
          name
        ),

      plays =
        n(),

      passes =
        sum(
          complete_pass +
            incomplete_pass,
          na.rm = TRUE
        ),

      total_epa =
        sum(
          epa,
          na.rm = TRUE
        ),

      dropbacks =
        sum(
          qb_dropback,
          na.rm = TRUE
        ),

      sacks =
        sum(
          sack,
          na.rm = TRUE
        ),

      .groups =
        "drop"
    ) %>%
    filter(
      passes >= 5
    ) %>%
    arrange(
      season,
      week
    ) %>%
    group_by(
      id
    ) %>%
    mutate(
      game_num =
        row_number(),

      ma10_epa_play =
        rollapply(
          lag(
            total_epa
          ),
          10,
          mean,
          align = "right",
          fill = NA
        ) /
        rollapply(
          lag(
            plays
          ),
          10,
          mean,
          align = "right",
          fill = NA
        ),

      ma10_sack_rate =
        rollapply(
          lag(
            sacks
          ),
          10,
          mean,
          align = "right",
          fill = NA
        ) /
        rollapply(
          lag(
            dropbacks
          ),
          10,
          mean,
          align = "right",
          fill = NA
        )
    ) %>%
    ungroup()


  starters <- pbp %>%
    filter(
      pass == 1,

      !is.na(
        posteam
      )
    ) %>%
    group_by(
      game_id,
      season,
      week,
      posteam
    ) %>%
    summarize(
      starting_qb =
        first(
          name
        ),

      .groups =
        "drop"
    )


  qs <- qg %>%
    left_join(
      starters,
      by =
        c(
          "game_id",
          "season",
          "week",
          "posteam"
        )
    ) %>%
    filter(
      name ==
        starting_qb
    ) %>%
    select(
      team =
        posteam,

      season,
      week,
      ma10_epa_play,
      ma10_sack_rate
    )


  panel_grid %>%
    left_join(
      qs,
      by =
        c(
          "team",
          "season",
          "week"
        )
    ) %>%
    arrange(
      team,
      season,
      week
    ) %>%
    group_by(
      team
    ) %>%
    mutate(
      ma10_epa_play =
        lag(
          zoo::na.locf(
            ma10_epa_play,
            na.rm = FALSE
          )
        ),

      ma10_sack_rate =
        lag(
          zoo::na.locf(
            ma10_sack_rate,
            na.rm = FALSE
          )
        )
    ) %>%
    ungroup() %>%
    select(
      team,
      season,
      week,
      ma10_epa_play,
      ma10_sack_rate
    )
}


OFF_VARS <- c(
  "off_epa_into_week",
  "off_pass_epa_into_week",
  "off_rush_epa_into_week",
  "succsess_rate_into_week",
  "ma15",
  "eckel_rate_into_week",
  "eckel_rate_oe",
  "ma10_epa_play",
  "ma10_sack_rate",
  "third_down_convert",
  "proe",
  "succ_rate_run",
  "succ_rate_pass",
  "succ_early",
  "succ_late",
  "pass_1st_down",
  "succ_1d_pass"
)


DEF_VARS <- c(
  "def_epa_into_week",
  "def_pass_epa_into_week",
  "def_rush_epa_into_week",
  "Def_succsess_rate_into_week",
  "def_ma15",
  "def_eckel",
  "def_eckel_oe",
  "def_proe",
  "def_succ_rate_run",
  "def_succ_rate_pass",
  "def_succ_early",
  "def_succ_late",
  "def_turnovers_per_game"
)


# ---- Long table ------------------------------------------------------------

build_long <- function(
    sched,
    feats,
    qb10
) {

  base <- bind_rows(

    sched %>%
      transmute(
        game_id,
        season,
        week,

        team =
          home_team,

        opp =
          away_team,

        home_team,
        away_team,

        is_home =
          1L,

        points_scored =
          home_score,

        QBID =
          home_qb_id,

        QBname =
          home_qb_name
      ),

    sched %>%
      transmute(
        game_id,
        season,
        week,

        team =
          away_team,

        opp =
          home_team,

        home_team,
        away_team,

        is_home =
          0L,

        points_scored =
          away_score,

        QBID =
          away_qb_id,

        QBname =
          away_qb_name
      )
  ) %>%
    mutate(
      QBID =
        ifelse(
          is.na(
            QBID
          ) |
            QBID == "",

          "00-0000000",

          QBID
        )
    )


  long <- base %>%
    left_join(
      feats,
      by =
        c(
          "team",
          "season",
          "week"
        )
    ) %>%
    left_join(
      qb10,
      by =
        c(
          "team",
          "season",
          "week"
        )
    )


  opp_side <- long %>%
    select(
      game_id,
      team,
      all_of(
        DEF_VARS
      )
    ) %>%
    rename_with(
      ~ paste0(
        "opp_",
        .x
      ),
      all_of(
        DEF_VARS
      )
    )


  long %>%
    left_join(
      opp_side,
      by =
        c(
          "game_id",
          "opp" =
            "team"
        )
    ) %>%
    arrange(
      QBID,
      season,
      week
    ) %>%
    group_by(
      QBID
    ) %>%
    mutate(
      qb_game_num =
        row_number()
    ) %>%
    ungroup() %>%
    group_by(
      QBID
    ) %>%
    mutate(
      career_starts_total =
        max(
          qb_game_num
        )
    ) %>%
    ungroup()
}


week_index <- function(
    long
) {

  long %>%
    distinct(
      season,
      week
    ) %>%
    arrange(
      season,
      week
    ) %>%
    mutate(
      raw_order =
        row_number()
    )
}


# ---- Rating engine ---------------------------------------------------------

fit_power <- function(
    long,
    tidx,
    panel_feats,
    target_season,
    target_week
) {

  ti <-
    tidx$raw_order[
      tidx$season ==
        target_season &
        tidx$week ==
        target_week
    ]


  if (!length(ti)) {

    return(NULL)

  }


  d <- long %>%
    left_join(
      tidx,
      by =
        c(
          "season",
          "week"
        )
    ) %>%
    mutate(
      w =
        0.5 ^
        (
          (
            ti -
              raw_order
          ) /
            TEAM_HL
        ),

      qb_A =
        as.integer(
          qb_game_num <=
            QB_TIER_A
        ),

      qb_B =
        as.integer(
          qb_game_num >
            QB_TIER_A &
            qb_game_num <=
            QB_TIER_B
        ),

      qb_C =
        as.integer(
          qb_game_num >
            QB_TIER_B &
            qb_game_num <=
            QB_TIER_C
        )
    )


  train <- d %>%
    filter(
      !is.na(
        points_scored
      ),

      raw_order <
        ti
    ) %>%
    filter(
      if_all(
        all_of(
          c(
            OFF_VARS,
            paste0(
              "opp_",
              DEF_VARS
            )
          )
        ),

        ~ !is.na(
          .x
        )
      )
    )


  snap_qb <- d %>%
    filter(
      season ==
        target_season,

      week ==
        target_week
    ) %>%
    select(
      team,
      QBID,
      QBname
    )


  snap <- panel_feats %>%
    filter(
      season ==
        target_season,

      week ==
        target_week
    ) %>%
    left_join(
      snap_qb,
      by =
        "team"
    )


  if (
    nrow(train) <
      200
  ) {

    return(NULL)

  }


  fml <-
    reformulate(
      c(
        "is_home",
        "qb_A",
        "qb_B",
        "qb_C",
        OFF_VARS,
        paste0(
          "opp_",
          DEF_VARS
        )
      ),

      response =
        "points_scored"
    )


  fit <-
    lm(
      fml,
      data = train,
      weights = w
    )


  be <-
    coef(
      fit
    )


  be[
    is.na(
      be
    )
  ] <- 0


  train$resid <-
    train$points_scored -
    predict(
      fit,
      train
    )


  qbw <- train %>%
    filter(
      career_starts_total <=
        QB_DROP_FIRST |
        qb_game_num >
        QB_DROP_FIRST
    ) %>%
    mutate(
      wq =
        0.5 ^
        (
          (
            ti -
              raw_order
          ) /
            QB_HL
        )
    ) %>%
    group_by(
      QBID
    ) %>%
    summarize(
      wmean =
        weighted.mean(
          resid,
          wq
        ),

      sw =
        sum(
          wq
        ),

      .groups =
        "drop"
    ) %>%
    mutate(
      resid_val =
        (
          sw /
            (
              sw +
                QB_K
            )
        ) *
        wmean
    )


  tiers <- train %>%
    group_by(
      QBID
    ) %>%
    summarize(
      career_starts =
        max(
          career_starts_total
        ),

      QBname =
        last(
          QBname
        ),

      .groups =
        "drop"
    ) %>%
    mutate(
      tier_base =
        case_when(
          career_starts <=
            QB_TIER_A ~
            be[[
              "qb_A"
            ]],

          career_starts <=
            QB_TIER_B ~
            be[[
              "qb_B"
            ]],

          career_starts <=
            QB_TIER_C ~
            be[[
              "qb_C"
            ]],

          TRUE ~
            0
        )
    )


  qb_rt <- tiers %>%
    left_join(
      qbw %>%
        select(
          QBID,
          resid_val
        ),
      by =
        "QBID"
    ) %>%
    mutate(
      resid_val =
        coalesce(
          resid_val,
          0
        ),

      qb_value =
        tier_base +
        resid_val
    )


  mu_off <- train %>%
    summarize(
      across(
        all_of(
          OFF_VARS
        ),

        ~ mean(
          .x,
          na.rm = TRUE
        )
      )
    )


  mu_def <- train %>%
    summarize(
      across(
        all_of(
          DEF_VARS
        ),

        ~ mean(
          .x,
          na.rm = TRUE
        )
      )
    )


  sr <-
    if (
      target_week ==
        1
    ) {

      SNAP_REGRESS

    } else {

      1

    }


  sn <- snap


  for (
    v in
      OFF_VARS
  ) {

    sn[[v]] <-
      mu_off[[v]] +
      sr *
      (
        coalesce(
          sn[[v]],
          mu_off[[v]]
        ) -
        mu_off[[v]]
      )
  }


  for (
    v in
      DEF_VARS
  ) {

    sn[[v]] <-
      mu_def[[v]] +
      sr *
      (
        coalesce(
          sn[[v]],
          mu_def[[v]]
        ) -
        mu_def[[v]]
      )
  }


  O_T <-
    as.numeric(
      as.matrix(
        sn[
          ,
          OFF_VARS
        ]
      ) %*%
      be[
        OFF_VARS
      ]
    )


  D_T <-
    as.numeric(
      as.matrix(
        sn[
          ,
          DEF_VARS
        ]
      ) %*%
      be[
        paste0(
          "opp_",
          DEF_VARS
        )
      ]
    )


  ratings <- sn %>%
    mutate(
      O_T =
        O_T,

      D_T =
        D_T
    ) %>%
    mutate(
      off_rt =
        O_T -
        mean(
          O_T,
          na.rm = TRUE
        ),

      def_good =
        -(
          D_T -
            mean(
              D_T,
              na.rm = TRUE
            )
        ),

      power =
        off_rt +
        def_good
    ) %>%
    select(
      team,
      QBID,
      QBname,
      off_rt,
      def_good,
      power
    )


  recent <- train %>%
    group_by(
      team
    ) %>%
    slice_max(
      raw_order,
      n = 1,
      with_ties = FALSE
    ) %>%
    select(
      team,

      recent_QBID =
        QBID,

      recent_QBname =
        QBname
    )


  ratings <- ratings %>%
    left_join(
      recent,
      by =
        "team"
    ) %>%
    mutate(
      QBID =
        ifelse(
          is.na(
            QBID
          ) |
            QBID ==
            "00-0000000",

          recent_QBID,

          QBID
        ),

      QBname =
        ifelse(
          is.na(
            QBname
          ) |
            QBname ==
            "",

          recent_QBname,

          QBname
        )
    )


  if (
    file.exists(
      QB_OVERRIDES
    )
  ) {

    ov <-
      read_csv(
        QB_OVERRIDES,
        show_col_types = FALSE
      )


    if (
      all(
        c(
          "team",
          "QBID"
        ) %in%
          names(
            ov
          )
      )
    ) {

      ratings <- ratings %>%
        left_join(
          ov %>%
            select(
              team,

              ov_id =
                QBID
            ),

          by =
            "team"
        ) %>%
        mutate(
          QBID =
            coalesce(
              ov_id,
              QBID
            )
        ) %>%
        select(
          -ov_id
        )
    }
  }


  ratings %>%
    left_join(
      qb_rt %>%
        select(
          QBID,
          qb_value,
          career_starts
        ),

      by =
        "QBID"
    ) %>%
    mutate(
      qb_value =
        coalesce(
          qb_value,
          be[[
            "qb_A"
          ]]
        ),

      power_final =
        power +
        qb_value,

      hfa =
        be[[
          "is_home"
        ]]
    )
}


# ---- Site display stats ----------------------------------------------------

snapshot_stats <- function(
    feats,
    target_season,
    target_week
) {

  feats %>%
    filter(
      season ==
        target_season,

      week ==
        target_week
    ) %>%
    transmute(
      team,

      off_wepa,

      def_wepa =
        def_wepa_own,

      off_epa =
        off_epa_into_week,

      def_epa =
        def_epa_into_week,

      off_pass_epa =
        off_pass_epa_into_week,

      def_pass_epa =
        def_pass_epa_into_week,

      off_rush_epa =
        off_rush_epa_into_week,

      def_rush_epa =
        def_rush_epa_into_week,

      off_eckel_rate =
        eckel_rate_into_week,

      def_eckel_rate =
        def_eckel,

      off_success =
        succsess_rate_into_week,

      def_success =
        Def_succsess_rate_into_week,

      proe,

      def_proe
    )
}


rk <- function(
    x,
    lower_better = FALSE
) {

  if (lower_better) {

    min_rank(
      x
    )

  } else {

    min_rank(
      desc(
        x
      )
    )

  }
}


# ---- Main ------------------------------------------------------------------

run_main <-
  !nzchar(
    Sys.getenv(
      "BTB_SOURCE_ONLY"
    )
  )


if (run_main) {

  ensure_nflverse()


  out_dir <-
    file.path(
      OUT_ROOT,
      TARGET_SEASON
    )


  dir.create(
    file.path(
      out_dir,
      "weekly"
    ),
    showWarnings = FALSE,
    recursive = TRUE
  )


  sched_all <-
    fetch_schedules_all()


  seasons <-
    (
      TARGET_SEASON -
        HIST_SEASONS
    ):
    TARGET_SEASON


  seasons <-
    seasons[
      seasons >=
        1999
    ]


  sched <-
    sched_all %>%
    filter(
      season %in%
        seasons
    )


  completed <- sched %>%
    filter(
      season ==
        TARGET_SEASON,

      !is.na(
        home_score
      )
    )


  max_completed <-
    if (
      nrow(
        completed
      )
    ) {

      max(
        completed$week
      )

    } else {

      0L

    }


  thru_week <-
    if (
      is.na(
        MAX_WEEK_ARG
      )
    ) {

      max_completed

    } else {

      min(
        MAX_WEEK_ARG,
        max_completed
      )

    }


  pbp_seasons <-
    seasons[
      seasons <=
        TARGET_SEASON
    ]


  pbp <- tryCatch(

    fetch_pbp(
      pbp_seasons
    ),

    error = function(e) {

      msg(
        paste0(
          "load_pbp failed (%s); ",
          "retrying without target season."
        ),
        conditionMessage(e)
      )


      fetch_pbp(
        pbp_seasons[
          pbp_seasons <
            TARGET_SEASON
        ]
      )
    }
  )


  pbp_target_wk <- pbp %>%
    filter(
      season ==
        TARGET_SEASON
    ) %>%
    pull(
      week
    )


  max_pbp_wk <-
    if (
      length(
        pbp_target_wk
      )
    ) {

      max(
        pbp_target_wk
      )

    } else {

      0L

    }


  if (
    max_pbp_wk <
      max_completed
  ) {

    if (
      max_completed >= 1 &&
        max_pbp_wk < 1
    ) {

      stop(
        sprintf(
          paste0(
            "Play-by-play has no %d data while %d completed week(s) exist. ",
            "Data-load failure, not preseason; refusing to publish."
          ),
          TARGET_SEASON,
          max_completed
        ),
        call. = FALSE
      )
    }


    msg(
      paste0(
        "DATA LAG: schedules show week %d complete, ",
        "pbp through week %d. Building through week %d."
      ),
      max_completed,
      max_pbp_wk,
      max_pbp_wk
    )


    thru_week <-
      min(
        thru_week,
        max_pbp_wk
      )
  }


  wepa_weights <-
    load_wepa_weights(
      WEIGHTS_FILE
    )


  have_weights <-
    file.exists(
      WEIGHTS_FILE
    )


  eckel_model <-
    if (
      file.exists(
        ECKEL_FILE
      )
    ) {

      readRDS(
        ECKEL_FILE
      )

    } else {

      msg(
        paste0(
          "Eckel model missing (%s): ",
          "eckel_rate_oe features set to 0."
        ),
        ECKEL_FILE
      )

      NULL
    }


  msg(
    "Building wEPA and features over %d seasons...",
    length(
      pbp_seasons
    )
  )


  wepa_pbp <-
    calculate_wepa(
      pbp,
      wepa_weights
    )


  drives <-
    build_drive_frame(
      pbp,
      eckel_model
    )


  panel_grid <<-
    sched %>%
    group_by(
      season
    ) %>%
    summarize(
      maxw =
        max(
          week
        ),

      .groups =
        "drop"
    ) %>%
    rowwise() %>%
    mutate(
      grid =
        list(
          expand_grid(
            team =
              sort(
                unique(
                  c(
                    sched$home_team[
                      sched$season ==
                        season
                    ],

                    sched$away_team[
                      sched$season ==
                        season
                    ]
                  )
                )
              ),

            week =
              1:
              (
                maxw +
                  1
              )
          )
        )
    ) %>%
    ungroup() %>%
    select(
      season,
      grid
    ) %>%
    unnest(
      grid
    ) %>%
    select(
      team,
      season,
      week
    )


  feats <-
    build_features(
      pbp,
      wepa_pbp,
      drives
    )


  if (
    is.null(
      eckel_model
    )
  ) {

    feats <- feats %>%
      mutate(
        eckel_rate_oe =
          0,

        def_eckel_oe =
          0
      )
  }


  qb10 <-
    build_qb_ma10(
      pbp
    )


  long <-
    build_long(
      sched,
      feats,
      qb10
    )


  tidx <-
    week_index(
      long
    )


  msg(
    "VERIFY register (pending canonical-export diff):"
  )


  for (
    v in
      VERIFY_REGISTER
  ) {

    msg(
      "  - %s",
      v
    )
  }


  weeks <-
    if (
      thru_week >= 1
    ) {

      1:
      (
        thru_week +
          1
      )

    } else {

      1

    }


  history <-
    list()


  for (
    wk in
      weeks
  ) {

    r <-
      fit_power(
        long,
        tidx,
        feats,
        TARGET_SEASON,
        wk
      )


    if (
      is.null(
        r
      )
    ) {

      msg(
        "Week %d: insufficient training data; skipped.",
        wk
      )

      next
    }


    st <-
      snapshot_stats(
        feats,
        TARGET_SEASON,
        wk
      )


    gp <-
      games_entering_week(
        sched,
        TARGET_SEASON,
        wk
      )


    snap <- r %>%
      left_join(
        st,
        by =
          "team"
      ) %>%
      left_join(
        gp,
        by =
          "team"
      ) %>%
      left_join(
        NFL_TEAMS,
        by =
          "team"
      ) %>%
      mutate(
        games =
          coalesce(
            games,
            0L
          ),

        season =
          TARGET_SEASON,

        week =
          wk,

        .before =
          1
      ) %>%
      mutate(
        power_rank =
          rk(
            power_final
          ),

        off_rank =
          rk(
            off_rt
          ),

        def_rank =
          rk(
            def_good
          ),

        off_wepa_rank =
          rk(
            off_wepa
          ),

        def_wepa_rank =
          rk(
            def_wepa,
            TRUE
          ),

        off_epa_rank =
          rk(
            off_epa
          ),

        def_epa_rank =
          rk(
            def_epa,
            TRUE
          ),

        off_pass_epa_rank =
          rk(
            off_pass_epa
          ),

        def_pass_epa_rank =
          rk(
            def_pass_epa,
            TRUE
          ),

        off_rush_epa_rank =
          rk(
            off_rush_epa
          ),

        def_rush_epa_rank =
          rk(
            def_rush_epa,
            TRUE
          ),

        off_eckel_rank =
          rk(
            off_eckel_rate
          ),

        def_eckel_rank =
          rk(
            def_eckel_rate,
            TRUE
          ),

        off_success_rank =
          rk(
            off_success
          ),

        def_success_rank =
          rk(
            def_success,
            TRUE
          )
      )


    history[[length(history) + 1]] <- snap


    msg(
      "Snapshot: entering week %d",
      wk
    )
  }


  hist_df <-
    bind_rows(
      history
    ) %>%
    arrange(
      week,
      power_rank
    ) %>%
    group_by(
      team
    ) %>%
    arrange(
      week,
      .by_group = TRUE
    ) %>%
    mutate(
      power_change =
        power_final -
        lag(
          power_final
        ),

      rank_change =
        lag(
          power_rank
        ) -
        power_rank,

      power_change =
        coalesce(
          power_change,
          0
        ),

      rank_change =
        coalesce(
          rank_change,
          0L
        )
    ) %>%
    ungroup() %>%
    arrange(
      week,
      power_rank
    )


  if (
    !nrow(
      hist_df
    )
  ) {

    stop(
      "No NFL rating snapshots were produced.",
      call. = FALSE
    )
  }


  write_csv(
    hist_df,

    file.path(
      out_dir,
      "ratings_history.csv"
    )
  )


  for (
    wk in
      unique(
        hist_df$week
      )
  ) {

    write_csv(
      hist_df %>%
        filter(
          week ==
            wk
        ),

      file.path(
        out_dir,
        "weekly",

        sprintf(
          "week_%02d.csv",
          wk
        )
      )
    )
  }


  latest <- hist_df %>%
    filter(
      week ==
        max(
          week
        )
    )


  entering_week <-
    max(
      hist_df$week
    )


  write_csv(
    latest,

    file.path(
      out_dir,
      "latest.csv"
    )
  )


  write_json(
    latest,

    file.path(
      out_dir,
      "latest.json"
    ),

    dataframe =
      "rows",

    pretty =
      TRUE,

    na =
      "null"
  )


  write_json(
    list(
      season =
        TARGET_SEASON,

      thru_week =
        thru_week,

      rating_entering_week =
        entering_week,

      generated_utc =
        format(
          Sys.time(),
          tz = "UTC"
        ),

      schedules_completed_week =
        max_completed,

      pbp_max_week =
        max_pbp_wk,

      hfa_points =
        latest$hfa[1],

      wepa_weights_loaded =
        have_weights,

      eckel_model_loaded =
        !is.null(
          eckel_model
        ),

      verify_register =
        VERIFY_REGISTER,

      config =
        list(
          HIST_SEASONS =
            HIST_SEASONS,

          TEAM_HL =
            TEAM_HL,

          QB_DROP_FIRST =
            QB_DROP_FIRST,

          QB_HL =
            QB_HL,

          QB_K =
            QB_K,

          SNAP_REGRESS =
            SNAP_REGRESS
        ),

      column_notes =
        list(
          power_final =
            paste0(
              "team base rating + starter QB value, ",
              "points vs average"
            ),

          power_components =
            paste0(
              "off_rt + def_good = power ",
              "before starter QB value"
            ),

          def_stats =
            paste0(
              "raw defensive per-play/rate stats: ",
              "lower = better; ranks encode direction"
            ),

          wepa =
            paste0(
              "canonical NFL wEPA: per-game rolling average ",
              "of clamped game sums"
            ),

          team_metadata =
            paste0(
              "team_name, conference, division, and logo ",
              "are display fields"
            )
        )
    ),

    file.path(
      out_dir,
      "meta.json"
    ),

    auto_unbox =
      TRUE,

    pretty =
      TRUE
  )


  msg(
    "Done. Ratings entering week %d. Outputs in %s",
    entering_week,
    out_dir
  )
}
