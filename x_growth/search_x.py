from __future__ import annotations

import requests

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from typing import Any


X_RECENT_SEARCH_URL = (
    "https://api.x.com/2/tweets/search/recent"
)


class XAPIError(RuntimeError):
    pass


def _utc_iso(
    dt: datetime,
) -> str:

    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


def search_recent_posts(
    bearer_token: str,
    query: str,
    query_name: str,
    sport: str,
    conversation_type_hint: str,
    max_results: int = 10,
    lookback_minutes: int = 40,
    timeout_seconds: int = 30,
) -> list[dict[str, Any]]:

    if not bearer_token:
        raise ValueError(
            "X bearer token is missing."
        )

    max_results = max(
        10,
        min(
            int(max_results),
            100,
        ),
    )

    now = datetime.now(
        timezone.utc
    )

    start_time = (
        now
        - timedelta(
            minutes=lookback_minutes
        )
    )

    headers = {
        "Authorization": (
            f"Bearer {bearer_token}"
        ),
        "User-Agent": (
            "BTB-X-Growth-Radar/1.5"
        ),
    }

    params = {

        "query": " ".join(
            query.split()
        ),

        "max_results": max_results,

        "start_time": _utc_iso(
            start_time
        ),

        "sort_order": "recency",

        "tweet.fields": ",".join(
            [
                "created_at",
                "public_metrics",
                "lang",
                "conversation_id",
                "attachments",
            ]
        ),

        "expansions": ",".join(
            [
                "author_id",
                "attachments.media_keys",
            ]
        ),

        "user.fields": ",".join(
            [
                "username",
                "name",
                "verified",
                "verified_type",
                "public_metrics",
            ]
        ),

        "media.fields": ",".join(
            [
                "media_key",
                "type",
                "url",
                "preview_image_url",
            ]
        ),
    }

    response = requests.get(
        X_RECENT_SEARCH_URL,
        headers=headers,
        params=params,
        timeout=timeout_seconds,
    )

    if response.status_code != 200:

        raise XAPIError(
            "\n".join(
                [
                    "X API request failed.",
                    f"Query: {query_name}",
                    (
                        "HTTP status: "
                        f"{response.status_code}"
                    ),
                    (
                        "Response: "
                        f"{response.text[:2000]}"
                    ),
                ]
            )
        )

    payload = response.json()

    posts = payload.get(
        "data",
        [],
    )

    includes = payload.get(
        "includes",
        {},
    )

    users = includes.get(
        "users",
        [],
    )

    media = includes.get(
        "media",
        [],
    )

    user_lookup = {
        str(user["id"]): user
        for user in users
        if user.get("id")
    }

    media_lookup = {
        item.get("media_key"): item
        for item in media
        if item.get("media_key")
    }

    normalized_posts = []

    for post in posts:

        post_id = str(
            post.get(
                "id",
                "",
            )
        )

        author_id = str(
            post.get(
                "author_id",
                "",
            )
        )

        author = user_lookup.get(
            author_id,
            {},
        )

        post_metrics = (
            post.get(
                "public_metrics"
            )
            or {}
        )

        user_metrics = (
            author.get(
                "public_metrics"
            )
            or {}
        )

        username = (
            author.get(
                "username"
            )
            or "unknown"
        )

        attachments = (
            post.get(
                "attachments"
            )
            or {}
        )

        media_keys = attachments.get(
            "media_keys",
            [],
        )

        attached_media = [
            media_lookup[key]
            for key in media_keys
            if key in media_lookup
        ]

        media_types = [
            item.get("type")
            for item in attached_media
            if item.get("type")
        ]

        normalized_posts.append(
            {
                "id": post_id,

                "query_name": query_name,

                "sport": sport,

                "conversation_type_hint": (
                    conversation_type_hint
                ),

                "text": post.get(
                    "text",
                    "",
                ),

                "created_at": post.get(
                    "created_at"
                ),

                "author_id": author_id,

                "author_name": (
                    author.get(
                        "name",
                        username,
                    )
                ),

                "username": username,

                "verified": bool(
                    author.get(
                        "verified",
                        False,
                    )
                ),

                "verified_type": (
                    author.get(
                        "verified_type"
                    )
                ),

                "followers": int(
                    user_metrics.get(
                        "followers_count",
                        0,
                    )
                    or 0
                ),

                "following": int(
                    user_metrics.get(
                        "following_count",
                        0,
                    )
                    or 0
                ),

                "likes": int(
                    post_metrics.get(
                        "like_count",
                        0,
                    )
                    or 0
                ),

                "replies": int(
                    post_metrics.get(
                        "reply_count",
                        0,
                    )
                    or 0
                ),

                "reposts": int(
                    post_metrics.get(
                        "repost_count",
                        0,
                    )
                    or 0
                ),

                "quotes": int(
                    post_metrics.get(
                        "quote_count",
                        0,
                    )
                    or 0
                ),

                "bookmarks": int(
                    post_metrics.get(
                        "bookmark_count",
                        0,
                    )
                    or 0
                ),

                "impressions": int(
                    post_metrics.get(
                        "impression_count",
                        0,
                    )
                    or 0
                ),

                "conversation_id": (
                    post.get(
                        "conversation_id"
                    )
                ),

                "media_types": (
                    media_types
                ),

                "has_video": (
                    "video"
                    in media_types
                ),

                "has_image": (
                    "photo"
                    in media_types
                ),

                "url": (
                    "https://x.com/"
                    f"{username}/status/"
                    f"{post_id}"
                    if (
                        username
                        != "unknown"
                        and post_id
                    )
                    else (
                        "https://x.com/i/web/"
                        f"status/{post_id}"
                    )
                ),
            }
        )

    return normalized_posts
