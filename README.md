# btb-discord-digest
NFL and NCAAF News

## 2025 likely starting QB inference (nflfastR/nflverse data)

This repository now includes a reproducible script to infer likely starting quarterbacks by team across the 2025 regular season using nflverse play-by-play data (the Python nfl_data_py client for nflfastR/nflverse data).

### Script

- `/home/runner/work/football-testgrounds/football-testgrounds/scripts/infer_qb_starters_2025.py`
- `/home/runner/work/football-testgrounds/football-testgrounds/scripts/qb_comparison_visuals.py`

### Install

```bash
pip install -r /home/runner/work/football-testgrounds/football-testgrounds/requirements.txt
```

### Run

```bash
python /home/runner/work/football-testgrounds/football-testgrounds/scripts/qb_comparison_visuals.py --output-dir /home/runner/work/football-testgrounds/football-testgrounds/outputs/qb_comparison
```

### Outputs

The QB comparison script writes:

- `/home/runner/work/football-testgrounds/football-testgrounds/outputs/qb_comparison/qb_comparison_metrics.csv`
  - Jacoby Brissett vs Gardner Minshew comparison table
  - includes EPA/play, CPOE, TD rate, INT rate, and 3rd down EPA/play
- `/home/runner/work/football-testgrounds/football-testgrounds/outputs/qb_comparison/qb_metric_bars.png`
  - bar chart visual for the requested metrics
- `/home/runner/work/football-testgrounds/football-testgrounds/outputs/qb_comparison/qb_metrics_table.png`
  - compact table visual for video overlays
- `/home/runner/work/football-testgrounds/football-testgrounds/outputs/qb_comparison/qb_headshots_panel.png`
  - headshot panel for both QBs
- `/home/runner/work/football-testgrounds/football-testgrounds/outputs/qb_comparison/headshots/*.png`
  - individual downloaded headshots

### Assumptions and limitations

- Uses the most recent substantial season samples specified in the script: Jacoby Brissett 2025 and Gardner Minshew 2024.
- Metrics are pass-play based from nflverse play-by-play data via `nfl_data_py`.
- Headshots are resolved from nflverse roster metadata (`headshot_url`) when available.
- If a headshot URL is missing or fails to download, the script continues and renders a placeholder in the headshot panel.

### GitHub Actions workflow

- Workflow file: `/home/runner/work/football-testgrounds/football-testgrounds/.github/workflows/qb-comparison-visuals.yml`
- Trigger manually from Actions (**Generate QB comparison visuals**) or via scheduled run.
- Generated files are uploaded as run artifacts and committed back when outputs changed.

## Shared data dictionary for vignettes

- Canonical dictionary asset: `/tmp/workspace/trashduty/football-testgrounds/inst/extdata/combined_data_dictionary.csv`
- Upstream sources included in the canonical file:
  - `nflreadr::dictionary_team_stats`
  - `nflfastR::field_descriptions`
- Use `/tmp/workspace/trashduty/football-testgrounds/data-raw/build_combined_data_dictionary.R` to refresh the local combined dictionary from upstream package datasets.
