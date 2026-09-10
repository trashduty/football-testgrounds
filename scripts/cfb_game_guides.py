#!/usr/bin/env python3
"""
CFB game-guide ingestion + BTB narrative generation.

Responsibilities:
1. Resolve media-guide PDFs using crosswalk.btb_team_short.
2. Expected guide naming convention:
      game_guides/{season}/{btb_team_short}.pdf

   Example:
      btb_team_short = "Syracuse"
      -> game_guides/2026/Syracuse.pdf

3. Extract text from available PDFs.
4. Use OpenAI to convert each guide into structured, source-grounded facts.
5. Identify the most meaningful statistical matchup angles.
6. Generate concise BTB-style narrative from:
      - verified guide facts
      - deterministic BTB/CFBD statistics
      - model/market information
7. Preserve source metadata for auditing.

IMPORTANT:
The language model NEVER determines whether a wager is a bet/pass.
That remains deterministic in weekly_matchup_articles_cfb.py.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from openai import OpenAI
from pypdf import PdfReader


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_OPENAI_MODEL = os.getenv(
    "OPENAI_ARTICLE_MODEL",
    "gpt-5.6",
)

# Prevent gigantic guides from producing unnecessarily large API requests.
MAX_GUIDE_CHARS = 110_000


# --------------------------------------------------------------------------- #
# BTB writing instructions
# --------------------------------------------------------------------------- #

BTB_SYSTEM_PROMPT = """
You are an editorial analyst writing for BTB Analytics.

BTB Analytics uses quantitative models to identify differences between its
expectation and the betting market.

The writing should sound sharp, skeptical, informed, and readable without
sounding academic, robotic, promotional, or like a tout.

CORE BTB PHILOSOPHY

The question is not simply:

"Which team is better?"

The question is:

"Is the market price consistent with what we know about these teams?"

Carefully distinguish between:

1. What happened.
2. What could matter going forward.
3. What the market already appears to price in.

Recent performance is context, not proof.

Never imply:
- a team will win because it won last week
- a team will lose because it lost last week
- one isolated statistic guarantees future performance
- a matchup is easy
- a wager is guaranteed
- a team is "due"

NEVER USE:
- lock
- hammer
- guaranteed
- easy money
- can't miss
- free money
- mortal lock
- slam
- smash

VOICE

Prefer language such as:
- "The more interesting part is..."
- "That matters because..."
- "The market is asking..."
- "The question is whether..."
- "What stands out here is..."
- "That does not automatically mean..."
- "The model is not reacting to..."
- "The useful takeaway is..."
- "Where this gets interesting..."
- "The price matters because..."

Avoid generic sports-preview filler such as:
- "enters this game with momentum"
- "will look to build on"
- "should be an exciting matchup"
- "both teams will be looking to"
- "keys to victory"
- "set the tone"
- "must establish the run"
- "needs to execute"

ANALYTICS STYLE

Use analytics to support an argument rather than overwhelm the reader.

Do not repeat every statistic supplied.

Prioritize two or three matchup ideas that help explain why BTB's expectation
may differ from the betting market.

If the guide contains a dramatic recent result, distinguish the final score
from the underlying information that may actually matter.

SOURCE DISCIPLINE

You may ONLY state guide-derived facts that appear in the supplied verified
facts.

Never invent:
- injuries
- personnel changes
- starting roles
- coaching roles
- statistics
- rankings
- previous results
- quotes
- tactical information

If evidence is weak, omit it.

Do not present media-guide promotional language as objective truth.
Team-issued guides are source material, not independent analysis.

BETTING DISCIPLINE

The deterministic program supplies the final wager designation.

You must not:
- upgrade a pass to a bet
- downgrade a bet to a pass
- create your own betting recommendation

Do not introduce any:
- spread
- price
- probability
- edge
- model projection

that was not provided in the structured model context.
"""


EXTRACTION_SYSTEM_PROMPT = """
You extract factual football information from team-issued game notes and
media guides for BTB Analytics.

Your job is NOT to summarize the entire document.

Your goal is to extract only information useful for explaining the upcoming
football matchup.

PRIORITIZE

