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

from x_growth.retrieve_btb_context import (
    retrieve_btb_context,
)

from x_growth.generate_btb_reply import (
    generate_btb_reply,
    BTBReplyGenerationError,
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
# CONFIG HELPERS
# ============================================================

def load_yaml(
    path: Path,
) -> dict:

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

    value = (
        os.environ.get(
            name
        )
    )

    if not value:

        raise RuntimeError(
            f"Required environment variable "
            f"{name} is not set."
        )

    return value


# ============================================================
# DUPLICATE SEARCH RESULTS
# ============================================================

def merge_duplicate_post(
    existing: dict,
    new_post: dict,
) -> dict:

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
        for item
        in query_names
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
        for item
        in hints
        if item
    )

    return existing


# ============================================================
# CONVERSATION HINT
# ============================================================

def choose_hint(
    post: dict,
) -> str:

    hints = (
        post.get(
            "conversation_hints",
            [],
        )
    )

    if not hints:

        return (
            post.get(
                "conversation_type_hint",
                "GENERAL",
            )
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


# ============================================================
# ALERT THRESHOLD
# ============================================================

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
                85,
            ),
        )
    )


# ============================================================
# ALERT SELECTION
# ============================================================

def select_alerts(
    opportunities: list[dict],
    max_alerts: int,
    max_per_team: int,
) -> list[dict]:

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

            count = (
                team_counts.get(
                    primary_team,
                    0,
                )
            )

            if (
                count
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

        if len(
            selected
        ) >= max_alerts:

            break

    return selected


# ============================================================
# LOGGING
# ============================================================

def log_opportunity(
    opportunity: dict,
    btb_context: dict,
    generated_reply: dict,
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
        "score",
        "alert_threshold",

        "created_at",
        "age_minutes",

        "username",
        "followers",

        "likes",
        "replies",
        "reposts",
        "quotes",

        "engagement_velocity",

        "team",

        "team_match_confidence",

        "btb_context_sources",

        "reply_angle",

        "preferred_reply",

        "alternative_reply",

        "url",

        "text",
    ]

    exists = (
        LOG_FILE.exists()
    )

    teams = (
        opportunity.get(
            "teams",
            [],
        )
    )

    primary = (
        teams[0]
        if teams
        else {}
    )

    context_sources = (
        ", ".join(
            btb_context.get(
                "sources",
                {}
            ).keys()
        )
    )

    row = {

        "id":
            opportunity.get(
                "id"
            ),

        "sport":
            opportunity.get(
                "sport"
            ),

        "conversation_type":
            opportunity.get(
                "conversation_type"
            ),

        "score":
            opportunity.get(
                "score"
            ),

        "alert_threshold":
            opportunity.get(
                "alert_threshold"
            ),

        "created_at":
            opportunity.get(
                "created_at"
            ),

        "age_minutes":
            opportunity.get(
                "age_minutes"
            ),

        "username":
            opportunity.get(
                "username"
            ),

        "followers":
            opportunity.get(
                "followers"
            ),

        "likes":
            opportunity.get(
                "likes"
            ),

        "replies":
            opportunity.get(
                "replies"
            ),

        "reposts":
            opportunity.get(
                "reposts"
            ),

        "quotes":
            opportunity.get(
                "quotes"
            ),

        "engagement_velocity":
            opportunity.get(
                "engagement_velocity"
            ),

        "team":
            primary.get(
                "team"
            ),

        "team_match_confidence":
            primary.get(
                "match_confidence"
            ),

        "btb_context_sources":
            context_sources,

        "reply_angle":
            generated_reply.get(
                "angle"
            ),

        "preferred_reply":
            generated_reply.get(
                "preferred_reply"
            ),

        "alternative_reply":
            generated_reply.get(
                "alternative_reply"
            ),

        "url":
            opportunity.get(
                "url"
            ),

        "text":
            opportunity.get(
                "text"
            ),
    }

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

        writer.writerow(
            row
        )

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

    header = (
        rows[0]
    )

    data = (
        rows[
            -max_rows:
        ]
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


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 65
    )

    print(
        "BTB X Growth Radar — Phase 2"
    )

    print(
        "=" * 65
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

    openai_api_key = (
        require_env(
            "OPENAI_API_KEY"
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

    search_config = (
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

    data_source_config = (
        load_yaml(
            CONFIG_DIR
            / "data_sources.yml"
        )
    )


    # --------------------------------------------------------
    # CFB CROSSWALK
    # --------------------------------------------------------

    crosswalk_path = (

        ROOT

        / settings.get(
            "data",
            {}
        ).get(
            "cfb_crosswalk",
            {}
        ).get(
            "path",
            "config/cfb_team_crosswalk.csv",
        )
    )

    cfb_crosswalk = (
        load_cfb_crosswalk(
            crosswalk_path
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

    reply_settings = (
        settings.get(
            "reply_generation",
            {},
        )
    )

    openai_settings = (
        settings.get(
            "openai",
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
        settings.get(
            "state",
            {}
        ).get(
            "keep_alerted_days",
            7,
        )
    )

    max_log_rows = int(
        settings.get(
            "logging",
            {}
        ).get(
            "max_log_rows",
            10000,
        )
    )

    model = str(
        openai_settings.get(
            "model",
            "gpt-5-mini",
        )
    )

    openai_timeout = int(
        openai_settings.get(
            "timeout_seconds",
            45,
        )
    )

    require_useful_reply = bool(
        openai_settings.get(
            "require_useful_reply",
            True,
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
    # BUILD SEARCH JOBS
    # ========================================================

    search_jobs = (
        build_all_queries(

            configured_queries=(
                search_config
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
            )

        except XAPIError as exc:

            print(
                str(exc),
                file=sys.stderr,
            )

            continue


        state.mark_query_run(
            name
        )

        searches_run += 1

        raw_results += (
            len(posts)
        )


        for post in posts:

            post_id = (
                post.get(
                    "id"
                )
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
        f"Searches run: {searches_run}"
    )

    print(
        f"Searches skipped: {searches_skipped}"
    )

    print(
        f"Raw results: {raw_results}"
    )

    print(
        f"Unique posts: "
        f"{len(posts_by_id)}"
    )


    # ========================================================
    # IDENTIFY / CLASSIFY / SCORE
    # ========================================================

    opportunities = []


    for post in (
        posts_by_id.values()
    ):

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
                conversation_type,
                alert_settings,
            )
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


    # ========================================================
    # PREVIOUSLY ALERTED
    # ========================================================

    new_opportunities = [

        opportunity

        for opportunity
        in opportunities

        if not state.has_alerted(
            opportunity["id"]
        )
    ]


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
        f"Selected candidates: "
        f"{len(selected)}"
    )


    # ========================================================
    # PHASE 2:
    # RETRIEVE BTB DATA → GENERATE RESPONSE → DISCORD
    # ========================================================

    sent_count = 0
    skipped_no_team = 0
    skipped_no_context = 0
    skipped_no_reply = 0


    for opportunity in selected:

        teams = (
            opportunity.get(
                "teams",
                [],
            )
        )


        # ----------------------------------------------------
        # Require a detected team for Phase 2 generation.
        # ----------------------------------------------------

        if not teams:

            print(
                "Skipping candidate: "
                "no reliable team match."
            )

            skipped_no_team += 1

            continue


        primary_team = (
            teams[0]
        )


        print()

        print(
            "Phase 2 candidate:"
        )

        print(
            f"  Team: "
            f"{primary_team.get('team')}"
        )

        print(
            f"  Type: "
            f"{opportunity.get('conversation_type')}"
        )

        print(
            f"  Score: "
            f"{opportunity.get('score')}"
        )


        # ----------------------------------------------------
        # RETRIEVE BTB DATA
        # ----------------------------------------------------

        btb_context = (
            retrieve_btb_context(

                root=ROOT,

                team=(
                    primary_team
                ),

                conversation_type=(
                    opportunity.get(
                        "conversation_type",
                        "GENERAL",
                    )
                ),

                source_config=(
                    data_source_config
                ),

                reply_settings=(
                    reply_settings
                ),
            )
        )


        sources_found = list(
            btb_context.get(
                "sources",
                {}
            ).keys()
        )


        print(
            f"  BTB sources: "
            f"{sources_found}"
        )


        if not btb_context.get(
            "has_btb_context"
        ):

            print(
                "  No BTB context found. "
                "Skipping."
            )

            skipped_no_context += 1

            continue


        # ----------------------------------------------------
        # GENERATE BTB RESPONSE
        # ----------------------------------------------------

        try:

            generated_reply = (
                generate_btb_reply(

                    api_key=(
                        openai_api_key
                    ),

                    model=(
                        model
                    ),

                    post=(
                        opportunity
                    ),

                    btb_context=(
                        btb_context
                    ),

                    timeout_seconds=(
                        openai_timeout
                    ),

                    preferred_max_characters=int(
                        reply_settings.get(
                            "preferred_max_characters",
                            500,
                        )
                    ),

                    alternative_max_characters=int(
                        reply_settings.get(
                            "alternative_max_characters",
                            500,
                        )
                    ),
                )
            )

        except (
            BTBReplyGenerationError,
            Exception,
        ) as exc:

            print(
                f"  Reply generation failed: "
                f"{exc}",
                file=sys.stderr,
            )

            continue


        useful_reply = bool(
            generated_reply.get(
                "useful_reply"
            )
        )


        if (
            require_useful_reply
            and not useful_reply
        ):

            print(
                "  Model determined BTB "
                "does not have a useful reply."
            )

            print(
                "  Reason: "
                f"{generated_reply.get('reason_if_skipped', '')}"
            )

            skipped_no_reply += 1

            continue


        # ----------------------------------------------------
        # DISCORD
        # ----------------------------------------------------

        embed = (
            generate_discord_embed(

                opportunity=(
                    opportunity
                ),

                btb_context=(
                    btb_context
                ),

                generated_reply=(
                    generated_reply
                ),

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
                f"Discord error: {exc}",
                file=sys.stderr,
            )

            continue


        state.mark_alerted(
            opportunity["id"]
        )


        log_opportunity(

            opportunity=(
                opportunity
            ),

            btb_context=(
                btb_context
            ),

            generated_reply=(
                generated_reply
            ),

            max_rows=(
                max_log_rows
            ),
        )


        sent_count += 1


    state.save()


    # ========================================================
    # SUMMARY
    # ========================================================

    print()

    print(
        "=" * 65
    )

    print(
        "PHASE 2 RUN SUMMARY"
    )

    print(
        "-" * 65
    )

    print(
        f"Searches run: "
        f"{searches_run}"
    )

    print(
        f"Unique posts: "
        f"{len(posts_by_id)}"
    )

    print(
        f"Above threshold: "
        f"{len(opportunities)}"
    )

    print(
        f"Selected: "
        f"{len(selected)}"
    )

    print(
        f"Skipped — no team: "
        f"{skipped_no_team}"
    )

    print(
        f"Skipped — no BTB data: "
        f"{skipped_no_context}"
    )

    print(
        f"Skipped — weak BTB response: "
        f"{skipped_no_reply}"
    )

    print(
        f"Discord alerts sent: "
        f"{sent_count}"
    )

    print(
        "=" * 65
    )


if __name__ == "__main__":

    main()
