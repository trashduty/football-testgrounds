#!/usr/bin/env python3
"""
Download and standardize college-football team logos.

Input:
    CFB Teams Full Crosswalk.csv

Matching logic:
    btb_team_short is the chart/PBP team name:
        Alabama
        Ohio State
        Arizona State

    cfbfastr_team is retained as the full team name:
        Alabama Crimson Tide
        Ohio State Buckeyes
        Arizona State Sun Devils

Outputs:
    assets/team_logos/<short-team-slug>.png
    data/processed/team_logo_map.csv
    data/processed/team_logo_download_report.csv

Usage:
    python scripts/build_team_logo_assets.py

Force every logo to redownload:
    python scripts/build_team_logo_assets.py --refresh
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass
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
    REPO_ROOT
    / "data"
    / "processed"
    / "team_logo_map.csv"
)

DEFAULT_REPORT_FILE = (
    REPO_ROOT
    / "data"
    / "processed"
    / "team_logo_download_report.csv"
)

CHART_TEAM_COLUMN = "btb_team_short"
FULL_TEAM_COLUMN = "cfbfastr_team"
LOGO_URL_COLUMN = "logo"
TEAM_ID_COLUMN = "team_id"

REQUEST_TIMEOUT_SECONDS = 30
REQUEST_DELAY_SECONDS = 0.05
DEFAULT_MAX_LOGO_SIZE = 500

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; BTB-Analytics-CFB-Logo-Downloader/1.0)"
)


@dataclass
class LogoResult:
    team: str
    cfbfastr_team: str | None
    team_id: str | None
    conference: str | None
    mascot: str | None
    logo_url: str
    logo_filename: str | None
    logo_path: str | None
    status: str
    message: str
    width: int | None = None
    height: int | None = None
    bytes_written: int | None = None


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download CFB team logos and create a chart-team "
            "to local-logo mapping."
        )
    )

    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=DEFAULT_CROSSWALK,
    )

    parser.add_argument(
        "--logo-directory",
        type=Path,
        default=DEFAULT_LOGO_DIRECTORY,
    )

    parser.add_argument(
        "--mapping-file",
        type=Path,
        default=DEFAULT_MAPPING_FILE,
    )

    parser.add_argument(
        "--report-file",
        type=Path,
        default=DEFAULT_REPORT_FILE,
    )

    parser.add_argument(
        "--max-size",
        type=int,
        default=DEFAULT_MAX_LOGO_SIZE,
    )

    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Redownload logos even when a valid local PNG exists.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
    )

    return parser.parse_args()


def build_requests_session() -> requests.Session:
    retries = Retry(
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
        max_retries=retries,
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
    if value is None or pd.isna(value):
        return None

    cleaned = str(value).strip()

    if not cleaned or cleaned.lower() == "nan":
        return None

    return cleaned


def clean_team_id(value: Any) -> str | None:
    cleaned = clean_text(value)

    if cleaned is None:
        return None

    if re.fullmatch(r"\d+\.0", cleaned):
        return cleaned[:-2]

    return cleaned


def slugify(value: str) -> str:
    normalized = value.lower()

    normalized = (
        normalized
        .replace("’", "")
        .replace("'", "")
        .replace("&", " and ")
    )

    normalized = re.sub(
        r"[^a-z0-9]+",
        "-",
        normalized,
    ).strip("-")

    if normalized:
        return normalized

    digest = hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()[:12]

    return f"team-{digest}"


def relative_repo_path(path: Path) -> str:
    resolved = path.resolve()

    try:
        return resolved.relative_to(
            REPO_ROOT.resolve()
        ).as_posix()
    except ValueError:
        return resolved.as_posix()


def validate_crosswalk(crosswalk: pd.DataFrame) -> None:
    required = {
        CHART_TEAM_COLUMN,
        FULL_TEAM_COLUMN,
        LOGO_URL_COLUMN,
    }

    missing = required.difference(crosswalk.columns)

    if missing:
        raise ValueError(
            "Crosswalk is missing required columns: "
            + ", ".join(sorted(missing))
        )


def open_downloaded_image(
    content: bytes,
    source_url: str,
) -> Image.Image:
    try:
        image = Image.open(BytesIO(content))
        image.load()
        return image
    except UnidentifiedImageError as error:
        raise ValueError(
            "Downloaded content was not a recognized image: "
            f"{source_url}"
        ) from error


def standardize_logo(
    image: Image.Image,
    max_size: int,
) -> Image.Image:
    if max_size < 50 or max_size > 2000:
        raise ValueError(
            "max_size must be between 50 and 2000."
        )

    standardized = image.convert("RGBA")

    standardized.thumbnail(
        (max_size, max_size),
        Image.Resampling.LANCZOS,
    )

    return standardized


def save_png(
    image: Image.Image,
    output_path: Path,
) -> int:
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


def inspect_existing_png(
    output_path: Path,
) -> tuple[int, int, int] | None:
    if not output_path.exists():
        return None

    try:
        with Image.open(output_path) as image:
            image.load()

            return (
                int(image.width),
                int(image.height),
                int(output_path.stat().st_size),
            )
    except Exception:
        return None


def download_logo(
    *,
    session: requests.Session,
    team: str,
    cfbfastr_team: str | None,
    team_id: str | None,
    conference: str | None,
    mascot: str | None,
    logo_url: str,
    output_path: Path,
    max_size: int,
    refresh: bool,
) -> LogoResult:
    relative_path = relative_repo_path(output_path)

    if not refresh:
        existing = inspect_existing_png(output_path)

        if existing is not None:
            width, height, bytes_written = existing

            return LogoResult(
                team=team,
                cfbfastr_team=cfbfastr_team,
                team_id=team_id,
                conference=conference,
                mascot=mascot,
                logo_url=logo_url,
                logo_filename=output_path.name,
                logo_path=relative_path,
                status="existing",
                message="Existing valid PNG retained.",
                width=width,
                height=height,
                bytes_written=bytes_written,
            )

    try:
        response = session.get(
            logo_url,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        if response.status_code != 200:
            return LogoResult(
                team=team,
                cfbfastr_team=cfbfastr_team,
                team_id=team_id,
                conference=conference,
                mascot=mascot,
                logo_url=logo_url,
                logo_filename=None,
                logo_path=None,
                status="failed",
                message=(
                    f"Logo server returned HTTP "
                    f"{response.status_code}."
                ),
            )

        if not response.content:
            return LogoResult(
                team=team,
                cfbfastr_team=cfbfastr_team,
                team_id=team_id,
                conference=conference,
                mascot=mascot,
                logo_url=logo_url,
                logo_filename=None,
                logo_path=None,
                status="failed",
                message="Logo response was empty.",
            )

        image = open_downloaded_image(
            response.content,
            logo_url,
        )

        standardized = standardize_logo(
            image,
            max_size,
        )

        bytes_written = save_png(
            standardized,
            output_path,
        )

        return LogoResult(
            team=team,
            cfbfastr_team=cfbfastr_team,
            team_id=team_id,
            conference=conference,
            mascot=mascot,
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
        message = f"Request failed: {error}"

    except Exception as error:
        message = f"Image processing failed: {error}"

    return LogoResult(
        team=team,
        cfbfastr_team=cfbfastr_team,
        team_id=team_id,
        conference=conference,
        mascot=mascot,
        logo_url=logo_url,
        logo_filename=None,
        logo_path=None,
        status="failed",
        message=message,
    )


def main() -> int:
    args = parse_arguments()
    configure_logging(args.verbose)

    crosswalk_path = args.crosswalk.resolve()
    logo_directory = args.logo_directory.resolve()
    mapping_file = args.mapping_file.resolve()
    report_file = args.report_file.resolve()

    if not crosswalk_path.exists():
        logging.error(
            "Crosswalk file not found: %s",
            crosswalk_path,
        )
        return 1

    crosswalk = pd.read_csv(
        crosswalk_path,
        dtype=str,
    )

    try:
        validate_crosswalk(crosswalk)
    except ValueError as error:
        logging.error("%s", error)
        return 1

    for column in crosswalk.columns:
        crosswalk[column] = (
            crosswalk[column]
            .astype("string")
            .str.strip()
        )

    usable = crosswalk[
        crosswalk[CHART_TEAM_COLUMN].notna()
        & crosswalk[LOGO_URL_COLUMN].notna()
        & (crosswalk[CHART_TEAM_COLUMN] != "")
        & (crosswalk[LOGO_URL_COLUMN] != "")
    ].copy()

    duplicate_count = int(
        usable.duplicated(
            subset=[CHART_TEAM_COLUMN],
            keep="first",
        ).sum()
    )

    if duplicate_count:
        logging.warning(
            "Found %d duplicate chart-team names. "
            "The first row for each team will be used.",
            duplicate_count,
        )

    usable = (
        usable
        .drop_duplicates(
            subset=[CHART_TEAM_COLUMN],
            keep="first",
        )
        .sort_values(
            CHART_TEAM_COLUMN,
            kind="stable",
        )
        .reset_index(drop=True)
    )

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

    session = build_requests_session()
    results: list[LogoResult] = []

    total = len(usable)

    for index, row in usable.iterrows():
        team = clean_text(
            row.get(CHART_TEAM_COLUMN)
        )

        full_team = clean_text(
            row.get(FULL_TEAM_COLUMN)
        )

        logo_url = clean_text(
            row.get(LOGO_URL_COLUMN)
        )

        team_id = clean_team_id(
            row.get(TEAM_ID_COLUMN)
        )

        conference = clean_text(
            row.get("conference")
        )

        mascot = clean_text(
            row.get("mascot")
        )

        if team is None or logo_url is None:
            continue

        # Filename is based on the chart/PBP team name.
        output_path = (
            logo_directory
            / f"{slugify(team)}.png"
        )

        logging.info(
            "[%d/%d] %s",
            index + 1,
            total,
            team,
        )

        result = download_logo(
            session=session,
            team=team,
            cfbfastr_team=full_team,
            team_id=team_id,
            conference=conference,
            mascot=mascot,
            logo_url=logo_url,
            output_path=output_path,
            max_size=args.max_size,
            refresh=args.refresh,
        )

        results.append(result)

        if result.status == "failed":
            logging.warning(
                "%s: %s",
                team,
                result.message,
            )

        time.sleep(REQUEST_DELAY_SECONDS)

    session.close()

    report = pd.DataFrame(
        [asdict(result) for result in results]
    )

    successful = report[
        report["status"].isin(
            ["downloaded", "existing"]
        )
        & report["logo_path"].notna()
    ].copy()

    mapping_columns = [
        "team",
        "cfbfastr_team",
        "team_id",
        "conference",
        "mascot",
        "logo_path",
        "logo_filename",
        "logo_url",
        "status",
        "width",
        "height",
        "bytes_written",
    ]

    mapping = successful[
        [
            column
            for column in mapping_columns
            if column in successful.columns
        ]
    ].copy()

    mapping = mapping.rename(
        columns={
            "status": "logo_status",
            "width": "logo_width",
            "height": "logo_height",
            "bytes_written": "logo_bytes",
        }
    )

    mapping = mapping.sort_values(
        "team",
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

    downloaded_count = int(
        (report["status"] == "downloaded").sum()
    )

    existing_count = int(
        (report["status"] == "existing").sum()
    )

    failed_count = int(
        (report["status"] == "failed").sum()
    )

    logging.info("")
    logging.info("Logo build complete.")
    logging.info("Downloaded: %d", downloaded_count)
    logging.info("Existing: %d", existing_count)
    logging.info("Failed: %d", failed_count)
    logging.info("Mapped teams: %d", len(mapping))
    logging.info("Mapping: %s", mapping_file)
    logging.info("Report: %s", report_file)

    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
