# make_preseason_csv.R
# Builds data/preseason_ratings_<year>.csv for the site pipeline from either
# preseason source, auto-detected by columns:
#   A) Aug 2 build (preseason_2026_adjusted_ratings.csv): adj_ppg_for/against.
#      Full off/def split from adjusted points for/against.
#   B) v5 ridge build (power_rating_2026_v5.csv): power_rating. No split.
#   Hybrid (recommended when using v5): pass --split-from=<Aug 2 file> to take
#   TOTALS from v5 and the off/def TILT from the Aug 2 build, so early-season
#   off/def ranks are not a degenerate 50/50 of power.
#
# Usage:
#   Rscript make_preseason_csv.R <input.csv> [output.csv] [--split-from=<aug2.csv>]
# Output default: data/preseason_ratings_2026.csv
# Scale never matters downstream (the loader re-centers and standardizes to
# SD 12, preserving the split); ordering and relative spacing carry through.

suppressMessages({ library(dplyr); library(readr) })

args    <- commandArgs(trailingOnly = TRUE)
pos     <- args[!grepl("^--", args)]
infile  <- if (length(pos) >= 1) pos[1] else "preseason_2026_adjusted_ratings.csv"
outfile <- if (length(pos) >= 2) pos[2] else "data/preseason_ratings_2026.csv"
splitfrom <- sub("^--split-from=", "", grep("^--split-from=", args, value = TRUE))[1]

# Keep in sync with PRIOR_NAME_RECODE in weekly_site_update.R. Applied to the
# Aug 2 file's names so it can join v5's CFBD-style names in hybrid mode.
RECODE <- c("Louisiana Monroe" = "UL Monroe", "Southern Mississippi" = "Southern Miss",
            "Sam Houston State" = "Sam Houston", "UMass" = "Massachusetts",
            "UT San Antonio" = "UTSA", "Connecticut" = "UConn",
            "San Jose State" = "San Jos\u00e9 State", "Hawaii" = "Hawai'i")

read_any <- function(path) {
  if (is.na(path) || !file.exists(path)) stop("Input not found: ", path, call. = FALSE)
  read_csv(path, show_col_types = FALSE)
}
team_col <- function(df) intersect(c("Team", "team", "school"), names(df))[1]
scale12  <- function(x) (x - mean(x, na.rm = TRUE)) / sd(x, na.rm = TRUE) * 12

ppg_split <- function(raw) {
  tc <- team_col(raw)
  raw %>% transmute(team = recode(.data[[tc]], !!!RECODE),
                    off = adj_ppg_for - mean(adj_ppg_for, na.rm = TRUE),
                    def = mean(adj_ppg_against, na.rm = TRUE) - adj_ppg_against)
}

raw <- read_any(infile)
is_ppg <- all(c("adj_ppg_for", "adj_ppg_against") %in% names(raw))
is_v5  <- "power_rating" %in% names(raw)

if (is_ppg) {
  s <- ppg_split(raw)
  out <- s %>% transmute(team, off_pts = round(off, 2), def_pts = round(def, 2),
                         power_pts = off_pts + def_pts)
  mode <- "Aug 2 adjusted-ppg build (native off/def split)"
} else if (is_v5) {
  tc <- team_col(raw)
  base <- raw %>% transmute(team = .data[[tc]], p = scale12(power_rating))
  if (!is.na(splitfrom)) {
    s <- ppg_split(read_any(splitfrom))
    k <- 12 / sd(s$off + s$def, na.rm = TRUE)
    s <- s %>% transmute(team, asym = (off - def) * k)
    base <- base %>% left_join(s, by = "team")
    nomatch <- base$team[is.na(base$asym)]
    if (length(nomatch)) warning("No split source match (tilt set to 0): ",
                                 paste(nomatch, collapse = ", "))
    base$asym[is.na(base$asym)] <- 0
    out <- base %>% transmute(team, power_pts = round(p, 2),
                              off_pts = round((p + asym) / 2, 2),
                              def_pts = round(p, 2) - off_pts)
    mode <- sprintf("HYBRID: totals from v5, off/def tilt from %s", basename(splitfrom))
  } else {
    out <- base %>% transmute(team, power_pts = round(p, 2))
    mode <- "v5 totals only. WARNING: no off/def split; early-season off/def ranks will equal the power rank for every team until game data accumulates."
  }
} else {
  stop("Unrecognized format. Expected adj_ppg_for/adj_ppg_against (Aug 2 build) ",
       "or power_rating (v5). Found: ", paste(names(raw), collapse = ", "), call. = FALSE)
}

out <- out %>% arrange(desc(power_pts))
dir.create(dirname(outfile), showWarnings = FALSE, recursive = TRUE)
write_csv(out, outfile)

cat("Mode:", mode, "\n")
cat(sprintf("Wrote %s (%d teams)\n\nTop 10:\n", outfile, nrow(out)))
print(as.data.frame(head(out, 10)))
cat("\nBottom 5:\n"); print(as.data.frame(tail(out, 5)))
