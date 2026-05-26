#!/usr/bin/env python3
"""Generate Jacoby Brissett vs Gardner Minshew comparison data and visuals.

This script pulls nflverse/nflfastr-style play-by-play data via nfl_data_py and computes:
- EPA/play
- CPOE
- TD rate
- INT rate
- 3rd down EPA/play

It also downloads player headshots and renders simple comparison visuals inspired by
nflplotR guidance: minimalist bars, direct labels, and clean table cards.

Default comparison seasons:
- Jacoby Brissett: 2025
- Gardner Minshew: 2024
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Iterable, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import requests
from PIL import Image

try:
    import nfl_data_py as nfl
except ImportError as exc:
    raise SystemExit(
        "nfl_data_py is required. Install with: pip install pandas nfl_data_py matplotlib requests pillow pyarrow"
    ) from exc


METRIC_ORDER = [
    "epa_per_play",
    "cpoe",
    "td_rate",
    "int_rate",
    "third_down_epa_per_play",
]

METRIC_LABELS = {
    "epa_per_play": "EPA/Play",
    "cpoe": "CPOE",
    "td_rate": "TD Rate",
    "int_rate": "INT Rate",
    "third_down_epa_per_play": "3rd Down EPA/Play",
}

REQUIRED_COLUMNS = {
    "season",
    "down",
    "play_type",
    "pass_attempt",
    "interception",
    "touchdown",
    "epa",
    "cpoe",
    "passer_player_name",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="outputs/qb_comparison",
        help="Directory where CSVs, images, and headshots will be saved.",
    )
    parser.add_argument(
        "--brissett-season",
        type=int,
        default=2025,
        help="Season to use for Jacoby Brissett.",
    )
    parser.add_argument(
        "--minshew-season",
        type=int,
        default=2024,
        help="Season to use for Gardner Minshew.",
    )
    return parser.parse_args()


def build_qbs(args: argparse.Namespace) -> List[Dict[str, object]]:
    return [
        {
            "player_name": "Jacoby Brissett",
            "player_display": "Jacoby Brissett",
            "season": args.brissett_season,
            "headshot_url": "https://a.espncdn.com/i/headshots/nfl/players/full/2573309.png",
            "color": "#97233F",
        },
        {
            "player_name": "Gardner Minshew",
            "player_display": "Gardner Minshew II",
            "season": args.minshew_season,
            "headshot_url": "https://a.espncdn.com/i/headshots/nfl/players/full/4038524.png",
            "color": "#FFB612",
        },
    ]


def validate_columns(df: pd.DataFrame) -> None:
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            "Play-by-play data is missing required columns: "
            + ", ".join(sorted(missing))
        )


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
    validate_columns(pbp)
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
        "td_rate": df["touchdown"].fillna(0).astype(float).mean(),
        "int_rate": df["interception"].fillna(0).astype(float).mean(),
        "third_down_epa_per_play": third["epa"].mean() if not third.empty else float("nan"),
    }


def build_metrics_table(pbp: pd.DataFrame, qbs: List[Dict[str, object]]) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []

    for qb in qbs:
        plays = qb_pass_plays(pbp, str(qb["player_name"]), int(qb["season"]))
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


def safe_filename(name: str) -> str:
    return name.lower().replace(" ", "_").replace(".", "").replace("/", "_")


def download_headshot(url: str, target: Path) -> bool:
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        target.write_bytes(response.content)
        return True
    except requests.RequestException:
        return False


def save_headshots(metrics_df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    headshot_dir = out_dir / "headshots"
    headshot_dir.mkdir(parents=True, exist_ok=True)

    local_paths = []
    headshot_ok = []

    for _, row in metrics_df.iterrows():
        filename = safe_filename(str(row["player"])) + ".png"
        target = headshot_dir / filename
        ok = download_headshot(str(row["headshot_url"]), target)
        local_paths.append(str(target))
        headshot_ok.append(ok)

    updated = metrics_df.copy()
    updated["headshot_path"] = local_paths
    updated["headshot_downloaded"] = headshot_ok
    return updated


def render_metric_bars(metrics_df: pd.DataFrame, out_dir: Path) -> None:
    fig, axes = plt.subplots(len(METRIC_ORDER), 1, figsize=(10, 14), constrained_layout=True)

    if len(METRIC_ORDER) == 1:
        axes = [axes]

    for ax, metric in zip(axes, METRIC_ORDER):
        values = metrics_df[metric]
        colors = metrics_df["color"]
        labels = [f"{p} ({s})" for p, s in zip(metrics_df["player"], metrics_df["season"])]

        ax.barh(labels, values, color=colors)
        ax.set_title(METRIC_LABELS[metric], loc="left", fontweight="bold")
        ax.axvline(0, color="#999999", linewidth=0.8)

        for i, v in enumerate(values):
            label = f"{v:.3f}" if pd.notna(v) else "NA"
            x = float(v) if pd.notna(v) else 0.0
            ax.text(x, i, f"  {label}", va="center", ha="left", fontsize=10)

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="x", linestyle="--", alpha=0.25)

    fig.suptitle(
        "Jacoby Brissett vs Gardner Minshew: QB Efficiency Snapshot",
        fontsize=16,
        fontweight="bold",
    )
    fig.savefig(out_dir / "qb_metric_bars.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def render_summary_table(metrics_df: pd.DataFrame, out_dir: Path) -> None:
    display_df = metrics_df[["player", "season", "attempts"] + METRIC_ORDER].copy()

    for col in METRIC_ORDER:
        display_df[col] = display_df[col].map(lambda x: f"{x:.3f}" if pd.notna(x) else "NA")

    fig, ax = plt.subplots(figsize=(12, 2.8))
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

    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_text_props(weight="bold")
            cell.set_facecolor("#ECECEC")

    ax.set_title("QB comparison table", loc="left", fontweight="bold")
    fig.savefig(out_dir / "qb_metrics_table.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def render_headshot_card(metrics_df: pd.DataFrame, out_dir: Path) -> None:
    fig, axes = plt.subplots(1, len(metrics_df), figsize=(10, 5), constrained_layout=True)

    if len(metrics_df) == 1:
        axes = [axes]

    for ax, (_, row) in zip(axes, metrics_df.iterrows()):
        ax.axis("off")
        path = Path(str(row["headshot_path"]))

        if bool(row["headshot_downloaded"]) and path.exists():
            image = Image.open(path).convert("RGBA")
            ax.imshow(image)
        else:
            ax.text(
                0.5,
                0.5,
                "Headshot unavailable",
                ha="center",
                va="center",
                fontsize=12,
                fontweight="bold",
            )

        ax.set_title(
            f"{row['player']}\nSeason used: {row['season']}",
            fontweight="bold",
        )

    fig.suptitle("Headshots for video package", fontsize=16, fontweight="bold")
    fig.savefig(out_dir / "qb_headshots_panel.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    qbs = build_qbs(args)
    seasons = sorted({int(qb["season"]) for qb in qbs})

    print(f"Loading play-by-play data for seasons: {seasons}")
    pbp = load_pbp(seasons)

    print("Building metrics table")
    metrics_df = build_metrics_table(pbp, qbs)

    metrics_df = save_headshots(metrics_df, out_dir)
    metrics_df.to_csv(out_dir / "qb_comparison_metrics.csv", index=False)

    print("Rendering visuals")
    render_metric_bars(metrics_df, out_dir)
    render_summary_table(metrics_df, out_dir)
    render_headshot_card(metrics_df, out_dir)

    print(f"Saved comparison outputs to {out_dir}")


if __name__ == "__main__":
    main()
