from __future__ import annotations

import argparse
from pathlib import Path

SEASON = 2025
GAME_OUTPUT = "qb_starter_inference_2025_game_level.csv"
SUMMARY_OUTPUT = "qb_starter_inference_2025_team_summary.csv"


def _build_qb_events(pbp):
    import pandas as pd

    offense = pbp.loc[pbp["posteam"].notna()].copy()

    pass_events = offense.loc[
        offense["passer_player_id"].notna(),
        ["game_id", "week", "posteam", "play_id", "passer_player_id", "passer_player_name"],
    ].rename(
        columns={
            "passer_player_id": "player_id",
            "passer_player_name": "player_name",
        }
    )

    rush_flags = offense[["qb_scramble", "qb_kneel", "qb_spike"]].fillna(0).sum(axis=1) > 0
    rush_events = offense.loc[
        rush_flags & offense["rusher_player_id"].notna(),
        ["game_id", "week", "posteam", "play_id", "rusher_player_id", "rusher_player_name"],
    ].rename(
        columns={
            "rusher_player_id": "player_id",
            "rusher_player_name": "player_name",
        }
    )

    qb_events = (
        pd.concat([pass_events, rush_events], ignore_index=True)
        .dropna(subset=["player_id"])
        .drop_duplicates(subset=["game_id", "posteam", "play_id", "player_id"])
    )
    return qb_events


def infer_game_level_starters(pbp):
    qb_events = _build_qb_events(pbp)

    qb_usage = (
        qb_events.groupby(["game_id", "week", "posteam", "player_id", "player_name"], as_index=False)
        .agg(first_qb_play_id=("play_id", "min"), qb_play_count=("play_id", "size"))
    )

    qb_usage = qb_usage.sort_values(
        ["game_id", "posteam", "first_qb_play_id", "qb_play_count", "player_name"],
        ascending=[True, True, True, False, True],
    )

    starters = (
        qb_usage.groupby(["game_id", "posteam"], as_index=False)
        .head(1)
        .rename(columns={"posteam": "team", "player_id": "starter_player_id", "player_name": "starter_player_name"})
    )

    team_game_totals = (
        qb_events.groupby(["game_id", "posteam"], as_index=False)
        .agg(total_qb_identified_plays=("play_id", "size"))
        .rename(columns={"posteam": "team"})
    )

    game_level = starters.merge(team_game_totals, on=["game_id", "team"], how="left")
    game_level["starter_qb_play_share"] = (
        game_level["qb_play_count"] / game_level["total_qb_identified_plays"]
    ).round(4)

    return game_level[
        [
            "game_id",
            "week",
            "team",
            "starter_player_id",
            "starter_player_name",
            "first_qb_play_id",
            "qb_play_count",
            "total_qb_identified_plays",
            "starter_qb_play_share",
        ]
    ].sort_values(["week", "team"])


def build_team_summary(game_level):
    summary = (
        game_level.groupby(["team", "starter_player_id", "starter_player_name"], as_index=False)
        .agg(
            inferred_starts=("game_id", "nunique"),
            starter_identified_qb_plays=("qb_play_count", "sum"),
            team_identified_qb_plays=("total_qb_identified_plays", "sum"),
            first_week_as_starter=("week", "min"),
            last_week_as_starter=("week", "max"),
        )
    )

    summary["starter_identified_qb_play_share"] = (
        summary["starter_identified_qb_plays"] / summary["team_identified_qb_plays"]
    ).round(4)

    return summary.sort_values(["team", "inferred_starts", "starter_identified_qb_plays"], ascending=[True, False, False])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Infer likely starting QBs for the 2025 NFL season using nflverse play-by-play data."
        )
    )
    parser.add_argument(
        "--output-dir",
        default="outputs",
        help="Directory where CSV files will be written (default: outputs)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        import nfl_data_py as nfl
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency. Install with: pip install pandas nfl_data_py"
        ) from exc

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pbp = nfl.import_pbp_data([SEASON], downcast=True, cache=False)
    pbp = pbp.loc[pbp["season_type"] == "REG"]

    game_level = infer_game_level_starters(pbp)
    summary = build_team_summary(game_level)

    game_output_path = output_dir / GAME_OUTPUT
    summary_output_path = output_dir / SUMMARY_OUTPUT

    game_level.to_csv(game_output_path, index=False)
    summary.to_csv(summary_output_path, index=False)

    print(f"Wrote {len(game_level)} rows to {game_output_path}")
    print(f"Wrote {len(summary)} rows to {summary_output_path}")


if __name__ == "__main__":
    main()
