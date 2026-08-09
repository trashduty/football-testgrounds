#!/usr/bin/env python3
"""
Weekly TikTok SEO report for BTB Analytics.

Data source:
  KeywordTool.io API v2
  - TikTok suggestions
  - TikTok estimated search volume

Outputs:
  data/tiktok/latest_keywords.csv
  data/tiktok/keyword_history.csv
  data/tiktok/latest_report.md
  data/tiktok/weekly_reports/YYYY-MM-DD.md
  data/tiktok/weekly_reports/YYYY-MM-DD.csv

API key:
  Environment variable KEYWORDTOOL_API_KEY
"""

from __future__ import annotations

import csv
import math
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List

import requests
import yaml


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "tiktok_seeds.yml"
DATA_DIR = ROOT / "data" / "tiktok"
WEEKLY_DIR = DATA_DIR / "weekly_reports"

SUGGESTIONS_URL = "https://api.keywordtool.io/v2/search/suggestions/tiktok"
VOLUME_URL = "https://api.keywordtool.io/v2/search/volume/tiktok"

SESSION = requests.Session()
SESSION.headers.update(
    {
        "Content-Type": "application/json",
        "User-Agent": "BTB-Analytics-TikTok-SEO/1.0",
    }
)


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_keyword(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" -–—:;,./")
    return value


def api_post(
    url: str,
    payload: dict,
    retries: int = 4,
) -> dict:
    """
    POST to KeywordTool.io.

    KeywordTool.io can return HTTP 404 for validation/API errors,
    not just for missing URLs. Therefore, print the actual returned
    body instead of assuming the endpoint does not exist.

    Only network errors and rate limiting are retried.
    """

    for attempt in range(retries):
        try:
            response = SESSION.post(
                url,
                json=payload,
                timeout=60,
            )

            try:
                body = response.json()
            except ValueError:
                body = None

            # Rate limit
            if response.status_code == 429:
                wait = min(
                    10 * (attempt + 1),
                    45,
                )

                detail = (
                    body
                    if body is not None
                    else response.text[:500]
                )

                print(
                    f"Rate limited: {detail}. "
                    f"Sleeping {wait}s..."
                )

                time.sleep(wait)
                continue

            # KeywordTool uses 4xx responses for validation,
            # quota, plan and keyword errors.
            if response.status_code >= 400:
                detail = (
                    body
                    if body is not None
                    else response.text[:1000]
                )

                raise RuntimeError(
                    f"KeywordTool API error "
                    f"HTTP {response.status_code} "
                    f"for {url}: {detail}"
                )

            if body is None:
                raise RuntimeError(
                    f"KeywordTool returned a non-JSON response "
                    f"for {url}: "
                    f"{response.text[:1000]}"
                )

            if (
                isinstance(body, dict)
                and body.get("error")
            ):
                raise RuntimeError(
                    f"KeywordTool API error: "
                    f"{body['error']}"
                )

            return body

        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise

            wait = 2 ** attempt

            print(
                f"Network attempt "
                f"{attempt + 1} failed: "
                f"{exc}. "
                f"Retrying in {wait}s..."
            )

            time.sleep(wait)

    raise RuntimeError(
        "API request failed unexpectedly."
    )


def flatten_suggestion_response(
    body: dict,
) -> List[str]:
    """
    Extract likely keyword strings from the TikTok
    suggestions response.

    Important:
    KeywordTool.io search-volume requests allow:
      - maximum 80 characters
      - maximum 10 words

    TikTok suggestions can sometimes exceed those limits.
    Those suggestions are removed here so one invalid term
    cannot cause an entire volume batch to fail.
    """

    out: List[str] = []

    ignore_keys = {
        "string",
        "volume",
        "trend",
        "cpc",
        "cmp",
        "total_keywords",
        "m1",
        "m2",
        "m3",
        "m4",
        "m5",
        "m6",
        "m7",
        "m8",
        "m9",
        "m10",
        "m11",
        "m12",
    }

    def walk(obj, parent_key=""):
        if isinstance(obj, dict):

            # Some KeywordTool results include
            # the actual keyword in "string".
            if isinstance(
                obj.get("string"),
                str,
            ):
                out.append(
                    obj["string"]
                )

            for key, val in obj.items():
                if key in ignore_keys:
                    continue

                # Some responses use the keyword
                # itself as a dictionary key.
                if (
                    parent_key == "results"
                    and isinstance(key, str)
                    and key != "results"
                ):
                    if (
                        " " in key
                        or key.isalpha()
                    ):
                        out.append(key)

                walk(
                    val,
                    key,
                )

        elif isinstance(obj, list):
            for item in obj:
                walk(
                    item,
                    parent_key,
                )

        elif isinstance(obj, str):
            if (
                len(obj) >= 3
                and not obj.startswith("http")
            ):
                out.append(obj)

    walk(body)

    cleaned = []
    seen = set()

    for item in out:
        kw = normalize_keyword(item)

        if not kw:
            continue

        if kw in seen:
            continue

        # IMPORTANT FIX:
        # Search volume endpoint permits no more than
        # 80 characters and 10 words.
        if len(kw) > 80:
            continue

        if len(kw.split()) > 10:
            continue

        seen.add(kw)
        cleaned.append(kw)

    return cleaned


