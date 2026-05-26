#!/usr/bin/env python3
"""Generate Jacoby Brissett vs Gardner Minshew comparison data and visuals.

Pulls nflverse/nflfastr-style play-by-play data via nfl_data_py, computes:
- EPA/play
- CPOE
- TD rate
- INT rate
- 3rd down EPA/play

It also downloads player headshots and renders simple comparison visuals inspired by
nflplotR guidance (clean labels, direct annotation, minimalist bars/cards).
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Dict, Iterable, List

import matplotlib.pyplot as plt
import pandas as pd
import requests

try:
    import nfl_data_py as nfl
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "nfl_data_py is required. Install with: pip install nfl_data_py pandas matplotlib requests pillow"
    ) from exc

from PIL import Image


QBS = [
    {
        "player_name": "Jacoby Brissett",
        "player_display": "Jacoby Brissett",
        "season": 2025,
        "headshot_url": "https://a.espncdn.com/i/headshots/nfl/players/full/2573309.png",
        "color": "#97233F",
    },
    {
        "player_name": "Gardner Minshew",
        "player_display": "Gardner Minshew II",
        "season": 2024,
        "headshot_url": "https://a.espncdn.com/i/headshots/nfl/players/full/4038524.png",
        "color": "#FFB612",
    },
]

METRIC_ORDER = ["epa_per_play", "cpoe", "td_rate", "int_rate", "third_down_epa_per_play"]
METRIC_LABELS = {
    "epa_per_play": "EPA/Play",
    "cpoe": "CPOE",
    "td_rate": "TD Rate",
    "int_rate": "INT Rate",
    "third_down_epa_per_play": "3rd Down EPA/Play",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="outputs/qb_comparison",
        help="Directory where CSVs, images, and headshots will be saved.",
    )
    return parser.parse_args()


def load_pbp(seasons: Iterable[int]) -> pd.DataFrame:
    cols = [
        "season",
        "week",
        "game_id",
        "posteam",
        "down",
        "play_type",
        "qb_dropback",
        "pass_attempt",
        "complete_pass",
        "incomplete_pass",
        "interception",
        "touchdown",
        "air_epa",
        "epa",
        "cpoe",
        "passer_player_name",
        "rusher_player_name",
        "desc",
    ]
    pbp = nfl.import_pbp_data(list(seasons), columns=cols)
    return pbp


def qb_pass_plays(pbp: pd.DataFrame, qb_name: str, season: int) -> pd.DataFrame:
    season_df = pbp[pbp["season"] == season].copy()
    mask = (
        (season_df["passer_player_name"] == qb_name)
        & (season_df["play_type"] == "pass")
        & (season_df["pass_attempt"] == 1)
    )
    return season_df.loc[mask].copy()


def compute_metrics(df: pd.DataFrame) -> Dict[str, float]:
    attempts = len(df)
    if attempts == 0:
        return {k: float("nan") for k in METRIC_ORDER} | {"attempts": 0}

    third = df[df["down"] == 3]
    return {
        "attempts": attempts,
        "epa_per_play": df["epa"].mean(),
        "cpoe": df["cpoe"].mean(),
        "td_rate": df["touchdown"].fillna(0).mean(),
        "int_rate": df["interception"].fillna(0).mean(),
        "third_down_epa_per_play": third["epa"].mean() if not third.empty else float("nan"),
    }


def build_metrics_table(pbp: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, float]] = []
    for qb in QBS:
        plays = qb_pass_plays(pbp, qb["player_name"], qb["season"])
        metrics = compute_metrics(plays)
        metrics.update(
            {
                "player": qb["player_display"],
                "season": qb["season"],
                "headshot_url": qb["headshot_url"],
                "color": qb["color"],
            }
        )
        rows.append(metrics)
    return pd.DataFrame(rows)


def save_headshots(metrics_df: pd.DataFrame, out_dir: Path) -> None:
    headshot_dir = out_dir / "headshots"
    headshot_dir.mkdir(parents=True, exist_ok=True)
    for _, row in metrics_df.iterrows():
        filename = row["player"].lower().replace(" ", "_").replace(".", "") + ".png"
        target = headshot_dir / filename
        response = requests.get(row["headshot_url"], timeout=30)
        response.raise_for_status()
        target.write_bytes(response.content)


def render_metric_bars(metrics_df: pd.DataFrame, out_dir: Path) -> None:
    fig, axes = plt.subplots(len(METRIC_ORDER), 1, figsize=(10, 14), constrained_layout=True)
    for ax, metric in zip(axes, METRIC_ORDER):
        values = metrics_df[metric]
        colors = metrics_df["color"]
        labels = [f"{p} ({s})" for p, s in zip(metrics_df["player"], metrics_df["season"])]
        ax.barh(labels, values, color=colors)
        ax.set_title(METRIC_LABELS[metric], loc="left", fontweight="bold")
        ax.axvline(0, color="#999999", linewidth=0.8)
        for i, v in enumerate(values):
            label = f"{v:.3f}" if pd.notna(v) else "NA"
            x = v if pd.notna(v) else 0
            ax.text(x, i, f"  {label}", va="center", ha="left", fontsize=10)
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Jacoby Brissett vs Gardner Minshew: QB Efficiency Snapshot", fontsize=16, fontweight="bold")
    fig.savefig(out_dir / "qb_metric_bars.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def render_summary_table(metrics_df: pd.DataFrame, out_dir: Path) -> None:
    display_df = metrics_df[["player", "season", "attempts"] + METRIC_ORDER].copy()
    for col in METRIC_ORDER:
        display_df[col] = display_df[col].map(lambda x: f"{x:.3f}" if pd.notna(x) else "NA")

    fig, ax = plt.subplots(figsize=(12, 2.5))
    ax.axis("off")
    table = ax.table(
        cellText=display_df.values,
        colLabels=["Player", "Season", "Att"] + [METRIC_LABELS[m] for m in METRIC_ORDER],
        cellLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.7)
    ax.set_title("QB comparison table", loc="left", fontweight="bold")
    fig.savefig(out_dir / "qb_metrics_table.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def render_headshot_card(metrics_df: pd.DataFrame, out_dir: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10, 5), constrained_layout=True)
    for ax, (_, row) in zip(axes, metrics_df.iterrows()):
        response = requests.get(row["headshot_url"], timeout=30)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content)).convert("RGBA")
        ax.imshow(image)
        ax.axis("off")
        ax.set_title(f"{row['player']}\nSeason used: {row['season']}", fontweight="bold")
    fig.suptitle("Headshots for video package", fontsize=16, fontweight="bold")
    fig.savefig(out_dir / "qb_headshots_panel.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    seasons = sorted({qb["season"] for qb in QBS})
    pbp = load_pbp(seasons)
    metrics_df = build_metrics_table(pbp)
    metrics_df.to_csv(out_dir / "qb_comparison_metrics.csv", index=False)

    save_headshots(metrics_df, out_dir)
    render_metric_bars(metrics_df, out_dir)
    render_summary_table(metrics_df, out_dir)
    render_headshot_card(metrics_df, out_dir)

    print(f"Saved comparison outputs to {out_dir}")


if __name__ == "__main__":
    main()
