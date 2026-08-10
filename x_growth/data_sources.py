from __future__ import annotations

import glob
import math
import re

from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd


# ============================================================
# NFL ABBREVIATIONS
# ============================================================

NFL_ABBREVIATIONS = {

    "Arizona Cardinals": "ARI",
    "Atlanta Falcons": "ATL",
    "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR",
    "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN",
    "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN",
    "Detroit Lions": "DET",
    "Green Bay Packers": "GB",
    "Houston Texans": "HOU",
    "Indianapolis Colts": "IND",
    "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV",
    "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR",
    "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN",
    "New England Patriots": "NE",
    "New Orleans Saints": "NO",
    "New York Giants": "NYG",
    "New York Jets": "NYJ",
    "Philadelphia Eagles": "PHI",
    "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA",
    "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN",
    "Washington Commanders": "WAS",
}


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    value = str(value)

    value = (
        value
        .replace("\xa0", " ")
        .strip()
        .lower()
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value


def safe_value(
    value: Any,
) -> Any:
    """
    Convert pandas/numpy values into JSON-friendly Python
    objects while preserving useful numeric information.
    """

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if hasattr(
        value,
        "item",
    ):
        try:
            return value.item()
        except Exception:
            pass

    return value


def dataframe_rows(
    df: pd.DataFrame,
) -> list[dict]:

    rows = []

    for record in df.to_dict(
        orient="records"
    ):

        clean = {
            str(key): safe_value(value)
            for key, value
            in record.items()
        }

        rows.append(clean)

    return rows


# ============================================================
# TEAM SEARCH TERMS
# ============================================================

def build_team_search_terms(
    team: dict,
) -> list[str]:

    terms = []

    for key in [
        "team",
        "btb_team_short",
        "mascot",
        "matched_alias",
    ]:

        value = team.get(key)

        if value:
            terms.append(
                str(value)
            )

    return list(
        dict.fromkeys(
            term
            for term in terms
            if term.strip()
        )
    )


def nfl_abbreviation(
    team: dict,
) -> str | None:

    team_name = (
        team.get("team")
    )

    if not team_name:
        return None

    return NFL_ABBREVIATIONS.get(
        team_name
    )


# ============================================================
# ROW MATCHING
# ============================================================

def _value_matches_terms(
    value: Any,
    terms: list[str],
) -> bool:

    value_normalized = (
        normalize_text(value)
    )

    if not value_normalized:
        return False

    for term in terms:

        term_normalized = (
            normalize_text(term)
        )

        if not term_normalized:
            continue

        if (
            value_normalized
            == term_normalized
        ):
            return True

    return False


def find_matching_rows(
    df: pd.DataFrame,
    team: dict,
    team_columns: list[str] | None = None,
    max_rows: int = 3,
) -> list[dict]:

    if df.empty:
        return []

    terms = (
        build_team_search_terms(
            team
        )
    )

    sport = str(
        team.get(
            "sport",
            "",
        )
    ).upper()

    if sport == "NFL":

        abbreviation = (
            nfl_abbreviation(
                team
            )
        )

        if abbreviation:
            terms.append(
                abbreviation
            )

    columns = list(
        df.columns
    )

    if team_columns:

        candidate_columns = [
            column
            for column
            in team_columns
            if column in columns
        ]

    else:

        candidate_columns = []

    if not candidate_columns:

        common_names = [
            "team",
            "Team",
            "TEAM",
            "school",
            "School",
            "team_name",
            "btb_team",
            "btb_team_short",
        ]

        candidate_columns = [
            column
            for column
            in common_names
            if column in columns
        ]

    if not candidate_columns:
        return []

    mask = pd.Series(
        False,
        index=df.index,
    )

    for column in candidate_columns:

        column_mask = (
            df[column]
            .apply(
                lambda value:
                    _value_matches_terms(
                        value,
                        terms,
                    )
            )
        )

        mask = (
            mask
            | column_mask
        )

    matched = (
        df.loc[mask]
        .head(max_rows)
    )

    return dataframe_rows(
        matched
    )


# ============================================================
# CSV
# ============================================================

def load_csv_team_rows(
    path: Path,
    team: dict,
    team_column: str | None = None,
    max_rows: int = 3,
) -> list[dict]:

    if not path.exists():
        return []

    try:

        df = pd.read_csv(
            path
        )

    except Exception as exc:

        print(
            f"Could not read CSV "
            f"{path}: {exc}"
        )

        return []

    columns = (
        [team_column]
        if team_column
        else None
    )

    return find_matching_rows(
        df=df,
        team=team,
        team_columns=columns,
        max_rows=max_rows,
    )


# ============================================================
# HTML FUTURES TABLE
# ============================================================

def load_html_team_rows(
    path: Path,
    team: dict,
    team_columns: list[str] | None = None,
    max_rows: int = 3,
) -> list[dict]:

    if not path.exists():
        return []

    try:

        html = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

    except Exception as exc:

        print(
            f"Could not read HTML "
            f"{path}: {exc}"
        )

        return []

    try:

        tables = pd.read_html(
            StringIO(html)
        )

    except ValueError:

        # No real HTML <table> found.
        print(
            f"No static HTML tables found "
            f"in {path}."
        )

        return []

    except Exception as exc:

        print(
            f"Could not parse HTML "
            f"{path}: {exc}"
        )

        return []

    all_matches = []

    for table_number, df in enumerate(
        tables,
        start=1,
    ):

        # Flatten multi-level headers if needed.
        if isinstance(
            df.columns,
            pd.MultiIndex,
        ):

            df.columns = [
                " ".join(
                    str(item)
                    for item in column
                    if str(item) != "nan"
                ).strip()

                for column
                in df.columns
            ]

        matches = (
            find_matching_rows(
                df=df,
                team=team,
                team_columns=(
                    team_columns
                ),
                max_rows=max_rows,
            )
        )

        for match in matches:

            match[
                "_html_table_number"
            ] = table_number

            all_matches.append(
                match
            )

        if len(
            all_matches
        ) >= max_rows:

            break

    return (
        all_matches[
            :max_rows
        ]
    )


# ============================================================
# PARQUET
# ============================================================

def load_parquet_team_rows(
    path: Path,
    team: dict,
    team_columns: list[str] | None = None,
    max_rows: int = 3,
) -> list[dict]:

    if not path.exists():
        return []

    try:

        df = pd.read_parquet(
            path
        )

    except Exception as exc:

        print(
            f"Could not read parquet "
            f"{path}: {exc}"
        )

        return []

    return find_matching_rows(
        df=df,
        team=team,
        team_columns=team_columns,
        max_rows=max_rows,
    )


# ============================================================
# MARKDOWN / MASTER DOCUMENT SEARCH
# ============================================================

def _extract_text_window(
    text: str,
    search_terms: list[str],
    window: int = 1400,
) -> str | None:

    lower = (
        text.lower()
    )

    best_position = None

    for term in search_terms:

        term = term.strip()

        if not term:
            continue

        position = lower.find(
            term.lower()
        )

        if position >= 0:

            if (
                best_position is None
                or position < best_position
            ):

                best_position = (
                    position
                )

    if best_position is None:
        return None

    start = max(
        0,
        best_position
        - window // 3,
    )

    end = min(
        len(text),
        best_position
        + window,
    )

    return (
        text[start:end]
        .strip()
    )


def load_text_glob_context(
    root: Path,
    pattern: str,
    team: dict,
    max_characters: int = 4500,
) -> list[dict]:

    absolute_pattern = str(
        root / pattern
    )

    paths = sorted(
        glob.glob(
            absolute_pattern,
            recursive=True,
        ),
        reverse=True,
    )

    search_terms = (
        build_team_search_terms(
            team
        )
    )

    contexts = []

    total_characters = 0

    for filename in paths:

        path = Path(
            filename
        )

        if not path.is_file():
            continue

        try:

            text = path.read_text(
                encoding="utf-8",
                errors="ignore",
            )

        except Exception:
            continue

        excerpt = (
            _extract_text_window(
                text=text,
                search_terms=search_terms,
            )
        )

        if not excerpt:
            continue

        remaining = (
            max_characters
            - total_characters
        )

        if remaining <= 0:
            break

        excerpt = (
            excerpt[:remaining]
        )

        contexts.append(
            {
                "file":
                    str(
                        path.relative_to(
                            root
                        )
                    ),

                "excerpt":
                    excerpt,
            }
        )

        total_characters += (
            len(excerpt)
        )

    return contexts


# ============================================================
# GENERIC SOURCE LOADER
# ============================================================

def load_source_for_team(
    root: Path,
    source_name: str,
    source_config: dict,
    team: dict,
    max_rows: int = 3,
    max_note_characters: int = 4500,
) -> list[dict]:

    source_type = (
        source_config.get(
            "type"
        )
    )

    if source_type == (
        "text_glob"
    ):

        pattern = (
            source_config.get(
                "glob"
            )
        )

        if not pattern:
            return []

        return (
            load_text_glob_context(
                root=root,
                pattern=pattern,
                team=team,
                max_characters=(
                    max_note_characters
                ),
            )
        )

    relative_path = (
        source_config.get(
            "path"
        )
    )

    if not relative_path:
        return []

    path = (
        root
        / relative_path
    )

    team_columns = (
        source_config.get(
            "team_columns"
        )
    )

    if source_type == "csv":

        return load_csv_team_rows(
            path=path,
            team=team,
            team_column=(
                source_config.get(
                    "team_column"
                )
            ),
            max_rows=max_rows,
        )

    if source_type == "html":

        return load_html_team_rows(
            path=path,
            team=team,
            team_columns=(
                team_columns
            ),
            max_rows=max_rows,
        )

    if source_type == "parquet":

        return load_parquet_team_rows(
            path=path,
            team=team,
            team_columns=(
                team_columns
            ),
            max_rows=max_rows,
        )

    print(
        f"Unknown data-source type "
        f"'{source_type}' "
        f"for {source_name}."
    )

    return []
