# Install/load packages if needed
if (!requireNamespace("nflreadr", quietly = TRUE)) install.packages("nflreadr")
if (!requireNamespace("dplyr", quietly = TRUE)) install.packages("dplyr")
if (!requireNamespace("readr", quietly = TRUE)) install.packages("readr")

library(nflreadr)
library(dplyr)
library(readr)

season_year <- 2026

# Load play-by-play data for 2026 season
pbp <- load_pbp(seasons = season_year)

# Extract unique QB names and IDs
qb_names <- pbp %>%
  filter(!is.na(passer_player_name), passer_player_name != "") %>%
  distinct(
    passer_player_id,
    passer_player_name
  ) %>%
  arrange(passer_player_name)

# Ensure output directory exists
dir.create("inst/extdata", recursive = TRUE, showWarnings = FALSE)

# Write CSV
write_csv(qb_names, "inst/extdata/qb_names_2026.csv")

cat("✓ CSV saved to inst/extdata/qb_names_2026.csv\n")
cat("Total QBs found:", nrow(qb_names), "\n")
