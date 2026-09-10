#!/usr/bin/env python3
"""
CFB game-guide ingestion + BTB narrative generation.

Responsibilities:
1. Resolve media-guide PDFs using crosswalk.btb_team_short.
2. Expected guide naming:
      game_guides/{season}/{btb_team_short}.pdf
3. Extract text from PDFs.
4. Convert guide content into structured verified facts.
5. Identify meaningful statistical matchup angles.
6. Generate BTB-style narrative using:
      - verified guide facts
      - deterministic BTB statistics
      - actual BTB model prediction
      - market line
      - cover probability
      - edge
7. Preserve source metadata for auditing.

The language model NEVER determines bet/pass.
"""

from __future__ import annotations

import json
import os
import re

from io import BytesIO
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

MAX_GUIDE_CHARS = 110_000


# --------------------------------------------------------------------------- #
# BTB editorial instructions
# --------------------------------------------------------------------------- #

BTB_SYSTEM_PROMPT = """
You are writing directly as BTB Analytics.

VOICE

Always write in first-person plural.

Use:
- we
- our
- our model
- our numbers
- our projection
- we make
- we project
- we see
- we think
- for us

Do NOT refer to BTB Analytics or BTB in the third person inside article prose.

BAD:
"BTB projects Syracuse -9.5."
"BTB sees an edge."
"BTB thinks Syracuse can separate."

GOOD:
"We make Syracuse -9.5."
"We see an edge."
"We think Syracuse can create separation."

TEAM NAMES

The structured context supplies conversational short team names.

Use those short names naturally in prose.

Example:

GOOD:
"We make Syracuse -9.5."
"The biggest concern for us is California's pass defense."

BAD:
"We make the Syracuse Orange -9.5."
"The biggest concern for us is the California Golden Bears pass defense."

Full mascot names may appear in tables and titles, but normal article prose
should use the short conversational names supplied in MODEL CONTEXT.

CORE BTB PHILOSOPHY

The most important question is not:

"Which team is better?"

It is:

"Why does our expectation differ from the market, and is that disagreement
meaningful at the available price?"

Carefully distinguish between:

1. What happened.
2. What may matter going forward.
3. What our longer-run numbers indicate.
4. What the market is currently pricing.
5. Why our expectation differs from that market price.

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

STYLE

The writing should sound:
- sharp
- skeptical
- informed
- conversational
- readable
- confident without overstating certainty

It should NOT sound:
- academic
- robotic
- generic
- promotional
- like a tout

Prefer language such as:
- "The more interesting part is..."
- "That matters because..."
- "The market is asking..."
- "The question is whether..."
- "What stands out to us is..."
- "That does not automatically mean..."
- "We are not reacting to..."
- "The useful takeaway is..."
- "Where this gets interesting..."
- "The price matters because..."
- "That is where we differ from the market..."
- "For us, the case is less about X and more about Y..."

Avoid generic sports-preview filler:
- "enters this game with momentum"
- "will look to build on"
- "should be an exciting matchup"
- "both teams will be looking to"
- "keys to victory"
- "set the tone"
- "must establish the run"
- "needs to execute"

ANALYTICS STYLE

Analytics should explain the argument rather than become the argument.

Do not repeat every statistic supplied.

Prioritize two or three matchup ideas that help explain the model/market
difference.

The reader should finish the article understanding:

1. What we project.
2. What the market is offering.
3. Where the disagreement comes from.
4. What football information supports or challenges that disagreement.
5. Why the available price is or is not enough for a bet.

Avoid excessive hedging.

It is good to acknowledge uncertainty, but do not repeatedly talk the reader
out of a deterministic bet.

Use uncertainty once when useful, then make the analytical conclusion clear.

REPETITION RULE

The deterministic Our Take opening already provides:
- our projected spread
- the market line
- the best available line/price
- cover probability
- edge
- bet/pass status

DO NOT repeat those same exact numbers in the generated narrative.

The narrative's job is football context.

SOURCE DISCIPLINE

You may ONLY state guide-derived facts appearing in VERIFIED MEDIA-GUIDE
FACTS.

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

Do not present team-issued promotional language as objective truth.

BETTING DISCIPLINE

The deterministic program supplies the wager status.

You must not:
- upgrade a pass to a bet
- downgrade a bet to a pass
- create another wager
- recommend the opposing side

Do not introduce spreads, prices, probabilities, edges, or projections that
were not supplied in MODEL CONTEXT.
"""