1. Upcoming opponent-specific notes.
2. Most recent game performance.
3. Injuries, returns, or availability information explicitly stated.
4. Quarterback information.
5. Offensive line information.
6. Offensive personnel changes.
7. Defensive personnel changes.
8. New coaches, coordinators, or play callers.
9. Transfers and first-time starters.
10. Recent offensive tendencies.
11. Recent defensive tendencies.
12. Individual players plausibly relevant to the matchup.
13. Special-teams developments if meaningful.
14. Series history only when unusually relevant.

DE-PRIORITIZE OR IGNORE

- broadcast crews
- radio information
- ticket information
- media contacts
- generic program history
- alumni in the NFL
- award-watch lists
- unrelated recruiting history
- birthdays
- generic coaching biographies
- trivial player connections
- high-school connections
- promotional slogans

IMPORTANT

A team-issued media guide is promotional material.

Extract statements as facts only when they are concrete, such as:
- statistics
- dates
- personnel
- results
- coaching assignments
- player roles
- clearly described roster changes

Do not convert subjective promotional claims into factual conclusions.

Return valid JSON only.
"""


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #

def _client() -> OpenAI:
    """
    Create the OpenAI client using OPENAI_API_KEY.
    """

    key = os.getenv("OPENAI_API_KEY")

    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. "
            "Add it as a GitHub Actions repository secret "
            "or local environment variable."
        )

    return OpenAI(api_key=key)


def _response_text(response: Any) -> str:
    """
    Safely retrieve text from an OpenAI Responses API response.
    """

    text = getattr(
        response,
        "output_text",
        None,
    )

    if text:
        return text.strip()

    # Defensive fallback in case the SDK response structure differs.
    try:
        pieces: List[str] = []

        for item in response.output:
            for content in item.content:
                value = getattr(
                    content,
                    "text",
                    None,
                )

                if value:
                    pieces.append(value)

        return "\n".join(
            pieces
        ).strip()

    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# Crosswalk / filename helpers
# --------------------------------------------------------------------------- #

def clean_filename_component(
    value: str,
) -> str:
    """
    Keep BTB short names readable while removing characters that cannot
    safely appear in filenames.

    Spaces are intentionally preserved.

    Example:
        "Ohio State" -> "Ohio State"
        "Miami (FL)" -> "Miami (FL)"
    """

    value = str(value).strip()

    value = re.sub(
        r'[<>:"/\\\\|?*]',
        "",
        value,
    )

    return value.strip()


def team_short_lookup(
    crosswalk: pd.DataFrame,
) -> Dict[int, str]:
    """
    Return:
        team_id -> btb_team_short
    """

    required = {
        "team_id",
        "btb_team_short",
    }

    missing = required.difference(
        crosswalk.columns
    )

    if missing:
        raise ValueError(
            "Crosswalk is missing required column(s): "
            + ", ".join(sorted(missing))
        )

    cw = crosswalk.dropna(
        subset=[
            "team_id",
            "btb_team_short",
        ]
    ).copy()

    cw["team_id"] = pd.to_numeric(
        cw["team_id"],
        errors="coerce",
    )

    cw = cw.dropna(
        subset=["team_id"]
    )

    cw["team_id"] = (
        cw["team_id"]
        .astype(int)
    )

    return (
        cw.set_index(
            "team_id"
        )["btb_team_short"]
        .astype(str)
        .to_dict()
    )


def team_full_lookup(
    crosswalk: pd.DataFrame,
) -> Dict[int, str]:
    """
    Return:
        team_id -> btb_team
    """

    required = {
        "team_id",
        "btb_team",
    }

    missing = required.difference(
        crosswalk.columns
    )

    if missing:
        raise ValueError(
            "Crosswalk is missing required column(s): "
            + ", ".join(sorted(missing))
        )

    cw = crosswalk.dropna(
        subset=[
            "team_id",
            "btb_team",
        ]
    ).copy()

    cw["team_id"] = pd.to_numeric(
        cw["team_id"],
        errors="coerce",
    )

    cw = cw.dropna(
        subset=["team_id"]
    )

    cw["team_id"] = (
        cw["team_id"]
        .astype(int)
    )

    return (
        cw.set_index(
            "team_id"
        )["btb_team"]
        .astype(str)
        .to_dict()
    )


def expected_guide_filename(
    team_id: int,
    crosswalk: pd.DataFrame,
) -> Optional[str]:
    """
    Return the expected PDF filename for a team.

    Example:
        btb_team_short = Syracuse

    returns:
        Syracuse.pdf
    """

    short_names = team_short_lookup(
        crosswalk
    )

    short = short_names.get(
        int(team_id)
    )

    if not short:
        return None

    clean_short = (
        clean_filename_component(
            short
        )
    )

    return f"{clean_short}.pdf"


def guide_path_for_team(
    team_id: int,
    season: int,
    crosswalk: pd.DataFrame,
    guide_root: Path,
) -> Optional[Path]:
    """
    Find a team's PDF game guide.

    Naming convention:

        game_guides/{season}/{btb_team_short}.pdf

    Example:

        btb_team_short = Syracuse

        game_guides/2026/Syracuse.pdf

    The normal lookup is exact.

    As a safety fallback, filename matching is also case-insensitive, so:

        Syracuse.pdf
        syracuse.pdf
        SYRACUSE.PDF

    all resolve to Syracuse.

    The .pdf extension is automatically appended by this function.
    It should NOT be stored in btb_team_short.
    """

    filename = expected_guide_filename(
        team_id,
        crosswalk,
    )

    if not filename:
        return None

    season_dir = (
        guide_root
        / str(season)
    )

    if not season_dir.exists():
        return None

    if not season_dir.is_dir():
        return None

    # ---------------------------------------------------------
    # 1. Expected exact filename
    # ---------------------------------------------------------

    exact_path = (
        season_dir
        / filename
    )

    if (
        exact_path.exists()
        and exact_path.is_file()
    ):
        return exact_path

    # ---------------------------------------------------------
    # 2. Case-insensitive filename fallback
    # ---------------------------------------------------------

    expected_lower = (
        filename.lower()
    )

    for path in season_dir.iterdir():

        if not path.is_file():
            continue

        if (
            path.name.lower()
            == expected_lower
        ):
            return path

    return None


# --------------------------------------------------------------------------- #
# PDF extraction
# --------------------------------------------------------------------------- #

def extract_pdf_text(
    path: Path,
) -> str:
    """
    Extract embedded text from an athletic media-guide PDF.

    Page markers are inserted into the extracted text so the model can
    preserve page-level source provenance.

    OCR is intentionally not used here.
    """

    reader = PdfReader(
        str(path)
    )

    chunks: List[str] = []

    for page_number, page in enumerate(
        reader.pages,
        start=1,
    ):

        text = (
            page.extract_text()
            or ""
        ).strip()

        if not text:
            continue

        chunks.append(
            "\n\n"
            f"===== SOURCE PDF PAGE {page_number} ====="
            "\n\n"
            f"{text}"
        )

    output = "".join(
        chunks
    ).strip()

    # Guardrail against exceptionally large documents.
    if (
        len(output)
        > MAX_GUIDE_CHARS
    ):
        output = output[
            :MAX_GUIDE_CHARS
        ]

    return output


# --------------------------------------------------------------------------- #
# Guide fact extraction
# --------------------------------------------------------------------------- #

def extract_guide_facts(
    *,
    pdf_path: Path,
    source_team_name: str,
    opponent_name: str,
    season: int,
    week: int,
    model: str = DEFAULT_OPENAI_MODEL,
) -> Dict[str, Any]:
    """
    Convert one media guide into structured factual information.

    This step extracts facts only.
    It does not write the article.
    """

    text = extract_pdf_text(
        pdf_path
    )

    if not text:
        return {
            "source_file": pdf_path.name,
            "source_team": source_team_name,
            "opponent": opponent_name,
            "usable": False,
            "reason": (
                "PDF contained no extractable text."
            ),
            "document_matches_upcoming_game": False,
            "facts": [],
        }

    prompt = f"""