def fetch_suggestions(
    api_key: str,
    seed: str,
    cfg: dict,
) -> List[str]:

    payload = {
        "apikey": api_key,
        "keyword": seed,
        "country": cfg.get(
            "country",
            "US",
        ),
        "language": cfg.get(
            "language",
            "en",
        ),
        "type": "suggestions",
        "metrics": False,
        "output": "json",
    }

    body = api_post(
        SUGGESTIONS_URL,
        payload,
    )

    return flatten_suggestion_response(
        body
    )


def chunks(
    items: List[str],
    size: int,
) -> Iterable[List[str]]:

    for i in range(
        0,
        len(items),
        size,
    ):
        yield items[
            i : i + size
        ]


def numeric(value):
    try:
        if (
            value is None
            or value == ""
        ):
            return None

        return float(value)

    except (
        TypeError,
        ValueError,
    ):
        return None


def extract_volume_results(
    body: dict,
) -> List[dict]:

    results = (
        body.get(
            "results",
            {},
        )
        if isinstance(body, dict)
        else {}
    )

    if isinstance(results, list):
        raw = results

    elif isinstance(results, dict):
        raw = []

        for key, value in results.items():

            if isinstance(value, dict):
                row = dict(value)

                row.setdefault(
                    "string",
                    key,
                )

                raw.append(row)

    else:
        raw = []

    parsed = []

    for row in raw:

        keyword = normalize_keyword(
            str(
                row.get(
                    "string",
                    "",
                )
            )
        )

        if not keyword:
            continue

        monthly = []

        for i in range(
            1,
            13,
        ):

            volume_value = row.get(
                f"m{i}"
            )

            month_value = row.get(
                f"m{i}_month"
            )

            year_value = row.get(
                f"m{i}_year"
            )

            if volume_value is None:
                continue

            try:
                volume_value = float(
                    volume_value
                )
            except (
                TypeError,
                ValueError,
            ):
                continue

            monthly.append(
                {
                    "index": i,
                    "volume": volume_value,
                    "month": month_value,
                    "year": year_value,
                }
            )

        parsed.append(
            {
                "keyword": keyword,
                "volume": numeric(
                    row.get("volume")
                ),
                "api_trend": numeric(
                    row.get("trend")
                ),
                "cmp": numeric(
                    row.get("cmp")
                ),
                "monthly": monthly,
            }
        )

    return parsed


def fetch_volumes(
    api_key: str,
    keywords: List[str],
    cfg: dict,
) -> List[dict]:
    """
    Request search volume in batches of up to
    1,000 keywords.

    Includes a second defensive filter in case a keyword
    enters the pipeline from somewhere other than the
    TikTok suggestions function.
    """

    valid_keywords = [
        kw
        for kw in keywords
        if (
            len(kw) <= 80
            and len(kw.split()) <= 10
        )
    ]

    dropped = (
        len(keywords)
        - len(valid_keywords)
    )

    if dropped:
        print(
            f"Dropped {dropped} keyword(s) "
            f"that exceed KeywordTool's "
            f"80-character/10-word "
            f"search-volume limits."
        )

    all_rows = []

    for batch_num, batch in enumerate(
        chunks(
            valid_keywords,
            1000,
        ),
        start=1,
    ):

        print(
            f"Volume batch "
            f"{batch_num}: "
            f"{len(batch)} keywords"
        )

        payload = {
            "apikey": api_key,
            "keyword": batch,

            # United States
            "metrics_location": [
                2840
            ],

            # English
            "metrics_language": [
                "en"
            ],

            "metrics_currency": "USD",
            "output": "json",
        }

        body = api_post(
            VOLUME_URL,
            payload,
        )

        parsed = extract_volume_results(
            body
        )

        print(
            f"  -> received volume data "
            f"for {len(parsed)} keywords"
        )

        all_rows.extend(parsed)

        # KeywordTool documents a rolling
        # API request-rate limit.
        time.sleep(4.2)

    return all_rows


