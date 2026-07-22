#!/usr/bin/env python3
"""
Download and standardize college-football team logos.

Input:
    CFB Teams Full Crosswalk.csv

Required columns:
    cfbfastr_team
    logo

Optional columns:
    team_id
    conference
    btb_team
    btb_team_short
    mascot
    api_team

Outputs:
    assets/team_logos/<team-slug>.png
    data/processed/team_logo_map.csv
    data/processed/team_logo_download_report.csv

Examples:
    python scripts/build_team_logo_assets.py

    python scripts/build_team_logo_assets.py --refresh

    python scripts/build_team_logo_assets.py \
        --crosswalk "CFB Teams Full Crosswalk.csv" \
        --max-size 500
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from PIL import Image, UnidentifiedImageError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CROSSWALK = REPO_ROOT / "CFB Teams Full Crosswalk.csv"
DEFAULT_LOGO_DIRECTORY = REPO_ROOT / "assets" / "team_logos"
DEFAULT_MAPPING_FILE = (
    REPO_ROOT / "data" / "processed" / "team_logo_map.csv"
)
DEFAULT_REPORT_FILE = (
    REPO_ROOT
    / "data"
    / "processed"
    / "team_logo_download_report.csv"
)

TEAM_COLUMN = "cfbfastr_team"
LOGO_URL_COLUMN = "logo"
TEAM_ID_COLUMN = "team_id"

REQUEST_TIMEOUT_SECONDS = 30
REQUEST_DELAY_SECONDS = 0.05
DEFAULT_MAX_LOGO_SIZE = 500

USER_AGENT = (
    "Mozilla/5.0 (compatible; "
    "BTB-Analytics-CFB-Logo-Downloader/1.0)"
)


@dataclass
class LogoResult:
    """One logo-download result."""

    team: str
    team_id: str | None
    logo_url: str
    logo_filename: str | None
    logo_path: str | None
    status: str
    message: str
    width: int | None = None
    height: int | None = None
    bytes_written: int | None = None


def configure_logging(verbose: bool = False) -> None:
    """Configure console logging."""

    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def parse_arguments() -> argparse.Namespace:
    """Read command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Download CFB team logos from the full crosswalk "
            "and store them as local PNG files."
        )
    )

    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=DEFAULT_CROSSWALK,
        help=(
            "Path to CFB Teams Full Crosswalk.csv. "
            f"Default: {DEFAULT_CROSSWALK}"
        ),
    )

    parser.add_argument(
        "--logo-directory",
        type=Path,
        default=DEFAULT_LOGO_DIRECTORY,
        help=(
            "Directory where PNG logos will be stored. "
            f"Default: {DEFAULT_LOGO_DIRECTORY}"
        ),
    )

    parser.add_argument(
        "--mapping-file",
        type=Path,
        default=DEFAULT_MAPPING_FILE,
        help=(
            "Output team-to-logo mapping CSV. "
            f"Default: {DEFAULT_MAPPING_FILE}"
        ),
    )

    parser.add_argument(
        "--report-file",
        type=Path,
        default=DEFAULT_REPORT_FILE,
        help=(
            "Output download report CSV. "
            f"Default: {DEFAULT_REPORT_FILE}"
        ),
    )

    parser.add_argument(
        "--max-size",
        type=int,
        default=DEFAULT_MAX_LOGO_SIZE,
        help=(
            "Maximum PNG width or height in pixels. "
            f"Default: {DEFAULT_MAX_LOGO_SIZE}"
        ),
    )

    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Redownload logos even when local files already exist.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show additional diagnostic logging.",
    )

    return parser.parse_args()


def build_requests_session() -> requests.Session:
    """Create a retry-capable HTTP session."""

    retry_policy = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=0.75,
        status_forcelist=[
            408,
            429,
            500,
            502,
            503,
            504,
        ],
        allowed_methods=["GET"],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry_policy,
        pool_connections=10,
        pool_maxsize=10,
    )

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": (
                "image/avif,image/webp,image/apng,"
                "image/svg+xml,image/*,*/*;q=0.8"
            ),
        }
    )

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def clean_text(value: Any) -> str | None:
    """Convert a CSV value to clean text or None."""

    if value is None or pd.isna(value):
        return None

    cleaned = str(value).strip()

    if not cleaned:
        return None

    return cleaned


