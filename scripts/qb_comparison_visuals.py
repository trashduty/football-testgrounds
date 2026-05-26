#!/usr/bin/env python3
"""Generate Jacoby Brissett vs Gardner Minshew comparison data and visuals."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

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

PBP_REQUIRED_COLUMNS = {
    "season",
    "down",
    "play_type",
    "pass_attempt",
    "epa",
    "cpoe",
    "passer_player_id",
}


def normalize_name(name: str) -> str:
    """Normalize a player name for robust matching.

    Strips common name suffixes (II, III, IV, Jr., Sr.) and lowercases.
    """
    name = str(name).strip()
    name = re.sub(r"\s+(II|III|IV|Jr\.?|Sr\.?)$", "", name, flags=re.IGNORECASE)
    return name.lower().strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="outputs/qb_comparison",
        help="Directory where CSVs, images, and headshots will be saved.",
    )
    return parser.parse_args()


def build_qbs() -> List[Dict[str, object]]:
    return [
        {
            "player_name": "Jacoby Brissett",
            "player_display": "Jacoby Brissett",
            "season": 2025,
            "color": "#97233F",
        },
        {
            "player_name": "Gardner Minshew",
            "player_display": "Gardner Minshew II",
            "season": 2024,
            "color": "#FFB612",
        },
    ]


def validate_pbp_columns(df: pd.DataFrame) -> None:
    missing = PBP_REQUIRED_COLUMNS - set(df.columns)
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
        "epa",
        "cpoe",
        "passer_player_name",
        "passer_player_id",
        "desc",
    ]
    pbp = nfl.import_pbp_data(list(seasons), columns=cols)
    validate_pbp_columns(pbp)
    return pbp


def load_seasonal_stats(seasons: Iterable[int]) -> pd.DataFrame:
    """Load player-season passing stats from nflverse via nfl_data_py."""
    try:
        stats = nfl.import_seasonal_data(list(seasons), s_type="REG")
        return stats
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not load seasonal stats: {exc}")
        return pd.DataFrame()


def load_rosters(seasons: Iterable[int]) -> pd.DataFrame:
    required = {"season", "player_name", "headshot_url"}
    empty_rosters = pd.DataFrame(columns=sorted(required))
    try:
        rosters = nfl.import_seasonal_rosters(list(seasons))
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not load roster metadata for headshots: {exc}")
        return empty_rosters

    if rosters.empty:
        print("Warning: roster metadata is empty; headshots may be unavailable.")
        return empty_rosters

    missing = required - set(rosters.columns)
    if missing:
        print(
            "Warning: roster metadata missing headshot fields: "
            + ", ".join(sorted(missing))
        )
        return empty_rosters

    return rosters


def match_player_season(
    stats_df: pd.DataFrame, player_name: str, season: int
) -> Optional[pd.Series]:
    """Find a player's row in seasonal stats, matching by normalized name + season.

    Tries exact match first, then normalized match (strips suffixes like II/Jr.).
    Returns the first matching row, or None with a warning if not found.
    """
    if stats_df.empty:
        print(f"  WARNING: seasonal stats DataFrame is empty; cannot match '{player_name}'")
        return None

    season_df = stats_df[stats_df["season"] == season].copy()
    if season_df.empty:
        print(f"  WARNING: no seasonal stats rows found for season {season}")
        return None

    norm_target = normalize_name(player_name)

    # Try multiple name columns in order of preference
    name_cols = [c for c in ["player_display_name", "player_name"] if c in season_df.columns]

    # 1) Exact case-insensitive match
    for col in name_cols:
        match = season_df[season_df[col].str.strip().str.lower() == player_name.lower()]
        if not match.empty:
            matched_val = match.iloc[0][col]
            print(f"  Matched '{player_name}' via {col}='{matched_val}' (exact, season {season})")
            return match.iloc[0]

    # 2) Normalized match (strips suffixes)
    for col in name_cols:
        season_df["_norm"] = season_df[col].apply(normalize_name)
        match = season_df[season_df["_norm"] == norm_target]
        if not match.empty:
            matched_val = match.iloc[0][col]
            print(f"  Matched '{player_name}' via normalized {col}='{matched_val}' (season {season})")
            return match.iloc[0]

    print(f"  WARNING: no match found for '{player_name}' in seasonal stats (season {season})")
    if name_cols:
        sample = season_df[name_cols[0]].dropna().head(10).tolist()
        print(f"  Sample names in dataset: {sample}")
    return None


def get_cpoe_from_pbp(pbp: pd.DataFrame, player_id: str, season: int) -> float:
    """Compute mean CPOE from PBP for a player, matched by player_id."""
    if not player_id or pd.isna(player_id):
        return float("nan")
    mask = (
        (pbp["season"] == season)
        & (pbp["passer_player_id"] == player_id)
        & (pbp["play_type"] == "pass")
        & (pbp["pass_attempt"] == 1)
    )
    plays = pbp.loc[mask, "cpoe"].dropna()
    if plays.empty:
        print(f"  No PBP pass plays found for player_id={player_id} (season {season}) for CPOE")
        return float("nan")
    return float(plays.mean())


def get_third_down_epa_from_pbp(pbp: pd.DataFrame, player_id: str, season: int) -> float:
    """Compute 3rd-down EPA/play from PBP, matched by player_id."""
    if not player_id or pd.isna(player_id):
        return float("nan")
    mask = (
        (pbp["season"] == season)
        & (pbp["passer_player_id"] == player_id)
        & (pbp["play_type"] == "pass")
        & (pbp["pass_attempt"] == 1)
        & (pbp["down"] == 3)
    )
    plays = pbp.loc[mask, "epa"].dropna()
    n = len(plays)
    print(f"  3rd-down pass plays found in PBP for player_id={player_id} (season {season}): {n}")
    if plays.empty:
        return float("nan")
    return float(plays.mean())


def get_headshot_url(rosters: pd.DataFrame, player_name: str, season: int) -> str:
    """Look up headshot URL from roster data by normalized name + season."""
    if rosters.empty:
        return ""

    roster_season = rosters[rosters["season"] == season].copy()
    if roster_season.empty:
        return ""

    norm_target = normalize_name(player_name)
    roster_season["_norm"] = roster_season["player_name"].apply(normalize_name)
    match = roster_season[roster_season["_norm"] == norm_target]

    if not match.empty:
        url = match.iloc[0].get("headshot_url", "")
        return str(url) if url and not pd.isna(url) else ""

    return ""


def build_metrics_table(
    pbp: pd.DataFrame,
    seasonal_stats: pd.DataFrame,
    rosters: pd.DataFrame,
    qbs: List[Dict[str, object]],
) -> pd.DataFrame:
    """Build the metrics table using seasonal stats as primary source and PBP for 3rd-down EPA."""
    rows: List[Dict[str, object]] = []

    for qb in qbs:
        player_name = str(qb["player_name"])
        season = int(qb["season"])

        print(f"\n--- Processing {player_name} (season {season}) ---")

        # Match player in seasonal stats
        stat_row = match_player_season(seasonal_stats, player_name, season)

        if stat_row is not None:
            player_id = stat_row.get("player_id", None)
            attempts = int(stat_row.get("attempts", 0) or 0)
            passing_epa = float(stat_row.get("passing_epa", float("nan")) or float("nan"))
            passing_tds = float(stat_row.get("passing_tds", float("nan")) or float("nan"))
            interceptions = float(stat_row.get("interceptions", float("nan")) or float("nan"))

            print(f"  player_id: {player_id}")
            print(f"  attempts (from seasonal stats): {attempts}")

            epa_per_play = passing_epa / attempts if attempts > 0 else float("nan")
            td_rate = passing_tds / attempts if attempts > 0 else float("nan")
            int_rate = interceptions / attempts if attempts > 0 else float("nan")

            cpoe = get_cpoe_from_pbp(pbp, player_id, season)
        else:
            player_id = None
            attempts = 0
            epa_per_play = float("nan")
            td_rate = float("nan")
            int_rate = float("nan")
            cpoe = float("nan")
            print(f"  WARNING: skipping metrics for '{player_name}' — no seasonal stats match")

        # 3rd-down EPA/play always comes from PBP (filtered by player_id)
        third_down_epa = get_third_down_epa_from_pbp(pbp, player_id, season)

        # Headshot from roster data
        headshot_url = get_headshot_url(rosters, player_name, season)

        rows.append(
            {
                "player": qb["player_display"],
                "lookup_name": player_name,
                "season": season,
                "attempts": attempts,
                "epa_per_play": epa_per_play,
                "cpoe": cpoe,
                "td_rate": td_rate,
                "int_rate": int_rate,
                "third_down_epa_per_play": third_down_epa,
                "headshot_url": headshot_url,
                "color": qb["color"],
            }
        )

    return pd.DataFrame(rows)


def safe_filename(name: str) -> str:
    return name.lower().replace(" ", "_").replace(".", "").replace("/", "_")


def download_headshot(url: str, target: Path) -> bool:
    if not url or pd.isna(url):
        return False
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
        ok = download_headshot(row.get("headshot_url"), target)
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
        labels = [f"{p} ({s})" for p, s in zip(metrics_df["player"], metrics_df["season"])]
        ax.barh(labels, values, color=metrics_df["color"])
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
            ax.text(0.5, 0.5, "Headshot unavailable", ha="center", va="center", fontsize=12, fontweight="bold")

        ax.set_title(f"{row['player']}\nSeason used: {row['season']}", fontweight="bold")

    fig.suptitle("Headshots for video package", fontsize=16, fontweight="bold")
    fig.savefig(out_dir / "qb_headshots_panel.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    qbs = build_qbs()
    seasons = sorted({int(qb["season"]) for qb in qbs})

    print(f"Loading play-by-play data for seasons: {seasons}")
    pbp = load_pbp(seasons)

    print(f"Loading seasonal player stats for seasons: {seasons}")
    seasonal_stats = load_seasonal_stats(seasons)

    print(f"Loading roster data for seasons: {seasons}")
    rosters = load_rosters(seasons)

    print("Building metrics table")
    metrics_df = build_metrics_table(pbp, seasonal_stats, rosters, qbs)
    metrics_df = save_headshots(metrics_df, out_dir)
    metrics_df.to_csv(out_dir / "qb_comparison_metrics.csv", index=False)

    print("Rendering visuals")
    render_metric_bars(metrics_df, out_dir)
    render_summary_table(metrics_df, out_dir)
    render_headshot_card(metrics_df, out_dir)

    print(f"Saved comparison outputs to {out_dir}")


if __name__ == "__main__":
    main()
