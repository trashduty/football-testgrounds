# btb-discord-digest
NFL and NCAAF News

## 2025 likely starting QB inference (nflfastR/nflverse data)

This repository now includes a reproducible script to infer likely starting quarterbacks by team across the 2025 regular season using nflverse play-by-play data (the Python nfl_data_py client for nflfastR/nflverse data).

### Script

- `/home/runner/work/football-testgrounds/football-testgrounds/scripts/infer_qb_starters_2025.py`

### Install

```bash
pip install pandas nfl_data_py
```

### Run

```bash
python /home/runner/work/football-testgrounds/football-testgrounds/scripts/infer_qb_starters_2025.py --output-dir /home/runner/work/football-testgrounds/football-testgrounds/outputs
```

### Outputs

The script writes:

- `/home/runner/work/football-testgrounds/football-testgrounds/outputs/qb_starter_inference_2025_game_level.csv`
  - one row per team per game
  - inferred starter based on the earliest offensive QB event in play-by-play (passer, QB scramble, kneel, or spike)
  - includes starter identified QB-play share for that team-game
- `/home/runner/work/football-testgrounds/football-testgrounds/outputs/qb_starter_inference_2025_team_summary.csv`
  - aggregates inferred starts and QB-identified play usage by team and quarterback for the full 2025 season

### Assumptions and limitations

- The inference is play-by-play-derived and identifies the likely starter as the QB with the earliest QB event for that team-game.
- This can differ from an official depth-chart starter in edge cases (e.g., trick plays, immediate injury substitution, or unusual package usage).
- Summary metrics are derived from the same QB-identified play events and are intended for practical starter/usage analysis over time.
