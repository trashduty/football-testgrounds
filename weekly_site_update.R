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
#   Production/current season:
#     Rscript weekly_site_update.R --year=2026
#     Rscript weekly_site_update.R --year=2026 --max-week=6
#
#   Historical QA without a preseason prior:
#     Rscript weekly_site_update.R --year=2025 --min-week=11 --max-week=12 --test-no-prior
#
#   In --test-no-prior mode:
#     * data/preseason_ratings_<year>.csv is NOT required.
#     * preseason weight is forced to 0 for every QA snapshot.
#     * only weeks from --min-week through --max-week are written to history.
#     * Week 12 movement therefore compares directly with Week 11.
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
#     top10_power.png      -- ranked Top 10 BTB Power Rating graphic
#     biggest_risers.png   -- ranked weekly BTB rating gainers graphic
#     biggest_fallers.png  -- ranked weekly BTB rating decliners graphic
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


manual_team_aliases <- c(
  "UTSA"           = "UT San Antonio",
  "UConn"          = "Connecticut",
  "Southern Miss"  = "Southern Mississippi",
  "Sam Houston"    = "Sam Houston State",
  "UL Monroe"      = "Louisiana Monroe",
  "Massachusetts"  = "UMass"
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


.has_flag <- function(flag) {
  flag %in% .args
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

MIN_WEEK_ARG <- suppressWarnings(
  as.integer(
    .get_arg(
      "--min-week",
      1
    )
  )
)

TEST_NO_PRIOR <- .has_flag(
  "--test-no-prior"
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
    season = NA_integer_,
    width = 12,
    height = 10
) {
  required <- c(
    "team",
    "off_pts",
    "def_pts",
    "logo"
  )

  if (!all(required %in% names(df))) {
    msg(
      "Skipping scatter plot: missing columns (%s).",
      paste(
        setdiff(required, names(df)),
        collapse = ", "
      )
    )

    return(invisible(NULL))
  }

  can_image <- ensure_ggimage()

  if (!requireNamespace("ggplot2", quietly = TRUE)) {
    msg(
      "Skipping scatter plot: ggplot2 not available."
    )

    return(invisible(NULL))
  }

  library(ggplot2)

  title_str <- if (!is.na(season)) {
    sprintf(
      "%d BTB Power Rating",
      as.integer(season)
    )
  } else {
    "BTB Power Rating"
  }

  df_plot <- df %>%
    mutate(
      logo_valid =
        is_valid_logo(logo),

      logo_safe =
        if_else(
          logo_valid,
          logo,
          NA_character_
        )
    )

  p <- ggplot(
    df_plot,
    aes(
      x = off_pts,
      y = def_pts
    )
  ) +
    geom_point(
      color = "grey70",
      size = 1.5,
      alpha = 0.4
    ) +
    geom_hline(
      yintercept = 0,
      linetype = "dashed",
      color = "grey50"
    ) +
    geom_vline(
      xintercept = 0,
      linetype = "dashed",
      color = "grey50"
    ) +
    labs(
      title = title_str,
      x = "Offensive Rating (pts vs avg)",
      y = "Defensive Rating (pts prevented vs avg)"
    ) +
    theme_minimal(
      base_size = 14
    )

  if (can_image) {
    library(ggimage)

    p <- p +
      ggimage::geom_image(
        data =
          df_plot %>%
          filter(logo_valid),

        aes(
          image = logo_safe
        ),

        size = 0.05,
        na.rm = TRUE
      )
  }

  no_logo_df <-
    df_plot %>%
    filter(
      !logo_valid
    )

  if (nrow(no_logo_df) > 0) {
    if (!requireNamespace("ggrepel", quietly = TRUE)) {
      p <- p +
        geom_text(
          data = no_logo_df,
          aes(
            label = team
          ),
          size = 2.5,
          color = "grey40"
        )
    } else {
      library(ggrepel)

      p <- p +
        ggrepel::geom_text_repel(
          data = no_logo_df,
          aes(
            label = team
          ),
          size = 2.5,
          color = "grey40"
        )
    }
  }

  out_path <-
    file.path(
      out_dir,
      "btb_scatter.png"
    )

  tryCatch(
    {
      ggsave(
        out_path,
        plot = p,
        width = width,
        height = height,
        dpi = 150
      )

      msg(
        "Scatter plot written to %s",
        out_path
      )
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


# ---- Ranked social graphics -------------------------------------------------
#
# BROWSER NOTE:
#   These files are generated by R:
#     biggest_risers.png
#     biggest_fallers.png
#     top10_power.png
#
#   The website button named "Download Movers PNG" is generated separately
#   in docs/app.js via html2canvas and produces:
#     btb-<season>-weekly-movers.png
#
#   Therefore changes to this R section do not change the browser button PNG.


ranked_graphic_theme <- function() {
  ggplot2::theme_void(
    base_family = "sans"
  ) +
    ggplot2::theme(
      plot.background =
        ggplot2::element_rect(
          fill = "#080808",
          color = NA
        ),

      panel.background =
        ggplot2::element_rect(
          fill = "#080808",
          color = NA
        ),

      plot.title =
        ggplot2::element_text(
          color = "#FFFFFF",
          size = 32,
          face = "bold",
          margin =
            ggplot2::margin(
              b = 4
            )
        ),

      plot.subtitle =
        ggplot2::element_text(
          color = "#F5D400",
          size = 18,
          face = "bold",
          margin =
            ggplot2::margin(
              b = 18
            )
        ),

      plot.margin =
        ggplot2::margin(
          24,
          24,
          24,
          24
        )
    )
}


make_ranked_card_png <- function(
    df,
    out_path,
    title,
    subtitle,
    primary_col,
    primary_suffix = "",
    secondary_builder = NULL,
    top_n = 10,
    direction = c("desc", "asc"),
    digits = 1
) {

  direction <-
    match.arg(
      direction
    )

  if (!requireNamespace("ggplot2", quietly = TRUE)) {
    msg(
      "Skipping ranked graphic %s: ggplot2 not available.",
      out_path
    )

    return(invisible(NULL))
  }

  if (!requireNamespace("ggimage", quietly = TRUE)) {
    msg(
      "Skipping ranked graphic %s: ggimage not available.",
      out_path
    )

    return(invisible(NULL))
  }

  if (!primary_col %in% names(df)) {
    msg(
      "Skipping ranked graphic %s: missing %s.",
      out_path,
      primary_col
    )

    return(invisible(NULL))
  }

  rows <- df %>%
    filter(
      is.finite(
        as.numeric(
          .data[[primary_col]]
        )
      )
    )

  if (direction == "desc") {
    rows <- rows %>%
      arrange(
        desc(
          .data[[primary_col]]
        )
      )
  } else {
    rows <- rows %>%
      arrange(
        .data[[primary_col]]
      )
  }

  rows <- rows %>%
    slice_head(
      n = top_n
    )

  if (!nrow(rows)) {
    msg(
      "Skipping ranked graphic %s: no rows.",
      out_path
    )

    return(invisible(NULL))
  }

  rows <- rows %>%
    mutate(
      list_rank =
        row_number(),

      plot_y =
        rev(
          seq_len(
            n()
          )
        ),

      primary_value =
        as.numeric(
          .data[[primary_col]]
        ),

      primary_text =
        paste0(
          ifelse(
            primary_value > 0,
            "+",
            ""
          ),

          formatC(
            primary_value,
            format = "f",
            digits = digits
          ),

          primary_suffix
        ),

      secondary_text =
        if (
          is.null(
            secondary_builder
          )
        ) {
          ""
        } else {
          secondary_builder(
            cur_data_all()
          )
        },

      logo_valid =
        is_valid_logo(
          logo
        )
    )

  row_height <- 0.82
  xmax <- 14

  p <-
    ggplot2::ggplot(
      rows
    )

  for (i in seq_len(nrow(rows))) {

    y <- rows$plot_y[i]

    p <- p +
      ggplot2::annotate(
        "rect",
        xmin = 0.1,
        xmax = xmax,
        ymin =
          y -
          row_height / 2,

        ymax =
          y +
          row_height / 2,

        fill =
          if (i %% 2 == 1) {
            "#171717"
          } else {
            "#111111"
          },

        color = "#343434",
        linewidth = 0.4
      ) +
      ggplot2::annotate(
        "rect",
        xmin = 0.1,
        xmax = 1.25,
        ymin =
          y -
          row_height / 2,

        ymax =
          y +
          row_height / 2,

        fill = "#F5D400",
        color = NA
      )
  }

  p <- p +
    ggplot2::geom_text(
      ggplot2::aes(
        x = 0.675,
        y = plot_y,
        label = list_rank
      ),
      color = "#050505",
      size = 10,
      fontface = "bold",
      family = "sans"
    ) +
    ggplot2::geom_text(
      ggplot2::aes(
        x = 3.0,
        y =
          plot_y +
          0.11,

        label = team
      ),
      color = "#FFFFFF",
      size = 7.4,
      fontface = "bold",
      hjust = 0,
      family = "sans"
    ) +
    ggplot2::geom_text(
      ggplot2::aes(
        x = 3.0,
        y =
          plot_y -
          0.18,

        label = secondary_text
      ),
      color = "#B8B8B8",
      size = 3.7,
      hjust = 0,
      family = "sans"
    ) +
    ggplot2::geom_text(
      ggplot2::aes(
        x = 13.45,
        y =
          plot_y +
          0.05,

        label = primary_text
      ),
      color = "#FFFFFF",
      size = 6.4,
      fontface = "bold",
      hjust = 1,
      family = "sans"
    )

  logo_rows <- rows %>%
    filter(
      logo_valid
    )

  if (nrow(logo_rows)) {
    p <- p +
      ggimage::geom_image(
        data = logo_rows,

        ggplot2::aes(
          x = 2.0,
          y = plot_y,
          image = logo
        ),

        size = 0.055,
        by = "width",
        asp = 1
      )
  }

  missing_rows <- rows %>%
    filter(
      !logo_valid
    )

  if (nrow(missing_rows)) {
    p <- p +
      ggplot2::geom_text(
        data = missing_rows,

        ggplot2::aes(
          x = 2.0,
          y = plot_y,

          label =
            substr(
              team,
              1,
              3
            )
        ),

        color = "#FFFFFF",
        size = 4,
        fontface = "bold"
      )
  }

  p <- p +
    ggplot2::coord_cartesian(
      xlim =
        c(
          0,
          xmax + 0.2
        ),

      ylim =
        c(
          0.25,
          nrow(rows) + 0.75
        ),

      clip = "off"
    ) +
    ggplot2::labs(
      title = title,
      subtitle = subtitle
    ) +
    ranked_graphic_theme()

  ggplot2::ggsave(
    filename = out_path,
    plot = p,
    width = 10.8,

    height =
      if (nrow(rows) >= 10) {
        13.5
      } else {
        9.0
      },

    units = "in",
    dpi = 150,
    bg = "#080808"
  )

  msg(
    "Ranked graphic written to %s",
    out_path
  )

  invisible(p)
}


write_ranked_exports <- function(
    latest,
    out_dir,
    season
) {

  if (!ensure_ggimage()) {
    msg(
      "Skipping ranked exports: plotting dependencies unavailable."
    )

    return(invisible(NULL))
  }

  top10_secondary <- function(df) {
    paste0(
      "Off ",
      formatC(
        as.numeric(
          df$off_pts
        ),
        format = "f",
        digits = 1
      ),

      "  |  Def ",

      formatC(
        as.numeric(
          df$def_pts
        ),
        format = "f",
        digits = 1
      )
    )
  }

  mover_secondary <- function(df) {

    rank_move <-
      as.numeric(
        df$rank_change
      )

    paste0(
      "#",
      as.integer(
        df$power_rank
      ),

      " overall  |  ",

      ifelse(
        rank_move > 0,

        paste0(
          "▲ ",
          abs(
            as.integer(
              rank_move
            )
          ),
          " rank"
        ),

        ifelse(
          rank_move < 0,

          paste0(
            "▼ ",
            abs(
              as.integer(
                rank_move
              )
            ),
            " rank"
          ),

          "— rank"
        )
      )
    )
  }

  make_ranked_card_png(
    latest,

    file.path(
      out_dir,
      "top10_power.png"
    ),

    sprintf(
      "BTB'S %d POWER RATINGS",
      season
    ),

    "TOP 10 TEAMS",

    primary_col = "power_pts",
    primary_suffix = " BTB",
    secondary_builder = top10_secondary,
    top_n = 10,
    direction = "desc",
    digits = 1
  )

  mover_rows <- latest %>%
    filter(
      is.finite(
        as.numeric(
          power_change
        )
      ),
      week > 0
    )

  risers <- mover_rows %>%
    filter(
      power_change > 0
    )

  fallers <- mover_rows %>%
    filter(
      power_change < 0
    )

  make_ranked_card_png(
    risers,

    file.path(
      out_dir,
      "biggest_risers.png"
    ),

    sprintf(
      "BTB'S %d WEEKLY MOVERS",
      season
    ),

    "BIGGEST RISERS",

    primary_col = "power_change",
    primary_suffix = " BTB pts",
    secondary_builder = mover_secondary,
    top_n = 10,
    direction = "desc",
    digits = 1
  )

  make_ranked_card_png(
    fallers,

    file.path(
      out_dir,
      "biggest_fallers.png"
    ),

    sprintf(
      "BTB'S %d WEEKLY MOVERS",
      season
    ),

    "BIGGEST FALLERS",

    primary_col = "power_change",
    primary_suffix = " BTB pts",
    secondary_builder = mover_secondary,
    top_n = 10,
    direction = "asc",
    digits = 1
  )

  invisible(NULL)
}


# ---- Data pulls -------------------------------------------------------------

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

  crosswalk_file <-
    "CFB Teams Full Crosswalk.csv"

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
    mutate(
      logo =
        as.character(
          logo
        ),

      logo =
        str_trim(
          logo
        ),

      logo =
        str_replace(
          logo,
          "^http://",
          "https://"
        ),

      logo =
        if_else(
          is_valid_logo(logo),
          logo,
          NA_character_
        )
    )

  candidate_cols <- intersect(
    c(
      "cfbfastr_team",
      "api_team",
      "btb_team",
      "btb_team_short"
    ),
    names(crosswalk)
  )

  crosswalk_lookup <-
    map_dfr(
      candidate_cols,

      function(col) {
        crosswalk %>%
          filter(
            !is.na(
              .data[[col]]
            ),

            as.character(
              .data[[col]]
            ) != "",

            !is.na(logo)
          ) %>%
          transmute(
            join_key =
              normalize_team_key(
                as.character(
                  .data[[col]]
                )
              ),

            logo = logo
          )
      }
    ) %>%
    distinct(
      join_key,
      .keep_all = TRUE
    )

  alias_lookup <- tibble(
    alias_key =
      normalize_team_key(
        names(
          manual_team_aliases
        )
      ),

    btb_team_short_key =
      normalize_team_key(
        unname(
          manual_team_aliases
        )
      )
  )

  crosswalk_short_lookup <-
    crosswalk %>%
    filter(
      !is.na(
        btb_team_short
      ),

      as.character(
        btb_team_short
      ) != "",

      !is.na(
        logo
      )
    ) %>%
    transmute(
      btb_team_short_key =
        normalize_team_key(
          as.character(
            btb_team_short
          )
        ),

      alias_logo =
        logo
    ) %>%
    distinct(
      btb_team_short_key,
      .keep_all = TRUE
    )

  alias_lookup <-
    alias_lookup %>%
    left_join(
      crosswalk_short_lookup,
      by = "btb_team_short_key"
    ) %>%
    filter(
      !is.na(
        alias_logo
      )
    ) %>%
    select(
      alias_key,
      alias_logo
    )

  out <- t %>%
    transmute(
      team =
        as.character(
          school
        ),

      conference
    ) %>%
    mutate(
      team =
        str_trim(
          team
        ),

      join_key =
        normalize_team_key(
          team
        )
    ) %>%
    left_join(
      crosswalk_lookup,
      by = "join_key"
    ) %>%
    left_join(
      alias_lookup,
      by =
        c(
          "join_key" =
            "alias_key"
        )
    ) %>%
    mutate(
      logo =
        if_else(
          !is_valid_logo(logo) &
            is_valid_logo(
              alias_logo
            ),

          alias_logo,
          logo
        )
    ) %>%
    select(
      -join_key,
      -alias_logo
    )

  missing_logos <- out %>%
    filter(
      !is_valid_logo(
        logo
      )
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
    timeout =
      max(
        1800,
        getOption("timeout")
      )
  )

  on.exit(
    options(old_to),
    add = TRUE
  )

  pbp <- tryCatch(
    cfbfastR::load_cfb_pbp(
      season
    ),

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
      season =
        if (
          "season" %in%
            names(.)
        ) {
          coalesce(
            season,
            year
          )
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
    min(
      pbp$week,
      na.rm = TRUE
    ),
    max(
      pbp$week,
      na.rm = TRUE
    )
  )

  pbp
}


# ---- wEPA -------------------------------------------------------------------

tag_wepa_features <- function(pbp) {

  tagged <- pbp %>%
    mutate(
      passing_epa =
        ifelse(
          pass == 1,
          1,
          0
        ),

      passing_ex_int_epa =
        ifelse(
          pass == 1 &
            !grepl(
              "Interception",
              play_type
            ),
          1,
          0
        ),

      rush_epa =
        ifelse(
          rush == 1,
          1,
          0
        ),

      pos_rush_epa =
        ifelse(
          rush == 1 &
            EPA > 0,
          1,
          0
        ),

      neg_rush_epa =
        ifelse(
          rush == 1 &
            EPA <= 0,
          1,
          0
        ),

      short_yd_rush_epa =
        ifelse(
          rush == 1 &
            distance < 5,
          1,
          0
        ),

      long_yd_rush_epa =
        ifelse(
          rush == 1 &
            distance >= 5,
          1,
          0
        ),

      qb_rush_epa =
        ifelse(
          rush == 1 &
            position_rush != "QB",
          1,
          0
        ),

      completed_passing_epa =
        ifelse(
          play_type %in%
            c(
              "Pass Reception",
              "Passing Touchdown"
            ),
          1,
          0
        ),

      incompleted_passing_epa =
        ifelse(
          play_type ==
            "Pass Incompletion",
          1,
          0
        ),

      off_holding_epa =
        ifelse(
          penalty_detail ==
            "Offensive Holding",
          1,
          0
        ),

      defensive_pi_epa =
        ifelse(
          penalty_detail ==
            "Pass Interference" &
            EPA > 0,
          1,
          0
        ),

      false_start_epa =
        ifelse(
          penalty_detail ==
            "False Start",
          1,
          0
        ),

      roughing_epa =
        ifelse(
          penalty_detail ==
            "Roughing the Passer",
          1,
          0
        ),

      defensive_holding_epa =
        ifelse(
          penalty_detail ==
            "Defensive Holding",
          1,
          0
        ),

      offensive_unnecessary_roughness_epa =
        ifelse(
          penalty_detail %in%
            c(
              "Unnecessary Roughness"
            ) &
            EPA < 0,
          1,
          0
        ),

      offensive_unsportsmanlike_epa =
        ifelse(
          penalty_detail %in%
            c(
              "Unsportsmanlike Conduct"
            ) &
            EPA < 0,
          1,
          0
        ),

      defensive_unnecessary_roughness_epa =
        ifelse(
          penalty_detail %in%
            c(
              "Unnecessary Roughness"
            ) &
            EPA >= 0,
          1,
          0
        ),

      defensive_unsportsmanlike_epa =
        ifelse(
          penalty_detail %in%
            c(
              "Unsportsmanlike Conduct"
            ) &
            EPA >= 0,
          1,
          0
        ),

      offensive_pi_epa =
        ifelse(
          penalty_detail ==
            "Pass Interference" &
            EPA <= 0,
          1,
          0
        ),

      off_hold_or_false_start_epa =
        ifelse(
          penalty_detail %in%
            c(
              "Offensive Holding",
              "False Start"
            ),
          1,
          0
        ),

      sack_epa =
        ifelse(
          sack == 1,
          1,
          0
        ),

      fumble_epa =
        ifelse(
          fumble_vec == 1,
          1,
          0
        ),

      sack_fumble_epa =
        ifelse(
          sack == 1 &
            fumble_vec == 1,
          1,
          0
        ),

      non_sack_fumble_epa =
        ifelse(
          sack == 0 &
            fumble_vec == 1,
          1,
          0
        ),

      non_fumble_sack_epa =
        ifelse(
          sack == 1 &
            fumble_vec == 0,
          1,
          0
        ),

      int_epa =
        ifelse(
          pass == 1 &
            grepl(
              "Interception",
              play_type
            ),
          1,
          0
        ),

      return_td_epa =
        ifelse(
          (
            grepl(
              "Return",
              play_type
            ) |
              grepl(
                "Recovery",
                play_type
              )
          ) &
            grepl(
              "Touchdown",
              play_type
            ),
          1,
          0
        ),

      punt_epa =
        ifelse(
          grepl(
            "Punt",
            play_type
          ),
          1,
          0
        ),

      blocked_punt_epa =
        ifelse(
          grepl(
            "Blocked Punt",
            play_type
          ),
          1,
          0
        ),

      fg_epa =
        ifelse(
          grepl(
            "Field Goal",
            play_type
          ),
          1,
          0
        ),

      kickoff_epa =
        ifelse(
          grepl(
            "Kickoff",
            play_type
          ),
          1,
          0
        ),

      first_down_epa =
        ifelse(
          down == 1,
          1,
          0
        ),

      second_down_epa =
        ifelse(
          down == 2,
          1,
          0
        ),

      third_down_epa =
        ifelse(
          down == 3,
          1,
          0
        ),

      fourth_down_epa =
        ifelse(
          (
            rush == 1 |
              pass == 1
          ) &
            down == 4,
          1,
          0
        ),

      third_down_ex_sack_int_epa =
        ifelse(
          down == 3 &
            !grepl(
              "Interception",
              play_type
            ) &
            !grepl(
              "Sack",
              play_type
            ),
          1,
          0
        ),

      third_down_pos_epa =
        ifelse(
          down == 3 &
            EPA > 0,
          1,
          0
        ),

      third_down_long_ex_sack_int_epa =
        ifelse(
          down == 3 &
            distance > 5 &
            !grepl(
              "Interception",
              play_type
            ) &
            !grepl(
              "Sack",
              play_type
            ),
          1,
          0
        ),

      first_down_rush_epa =
        ifelse(
          down == 1 &
            rush == 1,
          1,
          0
        ),

      second_down_rush_epa =
        ifelse(
          down == 2 &
            rush == 1,
          1,
          0
        ),

      third_down_rush_epa =
        ifelse(
          down == 3 &
            rush == 1,
          1,
          0
        ),

      fourth_down_rush_epa =
        ifelse(
          down == 4 &
            rush == 1,
          1,
          0
        ),

      first_down_pass_epa =
        ifelse(
          down == 1 &
            pass == 1,
          1,
          0
        ),

      second_down_pass_epa =
        ifelse(
          down == 2 &
            pass == 1,
          1,
          0
        ),

      third_down_pass_epa =
        ifelse(
          down == 3 &
            pass == 1,
          1,
          0
        ),

      fourth_down_pass_epa =
        ifelse(
          down == 4 &
            pass == 1,
          1,
          0
        ),

      neutral_second_down_rush_epa =
        ifelse(
          down == 2 &
            rush == 1 &
            wp_before > 0.05 &
            wp_after < 0.95,
          1,
          0
        ),

      early_down_rush_epa =
        ifelse(
          down <= 2,
          1,
          0
        ),

      early_down_sack_epa =
        ifelse(
          down <= 2 &
            sack == 1 &
            fumble_vec == 0,
          1,
          0
        ),

      red_zone_epa =
        ifelse(
          yards_to_goal <= 20,
          1,
          0
        ),

      goal_to_go_epa =
        ifelse(
          Goal_To_Go == TRUE,
          1,
          0
        ),

      goalline_epa =
        ifelse(
          yards_to_goal <= 3,
          1,
          0
        ),

      plus_territory_epa =
        ifelse(
          yards_to_goal <= 50,
          1,
          0
        ),

      low_wp_epa =
        ifelse(
          (
            wp_before <= 0.05 |
              wp_before >= 0.95
          ),
          1,
          0
        ),

      garbage_time_epa =
        ifelse(
          (
            score_diff >=
              GT_THRESH["1"] &
              period == 1
          ) |
            (
              score_diff >=
                GT_THRESH["2"] &
                period == 2
            ) |
            (
              score_diff >=
                GT_THRESH["3"] &
                period == 3
            ) |
            (
              score_diff >=
                GT_THRESH["4"] &
                period == 4
            ),
          1,
          0
        ),

      asym_low_wp_epa =
        ifelse(
          (
            wp_before <= 0.2 |
              wp_before >= 0.95
          ),
          1,
          0
        ),

      asym_garbage_time_epa =
        ifelse(
          (
            wp_before <= 0.2 |
              wp_before >= 0.95
          ) &
            period == 4,
          1,
          0
        ),

      offense_home_epa =
        ifelse(
          pos_team == home,
          1,
          0
        ),

      offense_away_epa =
        ifelse(
          pos_team == away,
          1,
          0
        )
    ) %>%
    rename_with(
      ~ paste0(
        str_remove(
          .x,
          "_epa"
        ),
        "_weight"
      ),
      ends_with(
        "_epa",
        ignore.case = FALSE
      )
    ) %>%
    mutate(
      across(
        ends_with(
          "_weight"
        ),
        ~ .x,
        .names = "def_{.col}"
      )
    ) %>%
    rename_with(
      ~ paste0(
        "off_",
        .x
      ),
      (
        ends_with(
          "_weight",
          ignore.case = FALSE
        ) &
          !starts_with(
            "def_",
            ignore.case = FALSE
          )
      )
    ) %>%
    select(
      ends_with(
        "_weight"
      )
    )

  tagged
}


apply_wepa_weights <- function(
    tagged,
    epa_vec,
    model_weights
) {

  if (is.null(model_weights)) {
    return(
      tibble(
        off_wepa = epa_vec,
        def_wepa = epa_vec
      )
    )
  }

  if (
    !is.null(
      names(
        model_weights
      )
    ) &&
      all(
        nzchar(
          names(
            model_weights
          )
        )
      )
  ) {

    missing_w <- setdiff(
      names(tagged),
      names(model_weights)
    )

    if (length(missing_w)) {
      stop(
        "wEPA weights file lacks entries for: ",
        paste(
          missing_w,
          collapse = ", "
        ),
        call. = FALSE
      )
    }

    model_weights <-
      model_weights[
        names(tagged)
      ]

  } else if (
    length(model_weights) !=
      ncol(tagged)
  ) {

    stop(
      sprintf(
        "wEPA weights length (%d) != tagged feature count (%d).",
        length(model_weights),
        ncol(tagged)
      ),
      call. = FALSE
    )

  } else {

    warning(
      paste0(
        "wEPA weights are unnamed; ",
        "relying on canonical column order."
      )
    )
  }

  tagged %>%
    map2(
      model_weights,
      `*`
    ) %>%
    bind_cols() %>%
    mutate(
      across(
        ends_with(
          "_weight"
        ),
        ~ as.numeric(
          replace_na(
            .x,
            0
          ) + 1
        )
      ),

      epa =
        epa_vec,

      off_wepa =
        pmap_dbl(
          pick(
            starts_with(
              "off_"
            ),
            epa
          ),
          prod
        ),

      def_wepa =
        pmap_dbl(
          pick(
            starts_with(
              "def_"
            ),
            epa
          ),
          prod
        )
    ) %>%
    select(
      off_wepa,
      def_wepa
    )
}


# ---- Eckel ------------------------------------------------------------------

build_drives <- function(
    pbp,
    eckel_model = NULL
) {

  drives <- pbp %>%
    arrange(
      game_id,
      drive_id,
      id_play
    ) %>%
    mutate(
      eck_ind_no_td =
        ifelse(
          down == 1 &
            yards_to_goal < 40,
          1,
          0
        )
    ) %>%
    group_by(
      game_id,
      week,
      drive_id,
      pos_team,
      def_pos_team
    ) %>%
    summarise(
      across(
        c(
          new_drive_pts,
          TimeSecsRem,
          adj_TimeSecsRem,
          yards_to_goal,
          drive_end_yards_to_goal,
          pos_team_timeouts,
          def_pos_team_timeouts,
          drive_result,
          period
        ),
        ~ .[1]
      ),

      eck_ind_no_td =
        ifelse(
          sum(
            eck_ind_no_td,
            na.rm = TRUE
          ) > 0,
          1,
          0
        ),

      .groups = "drop"
    ) %>%
    rename(
      start_yards_to_goal =
        yards_to_goal,

      half_secs_rem =
        TimeSecsRem,

      game_secs_rem =
        adj_TimeSecsRem,

      drive_start_period =
        period
    ) %>%
    filter(
      start_yards_to_goal > 40,
      !is.na(
        pos_team_timeouts
      ),
      !is.na(
        def_pos_team_timeouts
      )
    ) %>%
    group_by(
      game_id
    ) %>%
    mutate(
      prev_drive_result =
        lag(
          drive_result
        )
    ) %>%
    ungroup() %>%
    mutate(
      eckel =
        case_when(
          new_drive_pts >= 6 ~ 1,
          eck_ind_no_td == 1 ~ 1,
          TRUE ~ 0
        )
    )

  if (!is.null(eckel_model)) {

    drives <- drives %>%
      mutate(
        eckel_prediction =
          suppressWarnings(
            predict.glm(
              eckel_model,
              .,
              type = "response"
            )
          ),

        eckel_oe =
          eckel -
          eckel_prediction
      )

  } else {

    drives <- drives %>%
      mutate(
        eckel_prediction =
          NA_real_,

        eckel_oe =
          NA_real_
      )
  }

  drives
}


# ---- Team stats -------------------------------------------------------------

team_week_stats <- function(
    pbp_aug,
    drives,
    fbs_teams,
    thru_week
) {

  p <- pbp_aug %>%
    filter(
      week <= thru_week,
      !is.na(ppa)
    )

  d <- drives %>%
    filter(
      week <= thru_week
    )

  off <- p %>%
    filter(
      offense_play %in%
        fbs_teams
    ) %>%
    group_by(
      team =
        offense_play
    ) %>%
    summarise(
      off_wepa =
        mean(
          off_wepa,
          na.rm = TRUE
        ),

      off_pass_epa =
        mean(
          ppa[
            pass == 1
          ],
          na.rm = TRUE
        ),

      off_rush_epa =
        mean(
          ppa[
            rush == 1
          ],
          na.rm = TRUE
        ),

      .groups = "drop"
    )

  def <- p %>%
    filter(
      defense_play %in%
        fbs_teams
    ) %>%
    group_by(
      team =
        defense_play
    ) %>%
    summarise(
      def_wepa =
        mean(
          def_wepa,
          na.rm = TRUE
        ),

      def_pass_epa =
        mean(
          ppa[
            pass == 1
          ],
          na.rm = TRUE
        ),

      def_rush_epa =
        mean(
          ppa[
            rush == 1
          ],
          na.rm = TRUE
        ),

      .groups = "drop"
    )

  eck_off <- d %>%
    filter(
      pos_team %in%
        fbs_teams
    ) %>%
    group_by(
      team =
        pos_team
    ) %>%
    summarise(
      off_eckel_rate =
        mean(
          eckel,
          na.rm = TRUE
        ),

      off_eckel_rate_oe =
        mean(
          eckel_oe,
          na.rm = TRUE
        ),

      .groups = "drop"
    )

  eck_def <- d %>%
    filter(
      def_pos_team %in%
        fbs_teams
    ) %>%
    group_by(
      team =
        def_pos_team
    ) %>%
    summarise(
      def_eckel_rate =
        mean(
          eckel,
          na.rm = TRUE
        ),

      def_eckel_rate_oe =
        mean(
          eckel_oe,
          na.rm = TRUE
        ),

      .groups = "drop"
    )

  reduce(
    list(
      off,
      def,
      eck_off,
      eck_def
    ),
    full_join,
    by = "team"
  )
}


# ---- Opponent adjustment ----------------------------------------------------

fit_adjustment <- function(
    model_df,
    label
) {

  fit <- tryCatch(
    lmer(
      y ~
        off_home +
        (1 | offense) +
        (1 | defense),

      data =
        model_df,

      weights =
        wt,

      REML =
        TRUE,

      control =
        lmerControl(
          check.conv.singular =
            "ignore",

          calc.derivs =
            FALSE
        )
    ),

    error = function(e) {

      msg(
        paste0(
          "[%s] lmer failed (%s); ",
          "falling back to raw centered means."
        ),
        label,
        conditionMessage(e)
      )

      NULL
    }
  )

  if (is.null(fit)) {

    off <- model_df %>%
      group_by(
        offense
      ) %>%
      summarise(
        v =
          weighted.mean(
            y,
            wt,
            na.rm = TRUE
          ),

        .groups =
          "drop"
      ) %>%
      mutate(
        v =
          v -
          mean(
            v,
            na.rm = TRUE
          )
      )

    def <- model_df %>%
      group_by(
        defense
      ) %>%
      summarise(
        v =
          weighted.mean(
            y,
            wt,
            na.rm = TRUE
          ),

        .groups =
          "drop"
      ) %>%
      mutate(
        v =
          v -
          mean(
            v,
            na.rm = TRUE
          )
      )

    return(
      list(
        off =
          off %>%
          transmute(
            team =
              offense,

            off_adj =
              v
          ),

        def =
          def %>%
          transmute(
            team =
              defense,

            def_adj =
              v
          ),

        hfa =
          NA_real_
      )
    )
  }

  re <- ranef(fit)

  list(
    off =
      tibble(
        team =
          rownames(
            re$offense
          ),

        off_adj =
          re$offense[[1]]
      ),

    def =
      tibble(
        team =
          rownames(
            re$defense
          ),

        def_adj =
          re$defense[[1]]
      ),

    hfa =
      tryCatch(
        unname(
          fixef(fit)[
            "off_home"
          ]
        ),

        error =
          function(e)
            NA_real_
      )
  )
}


# ---- Preseason prior --------------------------------------------------------

load_prior <- function(
    path,
    teams_tbl
) {

  fbs <-
    teams_tbl$team

  if (!file.exists(path)) {
    stop(
      sprintf(
        paste0(
          "Required preseason prior file is missing: %s. ",
          "Week 0 must use the preseason baseline, and Weeks 1+ ",
          "blend that baseline with current-season data."
        ),
        path
      ),
      call. = FALSE
    )
  }

  raw <- read_csv(
    path,
    show_col_types = FALSE
  )

  team_col <- intersect(
    c(
      "team",
      "school",
      "Team",
      "TEAM"
    ),
    names(raw)
  )[1]

  power_col <- intersect(
    c(
      "power_pts",
      "power",
      "power_rating",
      "rating",
      "btb_power",
      "preseason_power"
    ),
    names(raw)
  )[1]

  if (
    is.na(team_col) ||
    is.na(power_col)
  ) {
    stop(
      "Preseason file needs a team column and a power column. Found: ",
      paste(
        names(raw),
        collapse = ", "
      ),
      call. = FALSE
    )
  }

  off_col <- intersect(
    c(
      "off_pts",
      "off",
      "off_rating"
    ),
    names(raw)
  )[1]

  def_col <- intersect(
    c(
      "def_pts",
      "def",
      "def_rating"
    ),
    names(raw)
  )[1]

  pri <- raw %>%
    transmute(
      team =
        recode(
          .data[[team_col]],
          !!!PRIOR_NAME_RECODE
        ),

      power =
        as.numeric(
          .data[[power_col]]
        ),

      off =
        if (!is.na(off_col)) {
          as.numeric(
            .data[[off_col]]
          )
        } else {
          NA_real_
        },

      def =
        if (!is.na(def_col)) {
          as.numeric(
            .data[[def_col]]
          )
        } else {
          NA_real_
        }
    )

  unmatched <- setdiff(
    pri$team,
    fbs
  )

  if (length(unmatched)) {
    msg(
      "PRESEASON TEAMS NOT MATCHED TO CFBD NAMES: %s",
      paste(
        unmatched,
        collapse = ", "
      )
    )
  }

  pri <- pri %>%
    filter(
      team %in%
        fbs
    )

  pri <- pri %>%
    mutate(
      power =
        power -
        mean(
          power,
          na.rm = TRUE
        )
    )

  scl <- 1

  if (isTRUE(STANDARDIZE_PRIOR)) {

    s <- sd(
      pri$power,
      na.rm = TRUE
    )

    if (
      is.finite(s) &&
      s > 0
    ) {
      scl <-
        TARGET_SD /
        s
    }
  }

  pri <- pri %>%
    mutate(
      power =
        power *
        scl,

      off =
        if (
          all(
            is.na(off)
          )
        ) {
          power / 2
        } else {
          (
            off -
            mean(
              off,
              na.rm = TRUE
            )
          ) *
            scl
        },

      def =
        if (
          all(
            is.na(def)
          )
        ) {
          power / 2
        } else {
          (
            def -
            mean(
              def,
              na.rm = TRUE
            )
          ) *
            scl
        },

      power =
        off +
        def
    )

  if (
    is.na(off_col) ||
    is.na(def_col)
  ) {
    msg(
      paste0(
        "Preseason file has no off/def split; ",
        "splitting the prior 50/50."
      )
    )
  }

  missing <- setdiff(
    fbs,
    pri$team
  )

  if (length(missing)) {

    fill <- quantile(
      pri$power,
      NEW_TEAM_PRIOR_Q,
      na.rm = TRUE,
      names = FALSE
    )

    msg(
      paste0(
        "Teams missing from preseason file get the ",
        "%d%%ile prior (%.1f): %s"
      ),

      round(
        100 *
          NEW_TEAM_PRIOR_Q
      ),

      fill,

      paste(
        missing,
        collapse = ", "
      )
    )

    pri <- bind_rows(
      pri,

      tibble(
        team = missing,
        power = fill,
        off = fill / 2,
        def = fill / 2
      )
    )
  }

  pri %>%
    transmute(
      team,
      prior_power = power,
      prior_off = off,
      prior_def = def
    )
}


make_neutral_prior <- function(
    teams_tbl
) {

  teams_tbl %>%
    transmute(
      team,
      prior_power = 0,
      prior_off = 0,
      prior_def = 0
    )
}


prior_weight <- function(
    games_played
) {

  pmax(
    0,
    1 -
      games_played /
        PRIOR_G_FULL
  ) ^
    PRIOR_POW
}


# ---- Week 0 -----------------------------------------------------------------

make_week0_snapshot <- function(
    teams_tbl,
    prior
) {

  teams_tbl %>%
    left_join(
      prior,
      by = "team"
    ) %>%
    transmute(
      season =
        TARGET_SEASON,

      week =
        0L,

      team,
      conference,
      logo,

      games =
        0L,

      prior_weight =
        1,

      power_pts =
        prior_power,

      off_pts =
        prior_off,

      def_pts =
        prior_def,

      power_rank =
        min_rank(
          desc(
            power_pts
          )
        ),

      off_rank =
        min_rank(
          desc(
            off_pts
          )
        ),

      def_rank =
        min_rank(
          desc(
            def_pts
          )
        )
    )
}


# ---- Weekly snapshot --------------------------------------------------------

snapshot_week <- function(
    w,
    pbp_aug,
    drives,
    games,
    teams_tbl,
    prior,
    have_weights
) {

  fbs <-
    teams_tbl$team

  gp <- games %>%
    filter(
      completed,
      week <= w
    ) %>%
    select(
      home_team,
      away_team
    ) %>%
    pivot_longer(
      everything(),
      values_to = "team"
    ) %>%
    filter(
      team %in%
        fbs
    ) %>%
    count(
      team,
      name = "games"
    )

  mdl <- pbp_aug %>%
    filter(
      week <= w,

      rush == 1 |
        pass == 1,

      !is.na(
        off_wepa
      )
    ) %>%
    transmute(
      offense =
        ifelse(
          offense_play %in%
            fbs,
          offense_play,
          "NON_FBS"
        ),

      defense =
        ifelse(
          defense_play %in%
            fbs,
          defense_play,
          "NON_FBS"
        ),

      off_home =
        case_when(
          isTRUE_v(
            neutral_site
          ) ~ 0,

          offense_play ==
            home ~ 1,

          TRUE ~ -1
        ),

      y =
        off_wepa,

      y_epa =
        ppa,

      wt =
        RECENCY_DECAY ^
          (
            w -
              week
          ),

      is_pass =
        pass == 1,

      is_rush =
        rush == 1,

      garbage =
        garbage_flag
    )

  if (!have_weights) {
    mdl <- mdl %>%
      filter(
        !garbage
      )
  }

  lam <- {

    cc <-
      !is.na(
        mdl$y
      ) &
      !is.na(
        mdl$y_epa
      )

    s_w <- sd(
      mdl$y[cc]
    )

    s_e <- sd(
      mdl$y_epa[cc]
    )

    if (
      is.finite(s_w) &&
      is.finite(s_e) &&
      s_w > 0
    ) {
      s_e / s_w
    } else {
      1
    }
  }

  adj_all <- fit_adjustment(
    mdl,

    sprintf(
      "wk%02d all",
      w
    )
  )

  adj_rush <- fit_adjustment(
    mdl %>%
      filter(
        is_rush,
        !is.na(y_epa)
      ) %>%
      mutate(
        y =
          y_epa
      ),

    sprintf(
      "wk%02d rush",
      w
    )
  )

  adj_pass <- fit_adjustment(
    mdl %>%
      filter(
        is_pass,
        !is.na(y_epa)
      ) %>%
      mutate(
        y =
          y_epa
      ),

    sprintf(
      "wk%02d pass",
      w
    )
  )

  stats <- team_week_stats(
    pbp_aug,
    drives,
    fbs,
    w
  )

  out <- teams_tbl %>%
    left_join(
      gp,
      by = "team"
    ) %>%
    mutate(
      games =
        coalesce(
          games,
          0L
        )
    ) %>%
    left_join(
      adj_all$off,
      by = "team"
    ) %>%
    left_join(
      adj_all$def,
      by = "team"
    ) %>%
    left_join(
      adj_rush$off %>%
        rename(
          adj_off_rush_epa =
            off_adj
        ),
      by = "team"
    ) %>%
    left_join(
      adj_rush$def %>%
        rename(
          adj_def_rush_epa =
            def_adj
        ),
      by = "team"
    ) %>%
    left_join(
      adj_pass$off %>%
        rename(
          adj_off_pass_epa =
            off_adj
        ),
      by = "team"
    ) %>%
    left_join(
      adj_pass$def %>%
        rename(
          adj_def_pass_epa =
            def_adj
        ),
      by = "team"
    ) %>%
    left_join(
      prior,
      by = "team"
    ) %>%
    left_join(
      stats,
      by = "team"
    ) %>%
    mutate(
      data_off_pts =
        coalesce(
          off_adj,
          0
        ) *
          lam *
          PLAYS_SCALE,

      data_def_pts =
        -coalesce(
          def_adj,
          0
        ) *
          lam *
          PLAYS_SCALE,

      prior_weight =
        if (TEST_NO_PRIOR) {
          0
        } else {
          prior_weight(
            games
          )
        },

      off_pts =
        prior_weight *
          prior_off +
          (
            1 -
              prior_weight
          ) *
            data_off_pts,

      def_pts =
        prior_weight *
          prior_def +
          (
            1 -
              prior_weight
          ) *
            data_def_pts,

      power_pts =
        off_pts +
          def_pts,

      season =
        TARGET_SEASON,

      week =
        w
    ) %>%
    mutate(
      power_rank =
        min_rank(
          desc(
            power_pts
          )
        ),

      off_rank =
        min_rank(
          desc(
            off_pts
          )
        ),

      def_rank =
        min_rank(
          desc(
            def_pts
          )
        ),

      off_wepa_rank =
        min_rank(
          desc(
            off_wepa
          )
        ),

      off_pass_epa_rank =
        min_rank(
          desc(
            off_pass_epa
          )
        ),

      off_rush_epa_rank =
        min_rank(
          desc(
            off_rush_epa
          )
        ),

      off_eckel_rate_rank =
        min_rank(
          desc(
            off_eckel_rate
          )
        ),

      def_wepa_rank =
        min_rank(
          def_wepa
        ),

      def_pass_epa_rank =
        min_rank(
          def_pass_epa
        ),

      def_rush_epa_rank =
        min_rank(
          def_rush_epa
        ),

      def_eckel_rate_rank =
        min_rank(
          def_eckel_rate
        )
    ) %>%
    select(
      season,
      week,
      team,
      conference,
      logo,
      games,
      prior_weight,

      power_pts,
      off_pts,
      def_pts,

      power_rank,
      off_rank,
      def_rank,

      off_wepa,
      off_pass_epa,
      off_rush_epa,
      off_eckel_rate,
      off_eckel_rate_oe,

      def_wepa,
      def_pass_epa,
      def_rush_epa,
      def_eckel_rate,
      def_eckel_rate_oe,

      adj_off_rush_epa,
      adj_def_rush_epa,
      adj_off_pass_epa,
      adj_def_pass_epa,

      ends_with(
        "_rank"
      )
    )

  attr(
    out,
    "hfa_epa"
  ) <-
    adj_all$hfa *
      lam

  attr(
    out,
    "wepa_scale"
  ) <-
    lam

  out
}


# ---- Main -------------------------------------------------------------------

run_main <- !nzchar(
  Sys.getenv(
    "BTB_SOURCE_ONLY"
  )
)


if (run_main) {

  if (
    !nzchar(
      Sys.getenv(
        "CFBD_API_KEY"
      )
    )
  ) {
    stop(
      paste0(
        "CFBD_API_KEY is not set. ",
        "Add it as a repository secret and export it in the workflow env."
      ),
      call. = FALSE
    )
  }

  ensure_cfbfastR()

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
    recursive = TRUE,
    showWarnings = FALSE
  )

  teams_tbl <-
    fetch_teams(
      TARGET_SEASON
    )

  report_logo_quality(
    teams_tbl,
    out_dir
  )

  games <-
    fetch_games(
      TARGET_SEASON
    )

  prior <-
    if (TEST_NO_PRIOR) {

      msg(
        paste0(
          "QA MODE: --test-no-prior enabled. ",
          "Using a neutral zero prior and forcing prior_weight = 0."
        )
      )

      make_neutral_prior(
        teams_tbl
      )

    } else {

      load_prior(
        PRESEASON_FILE,
        teams_tbl
      )
    }

  model_weights <-
    if (
      file.exists(
        MODEL_WEIGHTS_FILE
      )
    ) {
      read_rds(
        MODEL_WEIGHTS_FILE
      )
    } else {

      msg(
        paste0(
          "wEPA WEIGHTS FILE MISSING (%s): ",
          "using raw EPA fallback."
        ),
        MODEL_WEIGHTS_FILE
      )

      NULL
    }

  eckel_model <-
    if (
      file.exists(
        ECKEL_MODEL_FILE
      )
    ) {
      read_rds(
        ECKEL_MODEL_FILE
      )
    } else {

      msg(
        paste0(
          "Eckel model file missing (%s): ",
          "*_eckel_rate_oe will be NA."
        ),
        ECKEL_MODEL_FILE
      )

      NULL
    }

  pbp <-
    fetch_pbp(
      TARGET_SEASON
    )

  completed_wk <- games %>%
    filter(
      completed
    ) %>%
    pull(
      week
    )

  max_completed <-
    if (
      length(
        completed_wk
      )
    ) {
      max(
        completed_wk
      )
    } else {
      0L
    }

  max_pbp_wk <-
    if (
      !is.null(
        pbp
      )
    ) {
      max(
        pbp$week,
        na.rm = TRUE
      )
    } else {
      0L
    }

  thru_week <- min(
    max_completed,
    max_pbp_wk
  )

  if (
    !is.na(
      MAX_WEEK_ARG
    )
  ) {
    thru_week <- min(
      thru_week,
      MAX_WEEK_ARG
    )
  }

  start_week <-
    if (TEST_NO_PRIOR) {
      MIN_WEEK_ARG
    } else {
      1L
    }

  if (
    TEST_NO_PRIOR &&
      (
        is.na(start_week) ||
          start_week < 1
      )
  ) {
    stop(
      "--min-week must be an integer >= 1 in --test-no-prior mode.",
      call. = FALSE
    )
  }

  if (
    TEST_NO_PRIOR &&
      start_week >
        thru_week
  ) {
    stop(
      sprintf(
        paste0(
          "QA start week (%d) is later than available thru_week (%d). ",
          "Choose a smaller --min-week or a larger --max-week."
        ),
        start_week,
        thru_week
      ),
      call. = FALSE
    )
  }

  if (TEST_NO_PRIOR) {
    msg(
      "QA MODE: calculating snapshots for weeks %d-%d only.",
      start_week,
      thru_week
    )
  }

  if (
    max_pbp_wk <
      max_completed
  ) {
    msg(
      paste0(
        "DATA LAG: games show completed week %d ",
        "but PBP only reaches week %d. ",
        "Running through week %d."
      ),
      max_completed,
      max_pbp_wk,
      thru_week
    )
  }

  if (
    (
      is.null(pbp) ||
        max_pbp_wk < 1
    ) &&
      max_completed >= 1
  ) {
    stop(
      sprintf(
        paste0(
          "Play-by-play came back empty while %d completed week(s) exist. ",
          "This is a data-load failure, not preseason."
        ),
        max_completed
      ),
      call. = FALSE
    )
  }

  week0 <-
    make_week0_snapshot(
      teams_tbl,
      prior
    )

  # ---------------------------------------------------------------------------
  # Preseason
  # ---------------------------------------------------------------------------

  if (
    !TEST_NO_PRIOR &&
      (
        thru_week < 1 ||
          is.null(pbp)
      )
  ) {

    msg(
      "No completed weeks with PBP. Writing Week 0 preseason baseline."
    )

    snap0 <- week0 %>%
      mutate(
        power_change = 0,
        rank_change = 0L
      )

    write_csv(
      snap0,
      file.path(
        out_dir,
        "weekly",
        "week_00.csv"
      )
    )

    write_csv(
      snap0,
      file.path(
        out_dir,
        "latest.csv"
      )
    )

    plot_btb_scatter(
      snap0,
      out_dir,
      season = TARGET_SEASON
    )

    write_ranked_exports(
      snap0,
      out_dir,
      TARGET_SEASON
    )

    write_csv(
      snap0,
      file.path(
        out_dir,
        "ratings_history.csv"
      )
    )

    write_json(
      snap0,
      file.path(
        out_dir,
        "latest.json"
      ),
      dataframe = "rows",
      pretty = TRUE,
      na = "null"
    )

    write_json(
      list(
        season =
          TARGET_SEASON,

        thru_week =
          0,

        generated_utc =
          format(
            Sys.time(),
            tz = "UTC"
          ),

        note =
          "preseason prior only",

        n_teams =
          nrow(
            teams_tbl
          ),

        config =
          list(
            PLAYS_SCALE =
              PLAYS_SCALE,

            TARGET_SD =
              TARGET_SD,

            PRIOR_G_FULL =
              PRIOR_G_FULL,

            PRIOR_POW =
              PRIOR_POW,

            RECENCY_DECAY =
              RECENCY_DECAY
          )
      ),

      file.path(
        out_dir,
        "meta.json"
      ),

      auto_unbox = TRUE,
      pretty = TRUE
    )

    msg(
      "Done. Preseason outputs in %s",
      out_dir
    )

  } else {

    # -------------------------------------------------------------------------
    # In season
    # -------------------------------------------------------------------------

    neutral_lu <- games %>%
      select(
        game_id,
        neutral_site
      )

    tagged <-
      tag_wepa_features(
        pbp
      )

    wepa <-
      apply_wepa_weights(
        tagged,
        pbp$EPA,
        model_weights
      )

    pbp_aug <-
      bind_cols(
        pbp,
        wepa
      ) %>%
      select(
        -any_of(
          "neutral_site"
        )
      ) %>%
      left_join(
        neutral_lu,
        by = "game_id"
      ) %>%
      mutate(
        garbage_flag =
          (
            score_diff >=
              GT_THRESH["1"] &
              period == 1
          ) |
            (
              score_diff >=
                GT_THRESH["2"] &
                period == 2
            ) |
            (
              score_diff >=
                GT_THRESH["3"] &
                period == 3
            ) |
            (
              score_diff >=
                GT_THRESH["4"] &
                period == 4
            )
      )

    drives <-
      build_drives(
        pbp,
        eckel_model
      )

    snapshot_weeks <-
      if (TEST_NO_PRIOR) {

        seq.int(
          from = start_week,
          to = thru_week
        )

      } else {

        seq_len(
          thru_week
        )
      }

    history_list <- map(
      snapshot_weeks,

      function(w) {

        msg(
          "Snapshot: through week %d",
          w
        )

        snapshot_week(
          w,
          pbp_aug,
          drives,
          games,
          teams_tbl,
          prior,
          have_weights =
            !is.null(
              model_weights
            )
        )
      }
    )

    hfa_epa <-
      attr(
        history_list[[
            length(
              history_list
            )
          ]],
        "hfa_epa"
      )

    wepa_scale <-
      attr(
        history_list[[
            length(
              history_list
            )
          ]],
        "wepa_scale"
      )

    history <-
      if (TEST_NO_PRIOR) {

        bind_rows(
          history_list
        )

      } else {

        bind_rows(
          c(
            list(
              week0
            ),
            history_list
          )
        )
      }

    history <- history %>%
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
          power_pts -
            lag(
              power_pts
            ),

        rank_change =
          lag(
            power_rank
          ) -
            power_rank
      ) %>%
      ungroup() %>%
      mutate(
        power_change =
          ifelse(
            week ==
              min(
                week
              ) |
              is.na(
                power_change
              ),
            0,
            power_change
          ),

        rank_change =
          ifelse(
            week ==
              min(
                week
              ) |
              is.na(
                rank_change
              ),
            0L,
            rank_change
          )
      ) %>%
      arrange(
        week,
        power_rank
      )

    write_csv(
      history,
      file.path(
        out_dir,
        "ratings_history.csv"
      )
    )

    for (
      w in unique(
        history$week
      )
    ) {

      write_csv(
        history %>%
          filter(
            week == w
          ),

        file.path(
          out_dir,
          "weekly",

          sprintf(
            "week_%02d.csv",
            w
          )
        )
      )
    }

    latest <- history %>%
      filter(
        week ==
          max(
            week
          )
      )

    write_csv(
      latest,
      file.path(
        out_dir,
        "latest.csv"
      )
    )

    plot_btb_scatter(
      latest,
      out_dir,
      season =
        TARGET_SEASON
    )

    write_ranked_exports(
      latest,
      out_dir,
      TARGET_SEASON
    )

    write_json(
      latest,
      file.path(
        out_dir,
        "latest.json"
      ),
      dataframe = "rows",
      pretty = TRUE,
      na = "null"
    )

    write_json(
      list(
        season =
          TARGET_SEASON,

        qa_test_no_prior =
          TEST_NO_PRIOR,

        qa_min_week =
          if (TEST_NO_PRIOR) {
            start_week
          } else {
            1L
          },

        thru_week =
          thru_week,

        generated_utc =
          format(
            Sys.time(),
            tz = "UTC"
          ),

        games_max_completed_week =
          max_completed,

        pbp_max_week =
          max_pbp_wk,

        wepa_weights_loaded =
          !is.null(
            model_weights
          ),

        eckel_model_loaded =
          !is.null(
            eckel_model
          ),

        hfa_epa_per_play =
          hfa_epa,

        wepa_to_epa_scale =
          wepa_scale,

        n_teams =
          nrow(
            teams_tbl
          ),

        config =
          list(
            PLAYS_SCALE =
              PLAYS_SCALE,

            TARGET_SD =
              TARGET_SD,

            PRIOR_G_FULL =
              PRIOR_G_FULL,

            PRIOR_POW =
              PRIOR_POW,

            RECENCY_DECAY =
              RECENCY_DECAY
          ),

        column_notes =
          list(
            power_off_def_pts =
              paste0(
                "points versus average FBS team per game; ",
                "higher is better; def_pts = points prevented"
              ),

            off_stats =
              paste0(
                "current-season offensive metrics; ",
                "higher is better"
              ),

            def_stats =
              paste0(
                "current-season defensive allowed metrics; ",
                "lower is better"
              ),

            adj_rush_pass =
              paste0(
                "current-season opponent- and venue-adjusted EPA/play"
              ),

            prior_weight =
              paste0(
                "share of BTB Power Rating still derived ",
                "from the preseason baseline"
              ),

            power_change =
              paste0(
                "change in BTB Power Rating versus ",
                "the previous weekly snapshot"
              ),

            rank_change =
              paste0(
                "national rank movement versus the previous snapshot; ",
                "positive means the team moved up"
              )
          )
      ),

      file.path(
        out_dir,
        "meta.json"
      ),

      auto_unbox = TRUE,
      pretty = TRUE
    )

    msg(
      "Done. Through week %d. Outputs in %s",
      thru_week,
      out_dir
    )
  }
}
