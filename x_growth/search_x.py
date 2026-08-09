from __future__ import annotations

import requests
from datetime import datetime, timedelta, timezone
from typing import Any


X_RECENT_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"


class XAPIError(RuntimeError):
    """Raised when the X API request fails."""


def _utc_iso(dt: datetime) -> str:
    """
    Convert a datetime to the ISO-8601 format expected by X.
    """
    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def search_recent_posts(
    bearer_token: str,
    query: str,
    query_name: str,
    sport: str,
    max_results: int = 10,
    lookback_minutes: int = 45,
    timeout_seconds: int = 30,
) -> list[dict[str, Any]]:
    """
    Search X Recent Search and return normalized posts.

    Uses app-only Bearer authentication.
    """

    if not bearer_token:
        raise ValueError("X bearer token is missing.")

    if max_results < 10:
        max_results = 10

    if max_results > 100:
        max_results = 100

    now = datetime.now(timezone.utc)
    start_time = now - timedelta(minutes=lookback_minutes)

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "User-Agent": "BTB-X-Growth-Radar/1.0",
    }

    params = {
        "query": " ".join(query.split()),
        "max_results": max_results,
        "start_time": _utc_iso(start_time),
        "sort_order": "recency",

        # Post fields
        "tweet.fields": ",".join(
            [
                "created_at",
                "public_metrics",
                "lang",
                "conversation_id",
            ]
        ),

        # Return author information in the same response
        "expansions": "author_id",

        # Author fields
        "user.fields": ",".join(
            [
                "username",
                "name",
                "verified",
                "verified_type",
                "public_metrics",
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
        message = response.text[:1500]

        raise XAPIError(
            f"X API request failed.\n"
            f"Query: {query_name}\n"
            f"HTTP status: {response.status_code}\n"
            f"Response: {message}"
        )

    payload = response.json()

    posts = payload.get("data", [])
    includes = payload.get("includes", {})
    users = includes.get("users", [])

    user_lookup = {
        str(user["id"]): user
        for user in users
        if user.get("id")
    }

    normalized_posts = []

    for post in posts:

        post_id = str(post.get("id", ""))
        author_id = str(post.get("author_id", ""))

        author = user_lookup.get(author_id, {})

        post_metrics = post.get("public_metrics") or {}
        user_metrics = author.get("public_metrics") or {}

        username = author.get("username") or "unknown"

        normalized = {
            "id": post_id,
            "query_name": query_name,
            "sport": sport,

            "text": post.get("text", ""),
            "created_at": post.get("created_at"),

            "author_id": author_id,
            "author_name": author.get("name", username),
            "username": username,

            "verified": bool(author.get("verified", False)),
            "verified_type": author.get("verified_type"),

            "followers": int(
                user_metrics.get("followers_count", 0) or 0
            ),

            "following": int(
                user_metrics.get("following_count", 0) or 0
            ),

            "author_post_count": int(
                user_metrics.get("post_count", 0) or 0
            ),

            "likes": int(
                post_metrics.get("like_count", 0) or 0
            ),

            "replies": int(
                post_metrics.get("reply_count", 0) or 0
            ),

            "reposts": int(
                post_metrics.get("repost_count", 0) or 0
            ),

            "quotes": int(
                post_metrics.get("quote_count", 0) or 0
            ),

            "bookmarks": int(
                post_metrics.get("bookmark_count", 0) or 0
            ),

            "impressions": int(
                post_metrics.get("impression_count", 0) or 0
            ),

            "conversation_id": post.get("conversation_id"),

            "url": (
                f"https://x.com/{username}/status/{post_id}"
                if username != "unknown" and post_id
                else f"https://x.com/i/web/status/{post_id}"
            ),
        }

        normalized_posts.append(normalized)

    return normalized_posts