def pct_change(
    new: float | None,
    old: float | None,
) -> float | None:

    if (
        new is None
        or old in (
            None,
            0,
        )
    ):
        return None

    return (
        new - old
    ) / old


def monthly_metrics(
    monthly: List[dict],
) -> dict:

    # KeywordTool documents m1 as the
    # most recent available month.
    vols = [
        m["volume"]
        for m in monthly
        if m.get("volume")
        is not None
    ]

    if not vols:
        return {
            "latest_month_volume": None,
            "mom_change": None,
            "three_month_vs_prior": None,
            "six_month_vs_prior": None,
            "peak_month_volume": None,
        }

    latest = vols[0]

    mom = (
        pct_change(
            vols[0],
            vols[1],
        )
        if len(vols) >= 2
        else None
    )

    if len(vols) >= 3:
        three = statistics.mean(
            vols[:3]
        )
    else:
        three = statistics.mean(
            vols
        )

    prior3 = (
        statistics.mean(
            vols[3:6]
        )
        if len(vols) >= 6
        else None
    )

    three_vs_prior = pct_change(
        three,
        prior3,
    )

    if len(vols) >= 6:
        six = statistics.mean(
            vols[:6]
        )
    else:
        six = statistics.mean(
            vols
        )

    prior6 = (
        statistics.mean(
            vols[6:12]
        )
        if len(vols) >= 12
        else None
    )

    six_vs_prior = pct_change(
        six,
        prior6,
    )

    return {
        "latest_month_volume": latest,
        "mom_change": mom,
        "three_month_vs_prior": (
            three_vs_prior
        ),
        "six_month_vs_prior": (
            six_vs_prior
        ),
        "peak_month_volume": max(
            vols
        ),
    }


def detect_sport(
    keyword: str,
) -> str:

    k = keyword.lower()

    nfl_markers = [
        "nfl",
        "super bowl",
        "afc",
        "nfc",
    ]

    cfb_markers = [
        "college football",
        "cfb",
        "ncaa football",
        "big ten",
        "sec football",
        "acc football",
        "big 12 football",
        "college playoff",
        "college football playoff",
    ]

    if any(
        x in k
        for x in cfb_markers
    ):
        return "CFB"

    if any(
        x in k
        for x in nfl_markers
    ):
        return "NFL"

    return "Football"


def has_any(
    keyword: str,
    terms: List[str],
) -> bool:

    k = keyword.lower()

    return any(
        term.lower() in k
        for term in terms
    )


def log_norm(
    value: float | None,
    ceiling: float,
) -> float:

    if (
        value is None
        or value <= 0
    ):
        return 0.0

    return min(
        1.0,
        math.log1p(value)
        / math.log1p(
            max(
                ceiling,
                1,
            )
        ),
    )


def clamp01(
    x: float,
) -> float:

    return max(
        0.0,
        min(
            1.0,
            x,
        ),
    )


def momentum_score(
    row: dict,
) -> float:

    vals = []

    for key in (
        "mom_change",
        "three_month_vs_prior",
        "six_month_vs_prior",
        "api_trend",
    ):

        v = row.get(key)

        if v is not None:

            vals.append(
                clamp01(
                    (
                        v + 1.0
                    )
                    / 3.0
                )
            )

    if vals:
        return statistics.mean(
            vals
        )

    return 0.33


def intent_score(
    keyword: str,
    intent_terms: List[str],
) -> float:

    matches = sum(
        1
        for term in intent_terms
        if term.lower()
        in keyword.lower()
    )

    if matches >= 2:
        return 1.0

    if matches == 1:
        return 0.8

    # More specific team/game searches
    # can still be valuable even without
    # an explicit betting term.
    if len(
        keyword.split()
    ) >= 3:
        return 0.45

    return 0.25


def specificity_score(
    keyword: str,
) -> float:

    wc = len(
        keyword.split()
    )

    if 4 <= wc <= 8:
        return 1.0

    if wc == 3:
        return 0.8

    if wc == 2:
        return 0.55

    if wc > 8:
        return 0.7

    return 0.3


