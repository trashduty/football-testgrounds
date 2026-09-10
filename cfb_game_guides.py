#!/usr/bin/env python3
"""
CFB game-guide ingestion + BTB narrative generation.

Responsibilities:
1. Resolve media-guide PDFs using crosswalk.btb_team_short.
2. Extract text from available PDFs.
3. Use OpenAI to convert the guide into structured, source-grounded facts.
4. Identify the most meaningful statistical matchup angles.
5. Generate concise BTB-style narrative from:
      - verified guide facts
      - deterministic BTB/CFBD statistics
      - model/market information
6. Save source metadata for auditing.

The model NEVER determines whether a wager is a bet/pass.
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


DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_ARTICLE_MODEL", "gpt-5.6")

MAX_GUIDE_CHARS = 110_000

BTB_SYSTEM_PROMPT = """
You are an editorial analyst writing for BTB Analytics.

BTB Analytics uses quantitative models to identify differences between its
expectation and the betting market. The writing should sound sharp,
skeptical, informed, and readable without sounding academic or like a tout.

CORE BTB PHILOSOPHY

The question is not simply:
"Which team is better?"

The question is:
"Is the market price consistent with what we know about these teams?"

Distinguish carefully between:
1. What happened.
2. What could matter going forward.
3. What the market already appears to price in.

Recent performance is context, not proof.

Never imply:
- a team will win because it won last week
- one isolated stat guarantees future performance
- a matchup is easy
- a bet is guaranteed
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

ANALYTICS STYLE

Use analytics to support the argument, not overwhelm the reader.

Do not repeat every statistic supplied.

Prioritize 2-3 matchup ideas that explain why the model and market may differ.

If the guide contains a dramatic recent result, distinguish the final score
from the underlying information that may actually matter.

SOURCE DISCIPLINE

You may ONLY state guide-derived facts that appear in the supplied verified
facts. Never invent injuries, personnel changes, roles, coaches, statistics,
rankings, previous results, quotes, or tactical information.

If evidence is weak, omit it.

Do not present media-guide promotional language as objective truth.
Team-issued guides are source material, not independent analysis.

BETTING DISCIPLINE

The deterministic program supplies the final wager designation.
You must not upgrade a pass to a bet or downgrade a bet to a pass.

Do not introduce any point spread, price, probability, or model number that
was not provided in the structured model context.
"""


EXTRACTION_SYSTEM_PROMPT = """
You extract factual football information from team-issued game notes/media
guides for BTB Analytics.

Your goal is NOT to summarize the entire document.

Extract only information useful for explaining the upcoming football matchup.

Prioritize:
1. Upcoming opponent-specific notes.
2. Most recent game performance.
3. Injuries, returns, or availability information explicitly stated.
4. Quarterback information.
5. Offensive line information.
6. Offensive/defensive personnel changes.
7. New coaches, coordinators, or play callers.
8. Transfers and first-time starters.
9. Recent offensive or defensive tendencies.
10. Individual players plausibly relevant to the matchup.
11. Special-teams developments if meaningful.
12. Series history only when unusually relevant.

De-prioritize or ignore:
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

IMPORTANT:
A team-issued media guide is promotional material. Extract statements as
facts only when they are concrete, such as statistics, dates, personnel,
results, or clearly stated roles.

Do not convert subjective promotional claims into facts.

