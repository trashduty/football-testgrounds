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

from x_growth.query_builder import (
    build_all_queries,
)

from x_growth.team_matcher import (
    identify_teams,
)

from x_growth.classify_conversation import (
    classify_conversation,
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

from x_growth.state import (
    RadarState,
)


ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

CONFIG_DIR = (
    ROOT
    / "config"
)

STATE_DIR = (
    ROOT
    / "x_growth"
    / "state"
)

LOG_FILE = (
    STATE_DIR
    / "opportunities.csv"
)


def load_yaml(
    path: Path,
) -> dict:

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:

        return (
            yaml.safe_load(f)
            or {}
        )


def require_env(
    name: str,
) -> str:

    value = os.environ.get(
        name
    )

    if not value:

        raise RuntimeError(
            f"Required environment variable "
            f"{name} is not set."
        )

    return value


def merge_duplicate_post(
    existing: dict,
    new_post: dict,
) -> dict:

    #
    # One post may be returned from several query
    # buckets. Preserve all matched sources.
    #

    query_names = set(
        existing.get(
            "matched_queries",
            [
                existing.get(
                    "query_name",
                    "",
                )
            ],
        )
    )

    query_names.add(
        new_post.get(
            "query_name",
            "",
        )
    )

    existing[
        "matched_queries"
    ] = sorted(
        item
        for item in query_names
        if item
    )

    hints = set(
        existing.get(
            "conversation_hints",
            [
                existing.get(
                    "conversation_type_hint",
                    "GENERAL",
                )
            ],
        )
    )

    hints.add(
        new_post.get(
            "conversation_type_hint",
            "GENERAL",
        )
    )

    existing[
        "conversation_hints"
    ] = list(hints)

    return existing


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
        "conversation_type",
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

        "freshness_score",
        "reach_score",
        "velocity_score",
        "relevance_score",
        "team_priority_score",
        "source_priority_score",

        "teams",
        "url",
        "text",
    ]

    exists = LOG_FILE.exists()

    with LOG_FILE.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=columns,
        )

        if not exists:
            writer.writeheader()

        row = {}

        for column in columns:

            value = opportunity.get(
                column,
                "",
            )

            if column == "teams":

                value = "; ".join(
                    team.get(
                        "team",
                        ""
                    )
                    for team
                    in opportunity.get(
                        "teams",
                        [],
                    )
                )

            row[column] = value

        writer.writerow(row)

    trim_log(
        max_rows
    )


def trim_log(
    max_rows: int,
):

    if not LOG_FILE.exists():
        return

    with LOG_FILE.open(
        "r",
        newline="",
        encoding="utf-8",
    ) as f:

        rows = list(
            csv.reader(f)
        )

    if (
        len(rows)
        <= max_rows + 1
    ):
        return

    header = rows[0]

    data = rows[
        -max_rows:
    ]

    with LOG_FILE.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.writer(f)

        writer.writerow(header)

        writer.writerows(data)


def choose_hint(
    post: dict,
) -> str:

    hints = post.get(
        "conversation_hints",
        []
    )

    if not hints:

        return post.get(
            "conversation_type_hint",
            "GENERAL",
        )

    priority = [

        "TEAM_VIDEO",
        "INJURY",
        "STAR_PLAYER_NEWS",
        "NFL_CAMP",
        "TEAM_HYPE",
        "FUTURES",
        "RANKINGS",
        "POWER_RATINGS",
        "MATCHUP",
        "GENERAL",
    ]

    for item in priority:

        if item in hints:
            return item

    return hints[0]


def get_threshold(
    conversation_type: str,
    alert_settings: dict,
) -> float:

    thresholds = (
        alert_settings.get(
            "thresholds",
            {},
        )
    )

    return float(
        thresholds.get(
            conversation_type,
            thresholds.get(
                "GENERAL",
                72,
            ),
        )
    )