def create_opportunity_scores(
    rows: List[dict],
    cfg: dict,
) -> None:

    max_volume = max(
        [
            r.get("volume") or 0
            for r in rows
        ]
        + [1]
    )

    intent_terms = cfg.get(
        "intent_terms",
        [],
    )

    negative_terms = cfg.get(
        "negative_terms",
        [],
    )

    for r in rows:

        volume = log_norm(
            r.get("volume"),
            max_volume,
        )

        momentum = momentum_score(
            r
        )

        intent = intent_score(
            r["keyword"],
            intent_terms,
        )

        specificity = (
            specificity_score(
                r["keyword"]
            )
        )

        negative = (
            0.25
            if has_any(
                r["keyword"],
                negative_terms,
            )
            else 0.0
        )

        # BTB-specific score:
        #
        # 45% search demand
        # 25% momentum
        # 20% search/BTB intent
        # 10% specificity

        score = 100 * (
            0.45 * volume
            + 0.25 * momentum
            + 0.20 * intent
            + 0.10 * specificity
        )

        score *= (
            1.0 - negative
        )

        r[
            "opportunity_score"
        ] = round(
            score,
            1,
        )


def to_hashtag(
    text: str,
) -> str:

    parts = re.findall(
        r"[A-Za-z0-9]+",
        text,
    )

    if not parts:
        return ""

    return "".join(
        (
            p[0].upper()
            + p[1:]
        )
        if p
        else ""
        for p in parts
    )


def hashtag_set(
    keyword: str,
    sport: str,
    cfg: dict,
) -> List[str]:

    tags = []

    exact = to_hashtag(
        keyword
    )

    if (
        exact
        and len(exact) <= 45
    ):
        tags.append(exact)

    words = keyword.split()

    if len(words) >= 4:
        shortened = to_hashtag(
            " ".join(
                words[:4]
            )
        )

        if shortened:
            tags.append(
                shortened
            )

    base = cfg.get(
        "base_hashtags",
        {},
    )

    if sport == "CFB":
        tags.extend(
            base.get(
                "cfb",
                [],
            )
        )

    elif sport == "NFL":
        tags.extend(
            base.get(
                "nfl",
                [],
            )
        )

    tags.extend(
        base.get(
            "generic",
            [],
        )
    )

    deduped = []
    seen = set()

    for tag in tags:

        tag = re.sub(
            r"[^A-Za-z0-9]",
            "",
            tag,
        )

        if not tag:
            continue

        if tag.lower() in seen:
            continue

        seen.add(
            tag.lower()
        )

        deduped.append(
            "#" + tag
        )

    return deduped[:5]


def content_title(
    keyword: str,
) -> str:

    k = keyword.strip()

    if (
        "prediction" in k
        and not k.endswith(
            "predictions"
        )
    ):
        return (
            f"{k.title()}: "
            f"What Our Model Says"
        )

    if (
        "picks" in k
        or "best bet" in k
    ):
        return (
            f"{k.title()} — "
            f"Model-Based Breakdown"
        )

    return (
        f"{k.title()}: "
        f"Data-Driven Breakdown"
    )


def opening_line(
    keyword: str,
) -> str:

    return (
        f"If you're searching for "
        f"{keyword}, here's what our "
        f"model says and where it "
        f"differs from the market."
    )


def fmt_pct(
    value,
) -> str:

    if value is None:
        return "—"

    return (
        f"{value * 100:+.0f}%"
    )


def fmt_num(
    value,
) -> str:

    if value is None:
        return "—"

    return (
        f"{int(round(value)):,}"
    )


def save_csv(
    path: Path,
    rows: List[dict],
) -> None:

    fields = [
        "run_date",
        "rank",
        "sport",
        "keyword",
        "volume",
        "latest_month_volume",
        "mom_change",
        "three_month_vs_prior",
        "six_month_vs_prior",
        "api_trend",
        "peak_month_volume",
        "opportunity_score",
        "source_seeds",
        "hashtags",
        "suggested_title",
        "suggested_opening",
    ]

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    field: row.get(
                        field,
                        "",
                    )
                    for field in fields
                }
            )


def append_history(
    path: Path,
    rows: List[dict],
) -> None:

    fields = [
        "run_date",
        "keyword",
        "sport",
        "volume",
        "latest_month_volume",
        "mom_change",
        "three_month_vs_prior",
        "six_month_vs_prior",
        "api_trend",
        "opportunity_score",
    ]

    exists = path.exists()

    with path.open(
        "a",
        encoding="utf-8",
        newline="",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        if not exists:
            writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    field: row.get(
                        field,
                        "",
                    )
                    for field in fields
                }
            )