Return valid JSON only.
"""


def _client() -> OpenAI:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. "
            "Add it as a GitHub Actions secret or local environment variable."
        )
    return OpenAI(api_key=key)


def clean_filename_component(value: str) -> str:
    """
    Preserve readable BTB short names while preventing path problems.
    Spaces remain intact.
    """
    value = str(value).strip()
    value = re.sub(r'[<>:"/\\\\|?*]', "", value)
    return value.strip()


def team_short_lookup(crosswalk: pd.DataFrame) -> Dict[int, str]:
    cw = crosswalk.dropna(subset=["team_id", "btb_team_short"]).copy()
    cw["team_id"] = cw["team_id"].astype(int)
    return cw.set_index("team_id")["btb_team_short"].astype(str).to_dict()


def team_full_lookup(crosswalk: pd.DataFrame) -> Dict[int, str]:
    cw = crosswalk.dropna(subset=["team_id", "btb_team"]).copy()
    cw["team_id"] = cw["team_id"].astype(int)
    return cw.set_index("team_id")["btb_team"].astype(str).to_dict()


def guide_path_for_team(
    team_id: int,
    season: int,
    crosswalk: pd.DataFrame,
    guide_root: Path,
) -> Optional[Path]:
    short_names = team_short_lookup(crosswalk)
    short = short_names.get(int(team_id))
    if not short:
        return None

    filename = f"{clean_filename_component(short)}.pdf"
    path = guide_root / str(season) / filename

    return path if path.exists() else None


def extract_pdf_text(path: Path) -> str:
    """
    Extract embedded PDF text.

    These athletic media guides normally contain real text, so pypdf is
    sufficient and avoids OCR.
    """
    reader = PdfReader(str(path))

    chunks: List[str] = []

    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = text.strip()

        if not text:
            continue

        chunks.append(
            f"\n\n===== SOURCE PDF PAGE {page_number} =====\n\n{text}"
        )

    output = "".join(chunks).strip()

    if len(output) > MAX_GUIDE_CHARS:
        output = output[:MAX_GUIDE_CHARS]

    return output


def _response_text(response: Any) -> str:
    """
    Compatible helper for Responses API output.
    """
    text = getattr(response, "output_text", None)
    if text:
        return text.strip()

    # Defensive fallback.
    try:
        pieces = []
        for item in response.output:
            for content in item.content:
                if getattr(content, "text", None):
                    pieces.append(content.text)
        return "\n".join(pieces).strip()
    except Exception:
        return ""


def extract_guide_facts(
    *,
    pdf_path: Path,
    source_team_name: str,
    opponent_name: str,
    season: int,
    week: int,
    model: str = DEFAULT_OPENAI_MODEL,
) -> Dict[str, Any]:

    text = extract_pdf_text(pdf_path)

    if not text:
        return {
            "source_file": pdf_path.name,
            "source_team": source_team_name,
            "opponent": opponent_name,
            "usable": False,
            "reason": "No extractable PDF text",
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

IMPORTANT VALIDATION:
The document may be stale or may describe a different upcoming opponent.
Determine whether it appears intended for a matchup involving
{source_team_name} and {opponent_name}.

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

Each fact object must contain:
- fact: one concise factual statement
- page: source PDF page number when identifiable
- category
- importance: high, medium, or low

Do not duplicate facts.

If the document clearly describes a different upcoming opponent, set
document_matches_upcoming_game=false. You may still extract recent-team
information that is obviously current, but opponent-specific information
about the wrong opponent should not be returned.

SOURCE DOCUMENT:

{text}
"""

    response = _client().responses.create(
        model=model,
        instructions=EXTRACTION_SYSTEM_PROMPT,
        input=prompt,
        temperature=0.1,
    )

    raw = _response_text(response)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Try extracting the JSON object if the model wrapped it.
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            raise RuntimeError(
                f"Could not parse guide extraction JSON for {pdf_path}"
            )
        parsed = json.loads(match.group(0))

    parsed["source_file"] = pdf_path.name
    parsed["source_team"] = source_team_name
    parsed["opponent"] = opponent_name

    return parsed