SOURCE TEAM:
{source_team_name}

UPCOMING OPPONENT:
{opponent_name}

SEASON:
{season}

WEEK:
{week}

Your job is to extract only matchup-relevant facts from the SOURCE TEAM'S
media guide.

DOCUMENT VALIDATION

The document may be:
- current for this game
- current for the team but written for another opponent
- stale

Determine whether the document appears intended for an upcoming matchup
between:

{source_team_name}

and

{opponent_name}

Return this exact JSON structure:

{{
  "source_team": "{source_team_name}",
  "opponent": "{opponent_name}",
  "document_matches_upcoming_game": true,
  "document_match_reason": "...",

  "recent_game": [
    {{
      "fact": "...",
      "page": 1,
      "category": "recent_game",
      "importance": "high"
    }}
  ],

  "opponent_notes": [],
  "quarterback": [],
  "offensive_personnel": [],
  "defensive_personnel": [],
  "coaching_changes": [],
  "injury_availability": [],
  "offensive_context": [],
  "defensive_context": [],
  "special_teams": [],
  "series_context": [],
  "other_relevant": []
}}

EVERY FACT OBJECT MUST CONTAIN

- fact:
  One concise factual statement.

- page:
  The SOURCE PDF PAGE number when identifiable.

- category:
  One of the categories above.