def markdown_report(
    rows: List[dict],
    run_date: str,
) -> str:

    top = rows[:20]

    rising = sorted(
        [
            r
            for r in rows
            if r.get(
                "mom_change"
            )
            is not None
        ],
        key=lambda r: (
            r.get(
                "mom_change"
            )
            or -999
        ),
        reverse=True,
    )[:10]

    lines = [
        (
            f"# BTB TikTok Search Report "
            f"— {run_date}"
        ),
        "",
        (
            "> Source: KeywordTool.io "
            "TikTok estimated search volume "
            "and TikTok keyword suggestions."
        ),
        (
            "> Search volume is estimated; "
            "use it primarily for relative "
            "demand and trend comparison."
        ),
        "",
        "## Top 20 Search Opportunities",
        "",
        (
            "| Rank | Sport | Search query | "
            "Avg monthly volume | Latest month | "
            "MoM | 3-mo momentum | Score |"
        ),
        (
            "|---:|---|---|---:|---:|"
            "---:|---:|---:|"
        ),
    ]

    for r in top:
        lines.append(
            f"| {r['rank']} "
            f"| {r['sport']} "
            f"| {r['keyword']} "
            f"| {fmt_num(r['volume'])} "
            f"| {fmt_num(r['latest_month_volume'])} "
            f"| {fmt_pct(r['mom_change'])} "
            f"| {fmt_pct(r['three_month_vs_prior'])} "
            f"| **{r['opportunity_score']:.1f}** |"
        )

    lines += [
        "",
        "## Recommended TikToks This Week",
        "",
    ]

    for r in rows[:10]:

        lines += [
            (
                f"### {r['rank']}. "
                f"{r['keyword']}"
            ),
            "",
            (
                f"- **Opportunity score:** "
                f"{r['opportunity_score']:.1f}/100"
            ),
            (
                f"- **Estimated avg. monthly searches:** "
                f"{fmt_num(r['volume'])}"
            ),
            (
                f"- **Latest monthly estimate:** "
                f"{fmt_num(r['latest_month_volume'])}"
            ),
            (
                f"- **Suggested on-screen title:** "
                f"{r['suggested_title']}"
            ),
            (
                f"- **Suggested opening:** "
                f"{r['suggested_opening']}"
            ),
            (
                f"- **Hashtags:** "
                f"{r['hashtags']}"
            ),
            "",
        ]

    lines += [
        "## Fastest-Rising Searches",
        "",
        (
            "| Search query | Sport | MoM | "
            "3-mo momentum | Avg monthly volume | Score |"
        ),
        (
            "|---|---|---:|---:|---:|---:|"
        ),
    ]

    for r in rising:

        lines.append(
            f"| {r['keyword']} "
            f"| {r['sport']} "
            f"| {fmt_pct(r['mom_change'])} "
            f"| {fmt_pct(r['three_month_vs_prior'])} "
            f"| {fmt_num(r['volume'])} "
            f"| {r['opportunity_score']:.1f} |"
        )

    lines += [
        "",
        "## How to Use This Report",
        "",
        (
            "Prioritize search phrases that combine "
            "meaningful volume, positive momentum, "
            "and clear football intent. Use the target "
            "phrase naturally in the spoken hook, "
            "on-screen text, and caption. Hashtags are "
            "supplemental."
        ),
        "",
    ]

    return "\n".join(
        lines
    )