def flatten_verified_facts(
    guide_results: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Flatten extracted fact categories while retaining source provenance.
    """
    output: List[Dict[str, Any]] = []

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
        source_file = guide.get("source_file")
        source_team = guide.get("source_team")

        for key, value in guide.items():
            if key in metadata_keys:
                continue

            if not isinstance(value, list):
                continue

            for fact in value:
                if not isinstance(fact, dict):
                    continue

                text = str(fact.get("fact", "")).strip()
                if not text:
                    continue

                output.append(
                    {
                        "fact": text,
                        "page": fact.get("page"),
                        "category": fact.get("category", key),
                        "importance": fact.get("importance", "medium"),
                        "source_file": source_file,
                        "source_team": source_team,
                    }
                )

    return output


def _rank_value(
    ranked_stats: pd.DataFrame,
    team_id: int,
    column: str,
) -> Optional[int]:
    row = ranked_stats[
        ranked_stats["team_id"].astype("Int64") == int(team_id)
    ]

    if row.empty:
        return None

    value = row.iloc[0].get(column)

    if value is None or pd.isna(value):
        return None

    return int(value)


def identify_matchup_angles(
    bet_id: int,
    opp_id: int,
    ranked_stats: pd.DataFrame,
    crosswalk: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Identify the most interesting directional matchups from current
    rolling rankings.

    These are explanatory signals, not independent betting decisions.
    """

    names = team_full_lookup(crosswalk)
    bet_name = names.get(int(bet_id), str(bet_id))
    opp_name = names.get(int(opp_id), str(opp_id))

    candidates = [
        {
            "dimension": f"{bet_name} passing offense vs {opp_name} pass defense",
            "off_team": bet_name,
            "def_team": opp_name,
            "off_rank": _rank_value(
                ranked_stats, bet_id, "off_pass_epa_rank"
            ),
            "def_rank": _rank_value(
                ranked_stats, opp_id, "def_pass_epa_rank"
            ),
            "stat": "pass EPA",
        },
        {
            "dimension": f"{bet_name} rushing offense vs {opp_name} rush defense",
            "off_team": bet_name,
            "def_team": opp_name,
            "off_rank": _rank_value(
                ranked_stats, bet_id, "off_rush_epa_rank"
            ),
            "def_rank": _rank_value(
                ranked_stats, opp_id, "def_rush_epa_rank"
            ),
            "stat": "rush EPA",
        },
        {
            "dimension": f"{opp_name} passing offense vs {bet_name} pass defense",
            "off_team": opp_name,
            "def_team": bet_name,
            "off_rank": _rank_value(
                ranked_stats, opp_id, "off_pass_epa_rank"
            ),
            "def_rank": _rank_value(
                ranked_stats, bet_id, "def_pass_epa_rank"
            ),
            "stat": "pass EPA",
        },
        {
            "dimension": f"{opp_name} rushing offense vs {bet_name} rush defense",
            "off_team": opp_name,
            "def_team": bet_name,
            "off_rank": _rank_value(
                ranked_stats, opp_id, "off_rush_epa_rank"
            ),
            "def_rank": _rank_value(
                ranked_stats, bet_id, "def_rush_epa_rank"
            ),
            "stat": "rush EPA",
        },
        {
            "dimension": f"{bet_name} scoring-opportunity creation vs {opp_name} scoring-opportunity prevention",
            "off_team": bet_name,
            "def_team": opp_name,
            "off_rank": _rank_value(
                ranked_stats, bet_id, "off_eckel_rank"
            ),
            "def_rank": _rank_value(
                ranked_stats, opp_id, "def_eckel_rank"
            ),
            "stat": "Eckel rate",
        },
        {
            "dimension": f"{opp_name} scoring-opportunity creation vs {bet_name} scoring-opportunity prevention",
            "off_team": opp_name,
            "def_team": bet_name,
            "off_rank": _rank_value(
                ranked_stats, opp_id, "off_eckel_rank"
            ),
            "def_rank": _rank_value(
                ranked_stats, bet_id, "def_eckel_rank"
            ),
            "stat": "Eckel rate",
        },
    ]

    usable: List[Dict[str, Any]] = []

    for item in candidates:
        off_rank = item["off_rank"]
        def_rank = item["def_rank"]

        if off_rank is None or def_rank is None:
            continue

        # Large rank differential = potentially useful matchup story.
        differential = abs(def_rank - off_rank)

        # Strength/weakness heuristic.
        if off_rank <= 35 and def_rank >= 75:
            score = 100 + differential
            direction = "offense_advantage"
        elif def_rank <= 35 and off_rank >= 75:
            score = 100 + differential
            direction = "defense_advantage"
        else:
            score = differential
            direction = "mixed"

        item["rank_gap"] = differential
        item["direction"] = direction
        item["matchup_score"] = score

        usable.append(item)

    usable.sort(
        key=lambda x: x["matchup_score"],
        reverse=True,
    )

    return usable[:3]


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

    return {
        "model_side": bet_name,
        "opponent": opponent_name,
        "market_line": market_line,
        "best_available_line": best_line,
        "best_available_price": best_price,
        "cover_probability": cover_probability,
        "edge": edge,
        "bet_status": "BET" if has_bet else "PASS",
    }


def generate_matchup_narrative(
    *,
    away_name: str,
    home_name: str,
    model_context: Dict[str, Any],
    matchup_angles: List[Dict[str, Any]],
    guide_results: List[Dict[str, Any]],
    model: str = DEFAULT_OPENAI_MODEL,
) -> Dict[str, Any]:
    """
    Generate two article sections:
      narrative
      matchup_to_watch

    The output is JSON so rendering remains controlled by Python.
    """

    verified_facts = flatten_verified_facts(guide_results)

    if not verified_facts:
        return {
            "used_guides": False,
            "narrative": "",
            "matchup_to_watch": "",
            "used_fact_ids": [],
        }

    numbered_facts = []

    for idx, fact in enumerate(verified_facts, start=1):
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

Write content for two sections of a BTB Analytics matchup article.

Return valid JSON only:

{{
  "narrative": "2-3 concise paragraphs",
  "matchup_to_watch": "1-2 concise paragraphs",
  "used_fact_ids": [1, 4, 7]
}}

NARRATIVE REQUIREMENTS

The narrative should:
- explain what is actually interesting about this game
- use recent results as context rather than proof
- avoid simply reciting rankings
- focus on 2-3 meaningful ideas
- sound like BTB Analytics rather than a generic preview
- connect football information to why the market/model disagreement is interesting
- acknowledge uncertainty when appropriate

MATCHUP TO WATCH REQUIREMENTS

This should connect one or two BTB statistical matchup angles to relevant
guide context.

Do not say the matchup angle guarantees the bet.

Do not repeat the Bottom Line verbatim.

If the deterministic status is PASS, do not write as though BTB is betting it.

FACT REQUIREMENTS

Every guide-derived factual claim must come from VERIFIED MEDIA-GUIDE FACTS.

Return the fact IDs actually used.

Do not manufacture facts.
"""

    response = _client().responses.create(
        model=model,
        instructions=BTB_SYSTEM_PROMPT,
        input=prompt,
        temperature=0.35,
    )

    raw = _response_text(response)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            raise RuntimeError("Could not parse narrative JSON.")
        parsed = json.loads(match.group(0))

    parsed["used_guides"] = True

    # Resolve provenance for audit file.
    fact_by_id = {x["fact_id"]: x for x in numbered_facts}

    used_sources = []

    for fact_id in parsed.get("used_fact_ids", []):
        try:
            fact_id = int(fact_id)
        except (TypeError, ValueError):
            continue

        source = fact_by_id.get(fact_id)

        if source:
            used_sources.append(source)

    parsed["used_sources"] = used_sources

    return parsed


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
) -> Tuple[List[Dict[str, Any]], List[str]]:

    results: List[Dict[str, Any]] = []
    status: List[str] = []

    pairs = [
        (away_id, away_name, home_name),
        (home_id, home_name, away_name),
    ]

    for team_id, team_name, opponent_name in pairs:

        path = guide_path_for_team(
            team_id=team_id,
            season=season,
            crosswalk=crosswalk,
            guide_root=guide_root,
        )

        if path is None:
            status.append(f"No guide found for {team_name}")
            continue

        try:
            extracted = extract_guide_facts(
                pdf_path=path,
                source_team_name=team_name,
                opponent_name=opponent_name,
                season=season,
                week=week,
                model=model,
            )

            results.append(extracted)

            match_flag = extracted.get(
                "document_matches_upcoming_game",
                True,
            )

            if match_flag:
                status.append(
                    f"Loaded guide for {team_name}: {path.name}"
                )
            else:
                status.append(
                    f"Guide for {team_name} may be stale: "
                    f"{extracted.get('document_match_reason', '')}"
                )

        except Exception as exc:
            status.append(
                f"Guide processing failed for {team_name}: {exc}"
            )

    return results, status
