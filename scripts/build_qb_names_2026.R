if (!requireNamespace("rvest", quietly = TRUE)) install.packages("rvest")
if (!requireNamespace("dplyr", quietly = TRUE)) install.packages("dplyr")
if (!requireNamespace("purrr", quietly = TRUE)) install.packages("purrr")
if (!requireNamespace("stringr", quietly = TRUE)) install.packages("stringr")
if (!requireNamespace("readr", quietly = TRUE)) install.packages("readr")
if (!requireNamespace("janitor", quietly = TRUE)) install.packages("janitor")
if (!requireNamespace("tibble", quietly = TRUE)) install.packages("tibble")

library(rvest)
library(dplyr)
library(purrr)
library(stringr)
library(readr)
library(janitor)
library(tibble)

season <- 2026

# NFL team abbreviations used by Pro-Football-Reference URLs
teams <- c(
  "crd","atl","rav","buf","car","chi","cin","cle","dal","den","det","gnb",
  "htx","clt","jax","kan","rai","sdg","ram","mia","min","nwe","nor","nyg",
  "nyj","phi","pit","sfo","sea","tam","oti","was"
)

scrape_team_qbs <- function(team_code, season) {
  url <- sprintf("https://www.pro-football-reference.com/teams/%s/%s_roster.htm", team_code, season)

  message("Reading: ", url)

  page <- tryCatch(read_html(url), error = function(e) NULL)
  if (is.null(page)) return(tibble())

  # PFR roster table id is usually "games_played_team"
  roster <- page %>%
    html_element("table#games_played_team") %>%
    html_table()

  if (is.null(roster) || nrow(roster) == 0) return(tibble())

  roster <- roster %>%
    clean_names()

  # Position column is usually "pos"
  if (!"pos" %in% names(roster)) return(tibble())

  qbs <- roster %>%
    filter(pos == "QB") %>%
    transmute(
      season = season,
      team_code = toupper(team_code),
      player_name = player,
      pos = pos,
      age = suppressWarnings(as.integer(age)),
      games = suppressWarnings(as.integer(g)),
      games_started = suppressWarnings(as.integer(gs))
    )

  qbs
}

qb_df <- map_dfr(teams, scrape_team_qbs, season = season) %>%
  distinct(season, team_code, player_name, .keep_all = TRUE) %>%
  arrange(team_code, player_name)

dir.create("inst/extdata", recursive = TRUE, showWarnings = FALSE)
write_csv(qb_df, "inst/extdata/qb_names_2026_pfr.csv")

message("Saved inst/extdata/qb_names_2026_pfr.csv with ", nrow(qb_df), " rows.")