def main() -> int:

    api_key = os.getenv(
        "KEYWORDTOOL_API_KEY",
        "",
    ).strip()

    if not api_key:
        print(
            "ERROR: KEYWORDTOOL_API_KEY "
            "environment variable is not set.",
            file=sys.stderr,
        )

        return 2

    cfg = load_config()

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    WEEKLY_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    priority = (
        cfg.get(
            "priority_seeds",
            [],
        )
        or []
    )

    evergreen = (
        cfg.get(
            "evergreen_seeds",
            [],
        )
        or []
    )

    seeds = []
    seen_seeds = set()

    for seed in (
        priority
        + evergreen
    ):

        seed = normalize_keyword(
            seed
        )

        if (
            seed
            and seed
            not in seen_seeds
        ):
            seen_seeds.add(seed)
            seeds.append(seed)

    max_seed_requests = int(
        cfg.get(
            "max_seed_requests",
            22,
        )
    )

    seeds = seeds[
        :max_seed_requests
    ]

    max_per_seed = int(
        cfg.get(
            "max_suggestions_per_seed",
            150,
        )
    )

    keyword_sources: Dict[
        str,
        set,
    ] = defaultdict(set)

    # Always include seed keywords themselves.
    for seed in seeds:
        keyword_sources[
            seed
        ].add(seed)

    # -----------------------------
    # TikTok suggestion discovery
    # -----------------------------

    for i, seed in enumerate(
        seeds,
        start=1,
    ):

        print(
            f"[{i}/{len(seeds)}] "
            f"Suggestions: {seed}"
        )

        suggestions = (
            fetch_suggestions(
                api_key,
                seed,
                cfg,
            )[:max_per_seed]
        )

        print(
            f"  -> {len(suggestions)} "
            f"cleaned suggestions"
        )

        for kw in suggestions:
            keyword_sources[
                kw
            ].add(seed)

        # Remain safely below the
        # rolling API request limit.
        time.sleep(4.2)

    keywords = sorted(
        keyword_sources
    )

    print(
        f"Unique keyword universe: "
        f"{len(keywords):,}"
    )

    # -----------------------------
    # Search volume lookup
    # -----------------------------

    volume_rows = fetch_volumes(
        api_key,
        keywords,
        cfg,
    )

    by_keyword = {
        r["keyword"]: r
        for r in volume_rows
    }

    rows = []

    run_date = (
        date.today()
        .isoformat()
    )

    for kw in keywords:

        metric = by_keyword.get(
            kw
        )

        if not metric:
            continue

        row = {
            "run_date": run_date,
            "keyword": kw,
            "volume": metric.get(
                "volume"
            ),
            "api_trend": metric.get(
                "api_trend"
            ),
            "cmp": metric.get(
                "cmp"
            ),
            "sport": detect_sport(
                kw
            ),
            "source_seeds": "; ".join(
                sorted(
                    keyword_sources[
                        kw
                    ]
                )
            ),
        }

        row.update(
            monthly_metrics(
                metric.get(
                    "monthly",
                    [],
                )
            )
        )

        rows.append(
            row
        )

    if not rows:
        raise RuntimeError(
            "No search-volume rows were returned. "
            "Check the KeywordTool API response printed "
            "above and confirm your account has access "
            "to TikTok search-volume data."
        )

    # -----------------------------
    # Score opportunities
    # -----------------------------

    create_opportunity_scores(
        rows,
        cfg,
    )

    min_volume = float(
        cfg.get(
            "min_report_volume",
            50,
        )
    )

    report_rows = [
        r
        for r in rows
        if (
            r.get("volume")
            or 0
        )
        >= min_volume
    ]

    report_rows.sort(
        key=lambda r: (
            r.get(
                "opportunity_score"
            )
            or 0,
            r.get(
                "volume"
            )
            or 0,
        ),
        reverse=True,
    )

    for rank, row in enumerate(
        report_rows,
        start=1,
    ):

        row["rank"] = rank

        tags = hashtag_set(
            row["keyword"],
            row["sport"],
            cfg,
        )

        row["hashtags"] = (
            " ".join(tags)
        )

        row["suggested_title"] = (
            content_title(
                row["keyword"]
            )
        )

        row["suggested_opening"] = (
            opening_line(
                row["keyword"]
            )
        )

    top_n = int(
        cfg.get(
            "top_n",
            50,
        )
    )

    ranked = report_rows[
        :top_n
    ]

    # -----------------------------
    # Output files
    # -----------------------------

    latest_csv = (
        DATA_DIR
        / "latest_keywords.csv"
    )

    weekly_csv = (
        WEEKLY_DIR
        / f"{run_date}.csv"
    )

    latest_md = (
        DATA_DIR
        / "latest_report.md"
    )

    weekly_md = (
        WEEKLY_DIR
        / f"{run_date}.md"
    )

    history_csv = (
        DATA_DIR
        / "keyword_history.csv"
    )

    save_csv(
        latest_csv,
        ranked,
    )

    save_csv(
        weekly_csv,
        ranked,
    )

    append_history(
        history_csv,
        report_rows,
    )

    report = markdown_report(
        ranked,
        run_date,
    )

    latest_md.write_text(
        report,
        encoding="utf-8",
    )

    weekly_md.write_text(
        report,
        encoding="utf-8",
    )

    print("")
    print("Report generation complete.")
    print(
        f"Wrote: {latest_csv}"
    )
    print(
        f"Wrote: {latest_md}"
    )
    print(
        f"Wrote: {history_csv}"
    )
    print(
        f"Wrote weekly snapshot: "
        f"{weekly_md}"
    )
    print(
        f"Ranked opportunities: "
        f"{len(ranked)}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
