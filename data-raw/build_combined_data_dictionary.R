if (!requireNamespace("nflreadr", quietly = TRUE)) {
  stop("Package 'nflreadr' is required to refresh the combined dictionary.")
}

if (!requireNamespace("nflfastR", quietly = TRUE)) {
  stop("Package 'nflfastR' is required to refresh the combined dictionary.")
}

normalize_team_stats <- function(df) {
  data.frame(
    field = as.character(df$field),
    description = as.character(df$description),
    data_type = as.character(df$data_type),
    source = "nflreadr::dictionary_team_stats",
    stringsAsFactors = FALSE
  )
}

normalize_field_descriptions <- function(df) {
  field_col <- if ("field" %in% names(df)) "field" else "Field"
  description_col <- if ("description" %in% names(df)) "description" else "Description"

  data.frame(
    field = as.character(df[[field_col]]),
    description = as.character(df[[description_col]]),
    data_type = NA_character_,
    source = "nflfastR::field_descriptions",
    stringsAsFactors = FALSE
  )
}

team_stats <- normalize_team_stats(nflreadr::dictionary_team_stats)
field_descriptions <- normalize_field_descriptions(nflfastR::field_descriptions)

combined_dictionary <- rbind(team_stats, field_descriptions)
combined_dictionary <- combined_dictionary[order(combined_dictionary$source, combined_dictionary$field), ]

out_dir <- file.path("inst", "extdata")
if (!dir.exists(out_dir)) {
  dir.create(out_dir, recursive = TRUE)
}

out_file <- file.path(out_dir, "combined_data_dictionary.csv")
write.csv(combined_dictionary, out_file, row.names = FALSE)

message("Wrote ", nrow(combined_dictionary), " rows to ", out_file)