def clean_team_id(value: Any) -> str | None:
    """Normalize numeric-looking team IDs."""

    cleaned = clean_text(value)

    if cleaned is None:
        return None

    if re.fullmatch(r"\d+\.0", cleaned):
        cleaned = cleaned[:-2]

    return cleaned


def slugify_team_name(team_name: str) -> str:
    """
    Convert a team name into a filesystem-safe slug.

    Examples:
        Alabama Crimson Tide -> alabama-crimson-tide
        Hawai'i Rainbow Warriors -> hawaii-rainbow-warriors
    """

    normalized = team_name.lower()

    normalized = (
        normalized.replace("’", "")
        .replace("'", "")
        .replace("&", " and ")
    )

    normalized = re.sub(
        r"[^a-z0-9]+",
        "-",
        normalized,
    )

    normalized = normalized.strip("-")

    if normalized:
        return normalized

    digest = hashlib.sha256(
        team_name.encode("utf-8")
    ).hexdigest()[:12]

    return f"team-{digest}"


def relative_repo_path(path: Path) -> str:
    """
    Return a portable repository-relative path when possible.
    """

    resolved_path = path.resolve()

    try:
        return resolved_path.relative_to(
            REPO_ROOT.resolve()
        ).as_posix()
    except ValueError:
        return resolved_path.as_posix()


def validate_crosswalk(
    crosswalk: pd.DataFrame,
) -> None:
    """Confirm the required crosswalk columns exist."""

    required_columns = {
        TEAM_COLUMN,
        LOGO_URL_COLUMN,
    }

    missing_columns = required_columns.difference(
        crosswalk.columns
    )

    if missing_columns:
        raise ValueError(
            "Crosswalk is missing required columns: "
            + ", ".join(sorted(missing_columns))
        )


def open_downloaded_image(
    content: bytes,
    source_url: str,
) -> Image.Image:
    """Open downloaded image bytes using Pillow."""

    try:
        image = Image.open(BytesIO(content))
        image.load()
        return image
    except UnidentifiedImageError as error:
        raise ValueError(
            "Downloaded content was not a Pillow-compatible "
            f"image: {source_url}"
        ) from error


def standardize_logo(
    image: Image.Image,
    *,
    max_size: int,
) -> Image.Image:
    """
    Standardize an image for consistent transparent PNG output.

    The aspect ratio is preserved. The logo is not stretched.
    """

    if max_size < 50 or max_size > 2000:
        raise ValueError(
            "max_size must be between 50 and 2000 pixels."
        )

    # Preserve transparency when present.
    if image.mode not in {"RGBA", "LA"}:
        image = image.convert("RGBA")
    else:
        image = image.convert("RGBA")

    image.thumbnail(
        (max_size, max_size),
        Image.Resampling.LANCZOS,
    )

    return image


def save_png(
    image: Image.Image,
    output_path: Path,
) -> int:
    """Save an image atomically as an optimized PNG."""

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = output_path.with_suffix(
        ".tmp.png"
    )

    image.save(
        temporary_path,
        format="PNG",
        optimize=True,
    )

    temporary_path.replace(output_path)

    return output_path.stat().st_size


