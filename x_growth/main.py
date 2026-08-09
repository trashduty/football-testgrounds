from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import yaml

from x_growth.search_x import (
    search_recent_posts,
    XAPIError,
)

from x_growth.score_opportunities import (
    score_post,
)

from x_growth.generate_alert import (
    generate_discord_embed,
)

from x_growth.send_discord import (
    send_discord_alert,
)

from x_growth.state import AlertState


ROOT = Path(__file__).resolve().parents[1]

QUERY_CONFIG = (
    ROOT
    / "config"
    / "search_queries.yml"
)

SETTINGS_CONFIG = (
    ROOT
    / "config"
    / "settings.yml"
)

STATE_DIR = (
    ROOT
    / "x_growth"
    / "state"
)

ALERTED_STATE = (
    STATE_DIR
    / "alerted_ids.json"
)

LOG_FILE = (
    STATE_DIR
    / "opportunities.csv"
)


def load_yaml(path: Path) -> dict:

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:

        return yaml.safe_load(f) or {}


def require_env(name: str) -> str:

    value = os.environ.get(name)

    if not value:
        raise RuntimeError(
            f"Required environment variable "
            f"{name} is not set."
        )

    return value


def log_opportunity(
    opportunity: dict,
    max_rows: int,
):

    STATE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    columns = [
        "id",
        "sport",
        "query_name",
        "score",
        "created_at",
        "age_minutes",
        "username",
        "author_name",
        "followers",
        "likes",
        "replies",
        "reposts",
        "quotes",
        "engagement_velocity",
        "url",
        "text",
    ]

    file_exists = LOG_FILE.exists()

    with LOG_FILE.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=columns,
        )

        if not file_exists:
            writer.writeheader()

        writer.writerow(
            {
                column: opportunity.get(
                    column,
                    "",
                )
                for column in columns
            }
        )

    trim_log(max_rows)


def trim_log(max_rows: int):

    if not LOG_FILE.exists():
        return

    with LOG_FILE.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as f:

        rows = list(
            csv.reader(f)
        )

    if len(rows) <= max_rows + 1:
        return

    header = rows[0]
    data = rows[-max_rows:]

    with LOG_FILE.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:

        writer = csv.writer(f)

        writer.writerow(header)
        writer.writerows(data)


def main():

    print(
        "===================================="
    )

    print(
        "BTB X Growth Radar"
    )

    print(
        "===================================="
    )

    bearer_token = require_env(
        "X_BEARER_TOKEN"
    )

    discord_webhook = require_env(
        "DISCORD_WEBHOOK_URL"
    )

    search_config = load_yaml(
        QUERY_CONFIG
    )

    settings = load_yaml(
        SETTINGS_CONFIG
    )

    x_settings = settings.get(
        "x_api",
        {},
    )

    alert_settings = settings.get(
        "alerts",
        {},
    )

    state_settings = settings.get(
        "state",
        {},
    )

    logging_settings = settings.get(
        "logging",
        {},
    )

    max_results = int(
        x_settings.get(
            "max_results_per_query",
            10,
        )
    )

    lookback_minutes = int(
        x_settings.get(
            "lookback_minutes",
            45,
        )
    )

    timeout_seconds = int(
        x_settings.get(
            "request_timeout_seconds",
            30,
        )
    )

    score_threshold = float(
        alert_settings.get(
            "score_threshold",
            70,
        )
    )

    high_priority_threshold = float(
        alert_settings.get(
            "high_priority_threshold",
            88,
        )
    )

    max_alerts = int(
        alert_settings.get(
            "max_alerts_per_run",
            3,
        )
    )

    keep_days = int(
        state_settings.get(
            "keep_alerted_days",
            7,
        )
    )

    max_log_rows = int(
        logging_settings.get(
            "max_log_rows",
            5000,
        )
    )

    state = AlertState(
        str(ALERTED_STATE),
        keep_days=keep_days,
    )

    query_groups = search_config.get(
        "queries",
        {},
    )

    #
    # ------------------------------------------------
    # SEARCH X
    # ------------------------------------------------
    #

    posts_by_id = {}

    query_count = 0

    for query_name, query_info in query_groups.items():

        if not query_info.get(
            "enabled",
            True,
        ):
            continue

        query = query_info.get(
            "query"
        )

        sport = query_info.get(
            "sport",
            "Unknown",
        )

        if not query:
            continue

        query_count += 1

        print()
        print(
            f"Searching: {query_name}"
        )

        try:

            posts = search_recent_posts(
                bearer_token=bearer_token,
                query=query,
                query_name=query_name,
                sport=sport,
                max_results=max_results,
                lookback_minutes=lookback_minutes,
                timeout_seconds=timeout_seconds,
            )

        except XAPIError as exc:

            print(
                f"X API error:\n{exc}",
                file=sys.stderr,
            )

            continue

        print(
            f"Returned {len(posts)} posts."
        )

        for post in posts:

            post_id = post.get("id")

            if not post_id:
                continue

            #
            # A post may appear in multiple searches.
            # Only score it once per run.
            #
            if post_id not in posts_by_id:
                posts_by_id[post_id] = post

    print()
    print(
        f"Queries executed: {query_count}"
    )

    print(
        f"Unique posts scanned: "
        f"{len(posts_by_id)}"
    )

    #
    # ------------------------------------------------
    # SCORE OPPORTUNITIES
    # ------------------------------------------------
    #

    scored = []

    for post in posts_by_id.values():

        opportunity = score_post(
            post
        )

        if opportunity["score"] >= score_threshold:

            scored.append(
                opportunity
            )

    scored.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    print(
        f"Posts above threshold: "
        f"{len(scored)}"
    )

    #
    # ------------------------------------------------
    # REMOVE PREVIOUSLY ALERTED POSTS
    # ------------------------------------------------
    #

    new_opportunities = [
        item
        for item in scored
        if not state.has_alerted(
            item["id"]
        )
    ]

    print(
        f"New alert candidates: "
        f"{len(new_opportunities)}"
    )

    #
    # ------------------------------------------------
    # LIMIT ALERT VOLUME
    # ------------------------------------------------
    #

    selected = new_opportunities[
        :max_alerts
    ]

    #
    # ------------------------------------------------
    # SEND DISCORD ALERTS
    # ------------------------------------------------
    #

    sent_count = 0

    for opportunity in selected:

        print()
        print(
            "Sending alert:"
        )

        print(
            f"  Score: "
            f"{opportunity['score']}"
        )

        print(
            f"  Account: "
            f"@{opportunity['username']}"
        )

        print(
            f"  URL: "
            f"{opportunity['url']}"
        )

        embed = generate_discord_embed(
            opportunity,
            high_priority_threshold=(
                high_priority_threshold
            ),
        )

        try:

            send_discord_alert(
                webhook_url=discord_webhook,
                embed=embed,
                timeout_seconds=timeout_seconds,
            )

        except Exception as exc:

            print(
                f"Discord error: {exc}",
                file=sys.stderr,
            )

            #
            # Do NOT mark as alerted if
            # Discord delivery failed.
            #
            continue

        state.mark_alerted(
            opportunity["id"]
        )

        log_opportunity(
            opportunity,
            max_rows=max_log_rows,
        )

        sent_count += 1

    state.save()

    print()
    print(
        "===================================="
    )

    print(
        f"Alerts sent: {sent_count}"
    )

    print(
        "Growth Radar run complete."
    )

    print(
        "===================================="
    )


if __name__ == "__main__":

    main()