- importance:
  high, medium, or low.

RULES

Do not duplicate facts.

If the document clearly describes a different upcoming opponent:

- set document_matches_upcoming_game=false
- explain why in document_match_reason
- do NOT return opponent-specific information about the wrong opponent

You may still extract clearly current information about {source_team_name},
such as:
- its most recent game
- current quarterback
- current personnel
- current injuries
- current coaching changes

Do not infer an injury unless the document explicitly describes one.

Do not create tactical conclusions.

Do not rewrite promotional claims as objective facts.

SOURCE DOCUMENT:

{text}
"""

    response = (
        _client()
        .responses
        .create(
            model=model,
            instructions=(
                EXTRACTION_SYSTEM_PROMPT
            ),
            input=prompt,
                    )
    )

    raw = _response_text(
        response
    )

    try:
        parsed = json.loads(
            raw
        )

    except json.JSONDecodeError:

        # Defensive fallback if the model wraps JSON in prose/code fences.
        match = re.search(
            r"\{.*\}",
            raw,
            flags=re.S,
        )

        if not match:
            raise RuntimeError(
                "Could not parse guide extraction JSON "
                f"for {pdf_path}"
            )

        parsed = json.loads(
            match.group(0)
        )

    parsed[
        "source_file"
    ] = pdf_path.name

    parsed[
        "source_team"
    ] = source_team_name

    parsed[
        "opponent"
    ] = opponent_name

    return parsed


# --------------------------------------------------------------------------- #
# Flatten facts for narrative generation
# --------------------------------------------------------------------------- #

def flatten_verified_facts(
    guide_results: List[
        Dict[str, Any]
    ],
) -> List[
    Dict[str, Any]
]:
    """
    Flatten extracted guide categories while retaining:
    - source file
    - source team
    - source page
    - category
    - importance
    """

    output: List[
        Dict[str, Any]
    ] = []

    metadata_keys = {
        "source_file",
        "source_team",
        "opponent",
        "document_matches_upcoming_game",
        "document_match_reason",
        "usable",
        "reason",
    }

    for guide in guide_results:

        source_file = guide.get(
            "source_file"
        )

        source_team = guide.get(
            "source_team"
        )

        document_matches = guide.get(
            "document_matches_upcoming_game",
            True,
        )

        for key, value in guide.items():

            if key in metadata_keys:
                continue

            if not isinstance(
                value,
                list,
            ):
                continue

            for fact in value:

                if not isinstance(
                    fact,
                    dict,
                ):
                    continue

                text = str(
                    fact.get(
                        "fact",
                        "",
                    )
                ).strip()

                if not text:
                    continue

                category = fact.get(
                    "category",
                    key,
                )

                # If a guide is stale, don't let opponent-specific facts
                # leak into the current matchup narrative.
                if (
                    not document_matches
                    and category
                    in {
                        "opponent_notes",
                        "series_context",
                    }
                ):
                    continue

                output.append(
                    {
                        "fact": text,
                        "page": fact.get(
                            "page"
                        ),
                        "category": category,
                        "importance": (
                            fact.get(
                                "importance",
                                "medium",
                            )
                        ),
                        "source_file": (
                            source_file
                        ),
                        "source_team": (
                            source_team
                        ),
                    }
                )

    return output


# --------------------------------------------------------------------------- #
# Matchup-angle detection
# --------------------------------------------------------------------------- #

def _rank_value(
    ranked_stats: pd.DataFrame,
    team_id: int,
    column: str,
) -> Optional[int]:
    """
    Safely retrieve one FBS rank for one team.
    """

    if (
        team_id is None
        or column not in ranked_stats.columns
    ):
        return None

    team_ids = pd.to_numeric(
        ranked_stats["team_id"],
        errors="coerce",
    )

    row = ranked_stats[
        team_ids == int(team_id)
    ]

    if row.empty:
        return None

    value = row.iloc[0].get(
        column
    )

    if (
        value is None
        or pd.isna(value)
    ):
        return None

    return int(value)


def identify_matchup_angles(
    bet_id: int,
    opp_id: int,
    ranked_stats: pd.DataFrame,
    crosswalk: pd.DataFrame,
) -> List[
    Dict[str, Any]
]:
    """
    Identify the most interesting directional matchup relationships from
    BTB's rolling rankings.

    These are explanatory signals only.

    They do NOT independently determine whether there is a bet.
    """

    names = team_full_lookup(
        crosswalk
    )

    bet_name = names.get(
        int(bet_id),
        str(bet_id),
    )

    opp_name = names.get(
        int(opp_id),
        str(opp_id),
    )

    candidates = [
        {
            "dimension": (
                f"{bet_name} passing offense "
                f"vs {opp_name} pass defense"
            ),
            "off_team": bet_name,
            "def_team": opp_name,
            "off_rank": _rank_value(
                ranked_stats,
                bet_id,
                "off_pass_epa_rank",
            ),
            "def_rank": _rank_value(
                ranked_stats,
                opp_id,
                "def_pass_epa_rank",
            ),
            "stat": "pass EPA",
        },

        {
            "dimension": (
                f"{bet_name} rushing offense "
                f"vs {opp_name} rush defense"
            ),
            "off_team": bet_name,
            "def_team": opp_name,
            "off_rank": _rank_value(
                ranked_stats,
                bet_id,
                "off_rush_epa_rank",
            ),
            "def_rank": _rank_value(
                ranked_stats,
                opp_id,
                "def_rush_epa_rank",
            ),
            "stat": "rush EPA",
        },

        {
            "dimension": (
                f"{opp_name} passing offense "
                f"vs {bet_name} pass defense"
            ),
            "off_team": opp_name,
            "def_team": bet_name,
            "off_rank": _rank_value(
                ranked_stats,
                opp_id,
                "off_pass_epa_rank",
            ),
            "def_rank": _rank_value(
                ranked_stats,
                bet_id,
                "def_pass_epa_rank",
            ),
            "stat": "pass EPA",
        },

        {
            "dimension": (
                f"{opp_name} rushing offense "
                f"vs {bet_name} rush defense"
            ),
            "off_team": opp_name,
            "def_team": bet_name,
            "off_rank": _rank_value(
                ranked_stats,
                opp_id,
                "off_rush_epa_rank",
            ),
            "def_rank": _rank_value(
                ranked_stats,
                bet_id,
                "def_rush_epa_rank",
            ),
            "stat": "rush EPA",
        },

        {
            "dimension": (
                f"{bet_name} scoring-opportunity creation "
                f"vs {opp_name} scoring-opportunity prevention"
            ),
            "off_team": bet_name,
            "def_team": opp_name,
            "off_rank": _rank_value(
                ranked_stats,
                bet_id,
                "off_eckel_rank",
            ),
            "def_rank": _rank_value(
                ranked_stats,
                opp_id,
                "def_eckel_rank",
            ),
            "stat": "Eckel rate",
        },

        {
            "dimension": (
                f"{opp_name} scoring-opportunity creation "
                f"vs {bet_name} scoring-opportunity prevention"
            ),
            "off_team": opp_name,
            "def_team": bet_name,
            "off_rank": _rank_value(
                ranked_stats,
                opp_id,
                "off_eckel_rank",
            ),
            "def_rank": _rank_value(
                ranked_stats,
                bet_id,
                "def_eckel_rank",
            ),
            "stat": "Eckel rate",
        },
    ]

    usable: List[
        Dict[str, Any]
    ] = []

    for item in candidates:

        off_rank = item[
            "off_rank"
        ]

        def_rank = item[
            "def_rank"
        ]

        if (
            off_rank is None
            or def_rank is None
        ):
            continue

        differential = abs(
            def_rank
            - off_rank
        )

        # Strong offense against weak defense.
        if (
            off_rank <= 35
            and def_rank >= 75
        ):
            score = (
                100
                + differential
            )

            direction = (
                "offense_advantage"
            )

        # Strong defense against weak offense.
        elif (
            def_rank <= 35
            and off_rank >= 75
        ):
            score = (
                100
                + differential
            )

            direction = (
                "defense_advantage"
            )

        else:
            score = differential
            direction = "mixed"

        item[
            "rank_gap"
        ] = differential

        item[
            "direction"
        ] = direction

        item[
            "matchup_score"
        ] = score

        usable.append(
            item
        )

    usable.sort(
        key=lambda x: x[
            "matchup_score"
        ],
        reverse=True,
    )

    return usable[:3]


# --------------------------------------------------------------------------- #
# Model context
# --------------------------------------------------------------------------- #

def build_model_context(
    *,
    bet_name: str,
    opponent_name: str,
    market_line: float,
    best_line: float,
    best_price: Any,
    cover_probability: Any,
    edge: Any,
    has_bet: bool,
) -> Dict[str, Any]:
    """
    Package deterministic BTB model information for narrative writing.
    """

    return {
        "model_side": (
            bet_name
        ),
        "opponent": (
            opponent_name
        ),
        "market_line": (
            market_line
        ),
        "best_available_line": (
            best_line
        ),
        "best_available_price": (
            best_price
        ),
        "cover_probability": (
            cover_probability
        ),
        "edge": edge,
        "bet_status": (
            "BET"
            if has_bet
            else "PASS"
        ),
    }


# --------------------------------------------------------------------------- #
# Narrative generation
# --------------------------------------------------------------------------- #

def generate_matchup_narrative(
    *,
    away_name: str,
    home_name: str,
    model_context: Dict[str, Any],
    matchup_angles: List[
        Dict[str, Any]
    ],
    guide_results: List[
        Dict[str, Any]
    ],
    model: str = DEFAULT_OPENAI_MODEL,
) -> Dict[str, Any]:
    """
    Generate the narrative portions of the article.

    Returns:
        {
            "used_guides": bool,
            "narrative": "...",
            "matchup_to_watch": "...",
            "used_fact_ids": [...],
            "used_sources": [...]
        }
    """

    verified_facts = (
        flatten_verified_facts(
            guide_results
        )
    )

    if not verified_facts:
        return {
            "used_guides": False,
            "narrative": "",
            "matchup_to_watch": "",
            "used_fact_ids": [],
            "used_sources": [],
        }

    numbered_facts = []

    for idx, fact in enumerate(
        verified_facts,
        start=1,
    ):
        numbered_facts.append(
            {
                "fact_id": idx,
                **fact,
            }
        )

    prompt = f"""