EXTRACTION_SYSTEM_PROMPT = """
You extract factual football information from team-issued game notes and
media guides for BTB Analytics.

Do NOT summarize the entire document.

Extract only information useful for explaining the upcoming matchup.

PRIORITIZE

1. Upcoming opponent-specific notes.
2. Most recent game performance.
3. Injuries, returns, or availability explicitly stated.
4. Quarterback information.
5. Offensive line information.
6. Offensive personnel changes.
7. Defensive personnel changes.
8. New coaches, coordinators, or play callers.
9. Transfers and first-time starters.
10. Recent offensive tendencies.
11. Recent defensive tendencies.
12. Individual players relevant to the matchup.
13. Meaningful special-teams developments.
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

A team-issued media guide is promotional material.

Extract statements as facts only when they are concrete:
- statistics
- dates
- personnel
- results
- coaching assignments
- roster changes
- clearly stated injuries/returns

Do not convert subjective claims into objective conclusions.

Return valid JSON only.
"""


# --------------------------------------------------------------------------- #
# OpenAI helpers
# --------------------------------------------------------------------------- #

def _client() -> OpenAI:

    key = os.getenv(
        "OPENAI_API_KEY"
    )

    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set."
        )

    return OpenAI(
        api_key=key
    )


def _response_text(
    response: Any,
) -> str:

    text = getattr(
        response,
        "output_text",
        None,
    )

    if text:
        return text.strip()

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
                    pieces.append(
                        value
                    )

        return "\n".join(
            pieces
        ).strip()

    except Exception:

        return ""


# --------------------------------------------------------------------------- #
# Crosswalk helpers
# --------------------------------------------------------------------------- #

def clean_filename_component(
    value: str,
) -> str:

    value = str(
        value
    ).strip()

    value = re.sub(
        r'[<>:"/\\\\|?*]',
        "",
        value,
    )

    return value.strip()


def team_short_lookup(
    crosswalk: pd.DataFrame,
) -> Dict[int, str]:

    cw = (
        crosswalk
        .dropna(
            subset=[
                "team_id",
                "btb_team_short",
            ]
        )
        .copy()
    )

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
        cw
        .drop_duplicates(
            subset=["team_id"]
        )
        .set_index(
            "team_id"
        )["btb_team_short"]
        .astype(str)
        .to_dict()
    )


def team_full_lookup(
    crosswalk: pd.DataFrame,
) -> Dict[int, str]:

    cw = (
        crosswalk
        .dropna(
            subset=[
                "team_id",
                "btb_team",
            ]
        )
        .copy()
    )

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
        cw
        .drop_duplicates(
            subset=["team_id"]
        )
        .set_index(
            "team_id"
        )["btb_team"]
        .astype(str)
        .to_dict()
    )


def expected_guide_filename(
    team_id: int,
    crosswalk: pd.DataFrame,
) -> Optional[str]:

    short_names = (
        team_short_lookup(
            crosswalk
        )
    )

    short = short_names.get(
        int(team_id)
    )

    if not short:
        return None

    return (
        f"{clean_filename_component(short)}.pdf"
    )


