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
    load_cfb_crosswalk,
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


# ============================================================
# PATHS
# ============================================================

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


# ============================================================
# HELPERS
# ============================================================

def load_yaml(
    path: Path,
) -> dict:
    """
    Load a YAML configuration file.
    """

    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}"
        )

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
    """
    Require an environment variable.
    """

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
    """
    A post may be returned by multiple X searches.

    Preserve all matching query names and conversation
    hints instead of scoring the same post several times.
    """

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
    ] = sorted(
        item
        for item in hints
        if item
    )

    return existing


def log_opportunity(
    opportunity: dict,
    max_rows: int,
):
    """
    Append an alerted opportunity to the rolling CSV log.
    """

    STATE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    columns = [
        "id",
        "sport",
        "conversation_type",
        "query_name",
        "matched_queries",
        "score",
        "alert_threshold",
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
        "team_match_confidence",
        "team_match_reason",

        "url",
        "text",
    ]

    file_exists = (
        LOG_FILE.exists()
    )

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

        teams = opportunity.get(
            "teams",
            [],
        )

        team_names = "; ".join(
            team.get(
                "team",
                ""
            )
            for team in teams
            if team.get(
                "team"
            )
        )

        if teams:

            primary_team = (
                teams[0]
            )

            confidence = (
                primary_team.get(
                    "match_confidence",
                    "",
                )
            )

            match_reason = (
                primary_team.get(
                    "match_reason",
                    "",
                )
            )

        else:

            confidence = ""
            match_reason = ""

        matched_queries = "; ".join(
            opportunity.get(
                "matched_queries",
                [],
            )
        )

        row = {}

        for column in columns:

            if column == "teams":

                value = (
                    team_names
                )

            elif column == (
                "team_match_confidence"
            ):

                value = confidence

            elif column == (
                "team_match_reason"
            ):

                value = match_reason

            elif column == (
                "matched_queries"
            ):

                value = (
                    matched_queries
                )

            else:

                value = (
                    opportunity.get(
                        column,
                        "",
                    )
                )

            row[column] = value

        writer.writerow(
            row
        )

    trim_log(
        max_rows
    )


def trim_log(
    max_rows: int,
):
    """
    Keep the opportunity CSV from growing indefinitely.
    """

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

    header = (
        rows[0]
    )

    data = (
        rows[-max_rows:]
    )

    with LOG_FILE.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = (
            csv.writer(f)
        )

        writer.writerow(
            header
        )

        writer.writerows(
            data
        )


def choose_hint(
    post: dict,
) -> str:
    """
    If the same X post matched multiple search buckets,
    choose the most useful conversation-type hint.
    """

    hints = post.get(
        "conversation_hints",
        [],
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
        "DEPTH_CHART",
        "GENERAL",
    ]

    for item in priority:

        if item in hints:
            return item

    return (
        hints[0]
    )


def get_threshold(
    conversation_type: str,
    alert_settings: dict,
) -> float:
    """
    Different opportunity types use different Discord
    alert thresholds.
    """

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
    """
    Prevent one team from taking over all alerts in a run.
    """

    selected = []

    team_counts = {}

    for opportunity in opportunities:

        teams = (
            opportunity.get(
                "teams",
                [],
            )
        )

        primary_team = (
            teams[0]["team"]
            if teams
            else None
        )

        if primary_team:

            current = (
                team_counts.get(
                    primary_team,
                    0,
                )
            )

            if (
                current
                >= max_per_team
            ):
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

        if (
            len(selected)
            >= max_alerts
        ):
            break

    return selected