GAME:
{away_name} at {home_name}

DETERMINISTIC BTB MODEL CONTEXT:
{json.dumps(model_context, indent=2, default=str)}

BTB MATCHUP ANGLES:
{json.dumps(matchup_angles, indent=2, default=str)}

VERIFIED MEDIA-GUIDE FACTS:
{json.dumps(numbered_facts, indent=2, default=str)}

Write content for two portions of a BTB Analytics matchup article.

Return valid JSON only using this structure:

{{
  "narrative": "2-3 concise paragraphs",
  "matchup_to_watch": "1-2 concise paragraphs",
  "used_fact_ids": [1, 4, 7]
}}

NARRATIVE REQUIREMENTS

The narrative should:

- explain what is actually interesting about this game
- sound like BTB Analytics rather than a generic preview
- use recent results as context rather than proof
- focus on two or three meaningful ideas
- avoid simply reciting rankings
- connect football information to why the model/market disagreement matters
- acknowledge uncertainty when appropriate
- remain accessible to a recreational bettor
- avoid turning into an analytics lecture

Where appropriate, distinguish between:
- the headline result
- the more useful underlying takeaway

MATCHUP TO WATCH REQUIREMENTS

This should connect one or two BTB statistical matchup angles to relevant
guide context.

Explain why the matchup is worth paying attention to without claiming it
guarantees the model side will cover.