def guide_path_for_team(
    team_id: int,
    season: int,
    crosswalk: pd.DataFrame,
    guide_root: Path,
) -> Optional[Path]:

    filename = (
        expected_guide_filename(
            team_id,
            crosswalk,
        )
    )

    if not filename:
        return None

    season_dir = (
        guide_root
        / str(season)
    )

    if (
        not season_dir.exists()
        or not season_dir.is_dir()
    ):
        return None

    exact_path = (
        season_dir
        / filename
    )

    if (
        exact_path.exists()
        and exact_path.is_file()
    ):
        return exact_path

    expected_lower = (
        filename.lower()
    )

    for path in season_dir.iterdir():

        if (
            path.is_file()
            and path.name.lower()
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

    raw = path.read_bytes()

    pdf_start = raw.find(
        b"%PDF"
    )

    if pdf_start == -1:

        raise RuntimeError(
            f"{path.name} does not contain a valid %PDF header."
        )

    if pdf_start > 0:

        print(
            f"Normalizing PDF header for {path.name}: "
            f"removed {pdf_start} leading byte(s)."
        )

        raw = raw[
            pdf_start:
        ]

    reader = PdfReader(
        BytesIO(raw),
        strict=False,
    )

    chunks: List[str] = []

    for (
        page_number,
        page,
    ) in enumerate(
        reader.pages,
        start=1,
    ):

        try:

            text = (
                page.extract_text()
                or ""
            ).strip()

        except Exception as exc:

            print(
                f"Warning: could not extract "
                f"page {page_number} from "
                f"{path.name}: {exc}"
            )

            continue

        if not text:
            continue

        chunks.append(
            "\n\n"
            f"===== SOURCE PDF PAGE "
            f"{page_number} ====="
            "\n\n"
            f"{text}"
        )

    output = "".join(
        chunks
    ).strip()

    if not output:

        raise RuntimeError(
            f"No extractable text found in {path.name}."
        )

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

    text = extract_pdf_text(
        pdf_path
    )

    prompt = f"""
SOURCE TEAM:
{source_team_name}

UPCOMING OPPONENT:
{opponent_name}

SEASON:
{season}

WEEK:
{week}

Extract only matchup-relevant facts.

Determine whether the guide is intended for the upcoming matchup between
{source_team_name} and {opponent_name}.

Return valid JSON exactly in this structure:

{{
  "source_team": "{source_team_name}",
  "opponent": "{opponent_name}",
  "document_matches_upcoming_game": true,
  "document_match_reason": "...",

  "recent_game": [],
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

Every fact must be:

{{
  "fact": "...",
  "page": 1,
  "category": "...",
  "importance": "high"
}}

importance must be:
- high
- medium
- low

If this guide is clearly for a different opponent:

- document_matches_upcoming_game = false
- explain why
- do not extract opponent-specific information about the wrong opponent

You may still extract clearly current information about {source_team_name}.

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

        match = re.search(
            r"\{.*\}",
            raw,
            flags=re.S,
        )

        if not match:

            raise RuntimeError(
                "Could not parse guide "
                f"extraction JSON for "
                f"{pdf_path}"
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
# Flatten verified guide facts
# --------------------------------------------------------------------------- #

def flatten_verified_facts(
    guide_results: List[
        Dict[str, Any]
    ],
) -> List[
    Dict[str, Any]
]:

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

        source_file = (
            guide.get(
                "source_file"
            )
        )

        source_team = (
            guide.get(
                "source_team"
            )
        )

        document_matches = (
            guide.get(
                "document_matches_upcoming_game",
                True,
            )
        )

        for (
            key,
            value,
        ) in guide.items():

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

                category = (
                    fact.get(
                        "category",
                        key,
                    )
                )

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
                        "fact":
                            text,

                        "page":
                            fact.get(
                                "page"
                            ),

                        "category":
                            category,

                        "importance":
                            fact.get(
                                "importance",
                                "medium",
                            ),

                        "source_file":
                            source_file,

                        "source_team":
                            source_team,
                    }
                )

    return output


# --------------------------------------------------------------------------- #
# Matchup stats
# --------------------------------------------------------------------------- #

def _rank_value(
    ranked_stats: pd.DataFrame,
    team_id: int,
    column: str,
) -> Optional[int]:

    if (
        team_id is None
        or column
        not in ranked_stats.columns
    ):
        return None

    ids = pd.to_numeric(
        ranked_stats[
            "team_id"
        ],
        errors="coerce",
    )

    row = ranked_stats[
        ids
        == int(team_id)
    ]

    if row.empty:
        return None

    value = (
        row.iloc[0]
        .get(
            column
        )
    )

    if (
        value is None
        or pd.isna(value)
    ):
        return None

    return int(
        value
    )


def identify_matchup_angles(
    bet_id: int,
    opp_id: int,
    ranked_stats: pd.DataFrame,
    crosswalk: pd.DataFrame,
) -> List[
    Dict[str, Any]
]:

    names = team_full_lookup(
        crosswalk
    )

    short_names = team_short_lookup(
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

    bet_short = short_names.get(
        int(bet_id),
        bet_name,
    )

    opp_short = short_names.get(
        int(opp_id),
        opp_name,
    )

    candidates = [
        {
            "dimension":
                f"{bet_short} passing offense "
                f"vs {opp_short} pass defense",

            "off_team":
                bet_short,

            "def_team":
                opp_short,

            "off_rank":
                _rank_value(
                    ranked_stats,
                    bet_id,
                    "off_pass_epa_rank",
                ),

            "def_rank":
                _rank_value(
                    ranked_stats,
                    opp_id,
                    "def_pass_epa_rank",
                ),

            "stat":
                "pass EPA",
        },

        {
            "dimension":
                f"{bet_short} rushing offense "
                f"vs {opp_short} rush defense",

            "off_team":
                bet_short,

            "def_team":
                opp_short,

            "off_rank":
                _rank_value(
                    ranked_stats,
                    bet_id,
                    "off_rush_epa_rank",
                ),

            "def_rank":
                _rank_value(
                    ranked_stats,
                    opp_id,
                    "def_rush_epa_rank",
                ),

            "stat":
                "rush EPA",
        },

        {
            "dimension":
                f"{opp_short} passing offense "
                f"vs {bet_short} pass defense",

            "off_team":
                opp_short,

            "def_team":
                bet_short,

            "off_rank":
                _rank_value(
                    ranked_stats,
                    opp_id,
                    "off_pass_epa_rank",
                ),

            "def_rank":
                _rank_value(
                    ranked_stats,
                    bet_id,
                    "def_pass_epa_rank",
                ),

            "stat":
                "pass EPA",
        },

        {
            "dimension":
                f"{opp_short} rushing offense "
                f"vs {bet_short} rush defense",

            "off_team":
                opp_short,

            "def_team":
                bet_short,

            "off_rank":
                _rank_value(
                    ranked_stats,
                    opp_id,
                    "off_rush_epa_rank",
                ),

            "def_rank":
                _rank_value(
                    ranked_stats,
                    bet_id,
                    "def_rush_epa_rank",
                ),

            "stat":
                "rush EPA",
        },

        {
            "dimension":
                f"{bet_short} scoring-opportunity "
                f"creation vs {opp_short} "
                f"scoring-opportunity prevention",

            "off_team":
                bet_short,

            "def_team":
                opp_short,

            "off_rank":
                _rank_value(
                    ranked_stats,
                    bet_id,
                    "off_eckel_rank",
                ),

            "def_rank":
                _rank_value(
                    ranked_stats,
                    opp_id,
                    "def_eckel_rank",
                ),

            "stat":
                "Eckel rate",
        },

        {
            "dimension":
                f"{opp_short} scoring-opportunity "
                f"creation vs {bet_short} "
                f"scoring-opportunity prevention",

            "off_team":
                opp_short,

            "def_team":
                bet_short,

            "off_rank":
                _rank_value(
                    ranked_stats,
                    opp_id,
                    "off_eckel_rank",
                ),

            "def_rank":
                _rank_value(
                    ranked_stats,
                    bet_id,
                    "def_eckel_rank",
                ),

            "stat":
                "Eckel rate",
        },
    ]

    usable = []

    for item in candidates:

        off_rank = (
            item[
                "off_rank"
            ]
        )

        def_rank = (
            item[
                "def_rank"
            ]
        )

        if (
            off_rank is None
            or def_rank is None
        ):
            continue

        differential = abs(
            def_rank
            - off_rank
        )

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
        key=lambda x: (
            x[
                "matchup_score"
            ]
        ),
        reverse=True,
    )

    return usable[:3]


# --------------------------------------------------------------------------- #
# Model context
# --------------------------------------------------------------------------- #

def build_model_context(
    *,
    bet_name: str,
    bet_short: str,
    opponent_name: str,
    opponent_short: str,
    model_prediction: Any,
    market_line: Any,
    best_line: Any,
    best_price: Any,
    cover_probability: Any,
    edge: Any,
    has_bet: bool,
) -> Dict[str, Any]:

    return {
        "model_side_full":
            bet_name,

        "model_side":
            bet_short,

        "opponent_full":
            opponent_name,

        "opponent":
            opponent_short,

        "model_prediction":
            model_prediction,

        "market_line":
            market_line,

        "best_available_line":
            best_line,

        "best_available_price":
            best_price,

        "cover_probability":
            cover_probability,

        "edge":
            edge,

        "bet_status":
            (
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
    away_short: str,
    home_short: str,
    model_context: Dict[str, Any],
    matchup_angles: List[
        Dict[str, Any]
    ],
    guide_results: List[
        Dict[str, Any]
    ],
    model: str = DEFAULT_OPENAI_MODEL,
) -> Dict[str, Any]:

    verified_facts = (
        flatten_verified_facts(
            guide_results
        )
    )

    if not verified_facts:

        return {
            "used_guides":
                False,

            "narrative":
                "",

            "matchup_to_watch":
                "",

            "used_fact_ids":
                [],

            "used_sources":
                [],
        }

    numbered_facts = []

    for (
        idx,
        fact,
    ) in enumerate(
        verified_facts,
        start=1,
    ):

        numbered_facts.append(
            {
                "fact_id":
                    idx,

                **fact,
            }
        )

    prompt = f"""
GAME:
{away_name} at {home_name}

CONVERSATIONAL TEAM NAMES:
Away: {away_short}
Home: {home_short}

DETERMINISTIC MODEL CONTEXT:
{json.dumps(model_context, indent=2, default=str)}

MATCHUP ANGLES:
{json.dumps(matchup_angles, indent=2, default=str)}

VERIFIED MEDIA-GUIDE FACTS:
{json.dumps(numbered_facts, indent=2, default=str)}

Write two portions of an article in OUR first-person plural voice.

Return valid JSON only:

{{
  "narrative": "2-3 concise paragraphs",
  "matchup_to_watch": "1-2 concise paragraphs",
  "used_fact_ids": [1, 4, 7]
}}

IMPORTANT STRUCTURE

The deterministic opening has ALREADY told the reader:
- what we project
- the market line
- the best available line and price
- our cover probability
- our edge
- whether this is a bet or pass

DO NOT repeat those exact numbers in the narrative.

TEAM-NAME RULE

Use the conversational team names supplied above.

Examples:
- Syracuse
- California
- Alabama
- Kentucky

Do not repeatedly use mascot names in normal prose.

NARRATIVE GOAL

Explain the football reasons that make our model/market disagreement
interesting.

Use the guide to add real football context.

The ideal narrative answers:

"What are we seeing in this matchup that helps explain why our overall
expectation differs from the market?"

NARRATIVE REQUIREMENTS

- Always use first-person plural voice.
- Say "we", "our model", "our numbers", "our view", "for us".
- Never say "BTB thinks", "BTB projects", or "BTB sees".
- Use recent results as context rather than proof.
- Focus on two or three meaningful ideas.
- Avoid simply reciting rankings.
- Avoid repeating probability, edge, line, and price numbers.
- Remain accessible to a recreational bettor.
- Avoid an analytics lecture.
- Avoid excessive hedging.
- Make the analytical conclusion clear.

If this is a BET:

Explain the football context supporting our position while acknowledging one
meaningful concern when useful.

Do not spend multiple paragraphs talking the reader out of our own wager.

If this is a PASS:

Explain why we may see a disagreement but do not have enough value at the
current price.

MATCHUP TO WATCH

Choose the matchup most useful for understanding the game and our view.

Do not simply choose the largest rank gap.

Use first-person voice here too.

Do not repeat exact betting numbers from the opening.

FACT REQUIREMENTS

Every media-guide-derived factual claim must come from VERIFIED MEDIA-GUIDE
FACTS.

Return all fact IDs actually used.

Do not manufacture facts.
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

    fact_by_id = {
        x["fact_id"]: x
        for x in numbered_facts
    }

    used_sources = []

    for fact_id in (
        parsed.get(
            "used_fact_ids",
            [],
        )
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

        source = (
            fact_by_id.get(
                fact_id
            )
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
# Load guides
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
    List[
        Dict[str, Any]
    ],
    List[str],
]:

    results = []
    status = []

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

        if team_id is None:

            status.append(
                f"No team_id available for "
                f"{team_name}; cannot locate guide."
            )

            continue

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

        path = (
            guide_path_for_team(
                team_id=team_id,
                season=season,
                crosswalk=crosswalk,
                guide_root=guide_root,
            )
        )

        if path is None:

            status.append(
                f"No guide found for "
                f"{team_name}. "
                f"Expected PDF: "
                f"{expected_path}"
            )

            continue

        status.append(
            f"Found guide for "
            f"{team_name}: "
            f"{path}"
        )

        try:

            extracted = (
                extract_guide_facts(
                    pdf_path=path,
                    source_team_name=team_name,
                    opponent_name=opponent_name,
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
                    f"Guide for {team_name} was found "
                    f"but may not match the current opponent. "
                    f"{reason}"
                )

        except Exception as exc:

            status.append(
                f"Guide processing failed "
                f"for {team_name}: "
                f"{exc}"
            )

    return (
        results,
        status,
    )