def download_logo(
    *,
    session: requests.Session,
    team: str,
    team_id: str | None,
    logo_url: str,
    output_path: Path,
    max_size: int,
    refresh: bool,
) -> LogoResult:
    """Download one logo and save it as a local PNG."""

    relative_path = relative_repo_path(output_path)

    if output_path.exists() and not refresh:
        try:
            with Image.open(output_path) as existing:
                existing.load()

                return LogoResult(
                    team=team,
                    team_id=team_id,
                    logo_url=logo_url,
                    logo_filename=output_path.name,
                    logo_path=relative_path,
                    status="existing",
                    message="Existing valid PNG retained.",
                    width=int(existing.width),
                    height=int(existing.height),
                    bytes_written=output_path.stat().st_size,
                )
        except Exception:
            logging.warning(
                "%s has an invalid local logo; redownloading.",
                team,
            )

    try:
        response = session.get(
            logo_url,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        if response.status_code != 200:
            return LogoResult(
                team=team,
                team_id=team_id,
                logo_url=logo_url,
                logo_filename=None,
                logo_path=None,
                status="failed",
                message=(
                    f"HTTP {response.status_code} returned "
                    "by logo server."
                ),
            )

        content = response.content

        if not content:
            return LogoResult(
                team=team,
                team_id=team_id,
                logo_url=logo_url,
                logo_filename=None,
                logo_path=None,
                status="failed",
                message="Logo response contained no data.",
            )

        image = open_downloaded_image(
            content,
            logo_url,
        )

        standardized = standardize_logo(
            image,
            max_size=max_size,
        )

        bytes_written = save_png(
            standardized,
            output_path,
        )

        return LogoResult(
            team=team,
            team_id=team_id,
            logo_url=logo_url,
            logo_filename=output_path.name,
            logo_path=relative_path,
            status="downloaded",
            message="Logo downloaded and saved as PNG.",
            width=int(standardized.width),
            height=int(standardized.height),
            bytes_written=bytes_written,
        )

    except requests.RequestException as error:
        return LogoResult(
            team=team,
            team_id=team_id,
            logo_url=logo_url,
            logo_filename=None,
            logo_path=None,
            status="failed",
            message=f"Request failed: {error}",
        )

    except Exception as error:
        return LogoResult(
            team=team,
            team_id=team_id,
            logo_url=logo_url,
            logo_filename=None,
            logo_path=None,
            status="failed",
            message=f"Image processing failed: {error}",
        )


def build_mapping_row(
    source_row: pd.Series,
    result: LogoResult,
) -> dict[str, Any]:
    """Create one cleaned team-to-logo mapping record."""

    mapping = {
        "cfbfastr_team": result.team,
        "team_id": result.team_id,
        "logo_path": result.logo_path,
        "logo_filename": result.logo_filename,
        "logo_url": result.logo_url,
        "logo_status": result.status,
        "logo_width": result.width,
        "logo_height": result.height,
        "logo_bytes": result.bytes_written,
    }

    optional_columns = [
        "btb_team_short",
        "mascot",
        "btb_team",
        "api_team",
        "conference",
    ]

    for column in optional_columns:
        if column in source_row.index:
            mapping[column] = clean_text(
                source_row[column]
            )

    return mapping


def main() -> int:
    """Run the complete logo-download pipeline."""

    arguments = parse_arguments()
    configure_logging(arguments.verbose)

    crosswalk_path = arguments.crosswalk.resolve()
    logo_directory = arguments.logo_directory.resolve()
    mapping_file = arguments.mapping_file.resolve()
    report_file = arguments.report_file.resolve()

    if not crosswalk_path.exists():
        logging.error(
            "Crosswalk file was not found: %s",
            crosswalk_path,
        )
        return 1

    logging.info(
        "Reading crosswalk: %s",
        crosswalk_path,
    )

    crosswalk = pd.read_csv(
        crosswalk_path,
        dtype=str,
    )

    try:
        validate_crosswalk(crosswalk)
    except ValueError as error:
        logging.error("%s", error)
        return 1

    # Remove rows without a team or URL.
    crosswalk[TEAM_COLUMN] = (
        crosswalk[TEAM_COLUMN]
        .astype("string")
        .str.strip()
    )

    crosswalk[LOGO_URL_COLUMN] = (
        crosswalk[LOGO_URL_COLUMN]
        .astype("string")
        .str.strip()
    )

    usable = crosswalk[
        crosswalk[TEAM_COLUMN].notna()
        & crosswalk[LOGO_URL_COLUMN].notna()
        & (crosswalk[TEAM_COLUMN] != "")
        & (crosswalk[LOGO_URL_COLUMN] != "")
    ].copy()

    duplicate_count = int(
        usable.duplicated(
            subset=[TEAM_COLUMN],
            keep="first",
        ).sum()
    )

    if duplicate_count:
        logging.warning(
            "Found %d duplicate cfbfastr_team rows. "
            "The first logo URL for each team will be used.",
            duplicate_count,
        )

    usable = usable.drop_duplicates(
        subset=[TEAM_COLUMN],
        keep="first",
    )

    usable = usable.sort_values(
        TEAM_COLUMN,
        kind="stable",
    ).reset_index(drop=True)

    logo_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    mapping_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    logging.info(
        "Teams with usable logo URLs: %d",
        len(usable),
    )

    logging.info(
        "Logo directory: %s",
        logo_directory,
    )

    session = build_requests_session()

    results: list[LogoResult] = []
    mapping_rows: list[dict[str, Any]] = []

    total = len(usable)

    for index, source_row in usable.iterrows():
        team = clean_text(
            source_row[TEAM_COLUMN]
        )

        logo_url = clean_text(
            source_row[LOGO_URL_COLUMN]
        )

        team_id = (
            clean_team_id(source_row[TEAM_ID_COLUMN])
            if TEAM_ID_COLUMN in usable.columns
            else None
        )

        if team is None or logo_url is None:
            continue

        slug = slugify_team_name(team)
        output_path = logo_directory / f"{slug}.png"

        logging.info(
            "[%d/%d] %s",
            index + 1,
            total,
            team,
        )

        result = download_logo(
            session=session,
            team=team,
            team_id=team_id,
            logo_url=logo_url,
            output_path=output_path,
            max_size=arguments.max_size,
            refresh=arguments.refresh,
        )

        results.append(result)

        mapping_rows.append(
            build_mapping_row(
                source_row,
                result,
            )
        )

        if result.status == "failed":
            logging.warning(
                "Failed: %s — %s",
                team,
                result.message,
            )

        time.sleep(REQUEST_DELAY_SECONDS)

    session.close()

    report = pd.DataFrame(
        [
            {
                "cfbfastr_team": result.team,
                "team_id": result.team_id,
                "logo_url": result.logo_url,
                "logo_filename": result.logo_filename,
                "logo_path": result.logo_path,
                "status": result.status,
                "message": result.message,
                "width": result.width,
                "height": result.height,
                "bytes_written": result.bytes_written,
            }
            for result in results
        ]
    )

    mapping = pd.DataFrame(mapping_rows)

    if not mapping.empty:
        successful_statuses = {
            "downloaded",
            "existing",
        }

        mapping = mapping[
            mapping["logo_status"].isin(
                successful_statuses
            )
            & mapping["logo_path"].notna()
        ].copy()

        preferred_columns = [
            "cfbfastr_team",
            "team_id",
            "conference",
            "btb_team",
            "btb_team_short",
            "mascot",
            "api_team",
            "logo_path",
            "logo_filename",
            "logo_url",
            "logo_status",
            "logo_width",
            "logo_height",
            "logo_bytes",
        ]

        existing_columns = [
            column
            for column in preferred_columns
            if column in mapping.columns
        ]

        mapping = mapping[existing_columns]

        mapping = mapping.sort_values(
            "cfbfastr_team",
            kind="stable",
        )

    mapping.to_csv(
        mapping_file,
        index=False,
    )

    report.to_csv(
        report_file,
        index=False,
    )

    downloaded_count = sum(
        result.status == "downloaded"
        for result in results
    )

    existing_count = sum(
        result.status == "existing"
        for result in results
    )

    failed_count = sum(
        result.status == "failed"
        for result in results
    )

    logging.info("")
    logging.info("Logo build complete.")
    logging.info("Downloaded: %d", downloaded_count)
    logging.info("Existing: %d", existing_count)
    logging.info("Failed: %d", failed_count)
    logging.info("Mapping rows: %d", len(mapping))
    logging.info("Mapping file: %s", mapping_file)
    logging.info("Report file: %s", report_file)

    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