Do not repeat the Bottom Line verbatim.

If the deterministic status is PASS:
- do not write as though BTB is betting it
- explain why the matchup may be interesting while respecting the pass

FACT REQUIREMENTS

Every media-guide-derived factual claim must come from VERIFIED
MEDIA-GUIDE FACTS.

Return the fact IDs actually used.

Do not manufacture:
- injuries
- personnel
- statistics
- coaching changes
- previous results
- tactical information

Do not add external football information.
"""

    response = (
        _client()
        .responses
        .create(
            model=model,
            instructions=(
                BTB_SYSTEM_PROMPT
            ),
            input=prompt,
                    )
    )

    raw = _response_text(
        response
    )

    try:
        parsed = json.loads(
            raw
        )

    except json.JSONDecodeError:

        match = re.search(
            r"\{.*\}",
            raw,
            flags=re.S,
        )

        if not match:
            raise RuntimeError(
                "Could not parse narrative JSON."
            )

        parsed = json.loads(
            match.group(0)
        )

    parsed[
        "used_guides"
    ] = True

    # ---------------------------------------------------------
    # Convert fact IDs back into source metadata
    # ---------------------------------------------------------

    fact_by_id = {
        x["fact_id"]: x
        for x in numbered_facts
    }

    used_sources: List[
        Dict[str, Any]
    ] = []

    for fact_id in parsed.get(
        "used_fact_ids",
        [],
    ):

        try:
            fact_id = int(
                fact_id
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        source = fact_by_id.get(
            fact_id
        )

        if source:
            used_sources.append(
                source
            )

    parsed[
        "used_sources"
    ] = used_sources

    return parsed


# --------------------------------------------------------------------------- #
# Load guides for one game
# --------------------------------------------------------------------------- #

def load_game_guides(
    *,
    away_id: int,
    home_id: int,
    away_name: str,
    home_name: str,
    season: int,
    week: int,
    crosswalk: pd.DataFrame,
    guide_root: Path,
    model: str = DEFAULT_OPENAI_MODEL,
) -> Tuple[
    List[Dict[str, Any]],
    List[str],
]:
    """
    Find and process the media guides available for the game.

    Missing guides are NOT errors.

    One guide:
        use it.

    Both guides:
        use both.

    No guides:
        article generator falls back to deterministic BTB content.
    """

    results: List[
        Dict[str, Any]
    ] = []

    status: List[str] = []

    pairs = [
        (
            away_id,
            away_name,
            home_name,
        ),
        (
            home_id,
            home_name,
            away_name,
        ),
    ]

    for (
        team_id,
        team_name,
        opponent_name,
    ) in pairs:

        # -----------------------------------------------------
        # Validate team ID
        # -----------------------------------------------------

        if team_id is None:

            status.append(
                f"No team_id available for "
                f"{team_name}; cannot locate guide."
            )

            continue

        # -----------------------------------------------------
        # Determine expected filename
        # -----------------------------------------------------

        expected_filename = (
            expected_guide_filename(
                team_id,
                crosswalk,
            )
        )

        if not expected_filename:

            status.append(
                f"No btb_team_short mapping found "
                f"for {team_name} "
                f"(team_id={team_id})."
            )

            continue

        expected_path = (
            guide_root
            / str(season)
            / expected_filename
        )

        # -----------------------------------------------------
        # Locate actual file
        # -----------------------------------------------------

        path = guide_path_for_team(
            team_id=team_id,
            season=season,
            crosswalk=crosswalk,
            guide_root=guide_root,
        )

        if path is None:

            status.append(
                f"No guide found for {team_name}. "
                f"Expected PDF: {expected_path}"
            )

            continue

        # -----------------------------------------------------
        # Log what actually matched
        # -----------------------------------------------------

        status.append(
            f"Found guide for {team_name}: "
            f"{path}"
        )

        # -----------------------------------------------------
        # Extract guide information
        # -----------------------------------------------------

        try:

            extracted = (
                extract_guide_facts(
                    pdf_path=path,
                    source_team_name=(
                        team_name
                    ),
                    opponent_name=(
                        opponent_name
                    ),
                    season=season,
                    week=week,
                    model=model,
                )
            )

            results.append(
                extracted
            )

            match_flag = (
                extracted.get(
                    "document_matches_upcoming_game",
                    True,
                )
            )

            if match_flag:

                status.append(
                    f"Loaded guide for "
                    f"{team_name}: "
                    f"{path.name}"
                )

            else:

                reason = (
                    extracted.get(
                        "document_match_reason",
                        "",
                    )
                )

                status.append(
                    f"Guide for {team_name} "
                    f"was found but may not match "
                    f"the current opponent. "
                    f"{reason}"
                )

        except Exception as exc:

            status.append(
                f"Guide processing failed "
                f"for {team_name}: {exc}"
            )

    return (
        results,
        status,
    )