def print_team_matches(
    teams: list[dict],
):
    """
    Helpful GitHub Actions debugging output.
    """

    if not teams:
        print(
            "  Team match: none"
        )
        return

    for team in teams[:3]:

        print(
            "  Team match: "
            f"{team.get('team')} "
            f"| confidence="
            f"{team.get('match_confidence')} "
            f"| reason="
            f"{team.get('match_reason')}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 60
    )

    print(
        "BTB X Growth Radar — Phase 1.5"
    )

    print(
        "=" * 60
    )

    # --------------------------------------------------------
    # SECRETS
    # --------------------------------------------------------

    bearer_token = (
        require_env(
            "X_BEARER_TOKEN"
        )
    )

    discord_webhook = (
        require_env(
            "DISCORD_WEBHOOK_URL"
        )
    )

    # --------------------------------------------------------
    # CONFIG
    # --------------------------------------------------------

    settings = (
        load_yaml(
            CONFIG_DIR
            / "settings.yml"
        )
    )

    configured_queries = (
        load_yaml(
            CONFIG_DIR
            / "search_queries.yml"
        )
    )

    team_config = (
        load_yaml(
            CONFIG_DIR
            / "team_priorities.yml"
        )
    )

    account_config = (
        load_yaml(
            CONFIG_DIR
            / "priority_accounts.yml"
        )
    )

    # --------------------------------------------------------
    # LOAD CFB CROSSWALK
    # --------------------------------------------------------

    data_settings = (
        settings.get(
            "data",
            {},
        )
    )

    crosswalk_settings = (
        data_settings.get(
            "cfb_crosswalk",
            {},
        )
    )

    crosswalk_relative_path = (
        crosswalk_settings.get(
            "path",
            "config/cfb_team_crosswalk.csv",
        )
    )

    cfb_crosswalk_path = (
        ROOT
        / crosswalk_relative_path
    )

    cfb_crosswalk = (
        load_cfb_crosswalk(
            cfb_crosswalk_path
        )
    )

    print(
        f"Loaded "
        f"{len(cfb_crosswalk)} "
        f"CFB teams from crosswalk."
    )

    # --------------------------------------------------------
    # SETTINGS
    # --------------------------------------------------------

    x_settings = (
        settings.get(
            "x_api",
            {},
        )
    )

    alert_settings = (
        settings.get(
            "alerts",
            {},
        )
    )

    state_settings = (
        settings.get(
            "state",
            {},
        )
    )

    logging_settings = (
        settings.get(
            "logging",
            {},
        )
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

    # --------------------------------------------------------
    # STATE
    # --------------------------------------------------------

    state = RadarState(
        str(
            STATE_DIR
        ),
        keep_days=keep_days,
    )

    # ========================================================
    # BUILD SEARCH UNIVERSE
    # ========================================================

    search_jobs = (
        build_all_queries(
            configured_queries=(
                configured_queries
            ),
            team_config=(
                team_config
            ),
            account_config=(
                account_config
            ),
            max_chars=(
                max_query_chars
            ),
        )
    )

    print(
        f"Configured search buckets: "
        f"{len(search_jobs)}"
    )

    # ========================================================
    # SEARCH X
    # ========================================================

    posts_by_id = {}

    searches_run = 0

    searches_skipped = 0

    searches_failed = 0

    raw_results = 0

    for job in search_jobs:

        name = (
            job["name"]
        )

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
            f"Sport: "
            f"{job.get('sport')}"
        )

        print(
            f"Cadence: "
            f"{cadence} min"
        )

        try:

            posts = (
                search_recent_posts(
                    bearer_token=(
                        bearer_token
                    ),
                    query=(
                        job["query"]
                    ),
                    query_name=(
                        name
                    ),
                    sport=(
                        job.get(
                            "sport",
                            "Unknown",
                        )
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
                    lookback_minutes=(
                        int(
                            job.get(
                                "lookback_minutes",
                                40,
                            )
                        )
                    ),
                    timeout_seconds=(
                        timeout_seconds
                    ),
                )
            )

        except XAPIError as exc:

            print(
                str(exc),
                file=sys.stderr,
            )

            searches_failed += 1

            # Do not mark a failed
            # query as successfully run.
            continue

        state.mark_query_run(
            name
        )

        searches_run += 1

        raw_results += (
            len(posts)
        )

        print(
            f"Returned "
            f"{len(posts)} posts."
        )

        for post in posts:

            post_id = (
                post.get(
                    "id"
                )
            )

            if not post_id:
                continue

            if (
                post_id
                in posts_by_id
            ):

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
        f"Searches failed: "
        f"{searches_failed}"
    )

    print(
        f"Raw X results: "
        f"{raw_results}"
    )

    print(
        f"Unique posts: "
        f"{len(posts_by_id)}"
    )

    # ========================================================
    # IDENTIFY TEAMS / CLASSIFY / SCORE
    # ========================================================

    opportunities = []

    rejected_below_threshold = 0

    posts_with_team_match = 0

    posts_without_team_match = 0

    for post in (
        posts_by_id.values()
    ):

        # ----------------------------------------------------
        # TEAM MATCHING
        #
        # CFB now uses:
        #
        # btb_team
        # btb_team_short
        # mascot
        # football context
        #
        # from cfb_team_crosswalk.csv.
        # ----------------------------------------------------

        teams = (
            identify_teams(
                text=(
                    post.get(
                        "text",
                        "",
                    )
                ),
                team_config=(
                    team_config
                ),
                cfb_crosswalk=(
                    cfb_crosswalk
                ),
                sport_hint=(
                    post.get(
                        "sport"
                    )
                ),
            )
        )

        if teams:

            posts_with_team_match += 1

        else:

            posts_without_team_match += 1

        # ----------------------------------------------------
        # CONVERSATION TYPE
        # ----------------------------------------------------

        hint = (
            choose_hint(
                post
            )
        )

        conversation_type = (
            classify_conversation(
                post,
                hint=hint,
            )
        )

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        opportunity = (
            score_post(
                post=post,
                teams=teams,
                conversation_type=(
                    conversation_type
                ),
                priority_accounts=(
                    account_config
                ),
            )
        )

        threshold = (
            get_threshold(
                conversation_type=(
                    conversation_type
                ),
                alert_settings=(
                    alert_settings
                ),
            )
        )

        opportunity[
            "alert_threshold"
        ] = threshold

        # Helpful terminal logging for posts
        # that are close to or above threshold.
        if (
            opportunity["score"]
            >= threshold - 5
        ):

            print()

            print(
                "Candidate:"
            )

            print(
                f"  Score: "
                f"{opportunity['score']}"
            )

            print(
                f"  Threshold: "
                f"{threshold}"
            )

            print(
                f"  Type: "
                f"{conversation_type}"
            )

            print(
                f"  Account: "
                f"@{post.get('username')}"
            )

            print_team_matches(
                teams
            )

        if (
            opportunity["score"]
            >= threshold
        ):

            opportunities.append(
                opportunity
            )

        else:

            rejected_below_threshold += 1

    # Highest-quality opportunity first.
    opportunities.sort(
        key=lambda item: (
            item.get(
                "score",
                0,
            ),
            item.get(
                "engagement_velocity",
                0,
            ),
        ),
        reverse=True,
    )

    print()

    print(
        f"Posts with identified team: "
        f"{posts_with_team_match}"
    )

    print(
        f"Posts without identified team: "
        f"{posts_without_team_match}"
    )

    print(
        f"Posts below threshold: "
        f"{rejected_below_threshold}"
    )

    print(
        f"Posts above contextual threshold: "
        f"{len(opportunities)}"
    )

    # ========================================================
    # REMOVE PREVIOUSLY ALERTED POSTS
    # ========================================================

    new_opportunities = [
        opportunity

        for opportunity
        in opportunities

        if not state.has_alerted(
            opportunity[
                "id"
            ]
        )
    ]

    print(
        f"New alert candidates: "
        f"{len(new_opportunities)}"
    )

    # ========================================================
    # CONTROL ALERT VOLUME
    # ========================================================

    selected = (
        select_alerts(
            opportunities=(
                new_opportunities
            ),
            max_alerts=(
                max_alerts
            ),
            max_per_team=(
                max_per_team
            ),
        )
    )

    print(
        f"Selected for Discord: "
        f"{len(selected)}"
    )

    # ========================================================
    # SEND DISCORD ALERTS
    # ========================================================

    sent_count = 0

    for opportunity in (
        selected
    ):

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

            primary = (
                opportunity[
                    "teams"
                ][0]
            )

            print(
                f"  Team: "
                f"{primary.get('team')}"
            )

            print(
                f"  Match confidence: "
                f"{primary.get('match_confidence')}"
            )

            print(
                f"  Match reason: "
                f"{primary.get('match_reason')}"
            )

        else:

            print(
                "  Team: none"
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
                embed=(
                    embed
                ),
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

            # Do not mark the post alerted
            # if Discord failed.
            continue

        state.mark_alerted(
            opportunity[
                "id"
            ]
        )

        log_opportunity(
            opportunity=(
                opportunity
            ),
            max_rows=(
                max_log_rows
            ),
        )

        sent_count += 1

    # ========================================================
    # SAVE STATE
    # ========================================================

    state.save()

    # ========================================================
    # SUMMARY
    # ========================================================

    print()

    print(
        "=" * 60
    )

    print(
        "RUN SUMMARY"
    )

    print(
        "-" * 60
    )

    print(
        f"Searches run: "
        f"{searches_run}"
    )

    print(
        f"Raw results: "
        f"{raw_results}"
    )

    print(
        f"Unique posts: "
        f"{len(posts_by_id)}"
    )

    print(
        f"Team-matched posts: "
        f"{posts_with_team_match}"
    )

    print(
        f"Above threshold: "
        f"{len(opportunities)}"
    )

    print(
        f"Alerts sent: "
        f"{sent_count}"
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":

    main()