def select_alerts(
    opportunities: list[dict],
    max_alerts: int,
    max_per_team: int,
) -> list[dict]:

    selected = []

    team_counts = {}

    for opportunity in opportunities:

        teams = opportunity.get(
            "teams",
            [],
        )

        primary_team = (
            teams[0]["team"]
            if teams
            else None
        )

        if primary_team:

            current = team_counts.get(
                primary_team,
                0,
            )

            if current >= max_per_team:
                continue

        selected.append(
            opportunity
        )

        if primary_team:

            team_counts[
                primary_team
            ] = (
                team_counts.get(
                    primary_team,
                    0,
                )
                + 1
            )

        if len(selected) >= max_alerts:
            break

    return selected


def main():

    print(
        "=" * 55
    )

    print(
        "BTB X Growth Radar — Phase 1.5"
    )

    print(
        "=" * 55
    )

    bearer_token = require_env(
        "X_BEARER_TOKEN"
    )

    discord_webhook = require_env(
        "DISCORD_WEBHOOK_URL"
    )

    settings = load_yaml(
        CONFIG_DIR
        / "settings.yml"
    )

    configured_queries = load_yaml(
        CONFIG_DIR
        / "search_queries.yml"
    )

    team_config = load_yaml(
        CONFIG_DIR
        / "team_priorities.yml"
    )

    account_config = load_yaml(
        CONFIG_DIR
        / "priority_accounts.yml"
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

    timeout_seconds = int(
        x_settings.get(
            "request_timeout_seconds",
            30,
        )
    )

    max_query_chars = int(
        x_settings.get(
            "max_query_characters",
            475,
        )
    )

    max_alerts = int(
        alert_settings.get(
            "max_alerts_per_run",
            4,
        )
    )

    max_per_team = int(
        alert_settings.get(
            "max_alerts_per_team_per_run",
            2,
        )
    )

    high_priority_threshold = float(
        alert_settings.get(
            "high_priority_threshold",
            86,
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
            10000,
        )
    )

    state = RadarState(
        str(STATE_DIR),
        keep_days=keep_days,
    )

    #
    # -----------------------------------------------
    # BUILD SEARCH UNIVERSE
    # -----------------------------------------------
    #

    search_jobs = build_all_queries(

        configured_queries,

        team_config,

        account_config,

        max_query_chars,
    )

    print(
        f"Configured search buckets: "
        f"{len(search_jobs)}"
    )

    #
    # -----------------------------------------------
    # SEARCH X
    # -----------------------------------------------
    #

    posts_by_id = {}

    searches_run = 0

    searches_skipped = 0

    raw_results = 0

    for job in search_jobs:

        name = job["name"]

        cadence = int(
            job.get(
                "cadence_minutes",
                20,
            )
        )

        if not state.should_run_query(
            name,
            cadence,
        ):

            searches_skipped += 1
            continue

        print()

        print(
            f"Searching: {name}"
        )

        print(
            f"Type: "
            f"{job.get('conversation_type')}"
        )

        print(
            f"Cadence: "
            f"{cadence} min"
        )

        try:

            posts = search_recent_posts(

                bearer_token=(
                    bearer_token
                ),

                query=job["query"],

                query_name=name,

                sport=job.get(
                    "sport",
                    "Unknown",
                ),

                conversation_type_hint=(
                    job.get(
                        "conversation_type",
                        "GENERAL",
                    )
                ),

                max_results=(
                    max_results
                ),

                lookback_minutes=int(
                    job.get(
                        "lookback_minutes",
                        40,
                    )
                ),

                timeout_seconds=(
                    timeout_seconds
                ),
            )

        except XAPIError as exc:

            print(
                str(exc),
                file=sys.stderr,
            )

            #
            # Don't mark failed searches as run.
            #
            continue

        state.mark_query_run(
            name
        )

        searches_run += 1

        raw_results += len(
            posts
        )

        print(
            f"Returned "
            f"{len(posts)} posts."
        )

        for post in posts:

            post_id = post.get(
                "id"
            )

            if not post_id:
                continue

            if post_id in posts_by_id:

                posts_by_id[
                    post_id
                ] = (
                    merge_duplicate_post(
                        posts_by_id[
                            post_id
                        ],
                        post,
                    )
                )

            else:

                post[
                    "matched_queries"
                ] = [
                    post.get(
                        "query_name"
                    )
                ]

                post[
                    "conversation_hints"
                ] = [
                    post.get(
                        "conversation_type_hint",
                        "GENERAL",
                    )
                ]

                posts_by_id[
                    post_id
                ] = post

    print()

    print(
        f"Searches executed: "
        f"{searches_run}"
    )

    print(
        f"Searches skipped by cadence: "
        f"{searches_skipped}"
    )

    print(
        f"Raw X results: "
        f"{raw_results}"
    )

    print(
        f"Unique posts: "
        f"{len(posts_by_id)}"
    )

    #
    # -----------------------------------------------
    # IDENTIFY / CLASSIFY / SCORE
    # -----------------------------------------------
    #

    opportunities = []

    for post in posts_by_id.values():

        teams = identify_teams(

            post.get(
                "text",
                "",
            ),

            team_config,

            sport_hint=post.get(
                "sport"
            ),
        )

        hint = choose_hint(
            post
        )

        conversation_type = (
            classify_conversation(
                post,
                hint=hint,
            )
        )

        opportunity = score_post(

            post,

            teams,

            conversation_type,

            account_config,
        )

        threshold = get_threshold(

            conversation_type,

            alert_settings,
        )

        opportunity[
            "alert_threshold"
        ] = threshold

        if (
            opportunity["score"]
            >= threshold
        ):

            opportunities.append(
                opportunity
            )

    #
    # Highest opportunity first.
    #
    opportunities.sort(

        key=lambda item: (

            item["score"],

            item.get(
                "engagement_velocity",
                0,
            ),

        ),

        reverse=True,
    )

    print(
        f"Posts above contextual threshold: "
        f"{len(opportunities)}"
    )

    #
    # -----------------------------------------------
    # REMOVE ALREADY ALERTED
    # -----------------------------------------------
    #

    new_opportunities = [

        opportunity

        for opportunity
        in opportunities

        if not state.has_alerted(
            opportunity["id"]
        )
    ]

    print(
        f"New candidates: "
        f"{len(new_opportunities)}"
    )

    #
    # -----------------------------------------------
    # CONTROL ALERT VOLUME
    # -----------------------------------------------
    #

    selected = select_alerts(

        new_opportunities,

        max_alerts=max_alerts,

        max_per_team=max_per_team,
    )

    #
    # -----------------------------------------------
    # DISCORD
    # -----------------------------------------------
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
            f"  Type: "
            f"{opportunity['conversation_type']}"
        )

        if opportunity.get(
            "teams"
        ):

            print(
                "  Team: "
                + ", ".join(
                    item["team"]
                    for item
                    in opportunity[
                        "teams"
                    ]
                )
            )

        print(
            f"  Account: "
            f"@{opportunity['username']}"
        )

        print(
            f"  URL: "
            f"{opportunity['url']}"
        )

        embed = (
            generate_discord_embed(

                opportunity,

                high_priority_threshold=(
                    high_priority_threshold
                ),
            )
        )

        try:

            send_discord_alert(

                webhook_url=(
                    discord_webhook
                ),

                embed=embed,

                timeout_seconds=(
                    timeout_seconds
                ),
            )

        except Exception as exc:

            print(
                (
                    "Discord error: "
                    f"{exc}"
                ),
                file=sys.stderr,
            )

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
        "=" * 55
    )

    print(
        f"Alerts sent: "
        f"{sent_count}"
    )

    print(
        "Growth Radar run complete."
    )

    print(
        "=" * 55
    )


if __name__ == "__main__":

    main()
