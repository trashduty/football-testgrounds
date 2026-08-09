from __future__ import annotations

import requests


class DiscordWebhookError(RuntimeError):
    pass


def send_discord_alert(
    webhook_url: str,
    embed: dict,
    timeout_seconds: int = 30,
):

    if not webhook_url:
        raise ValueError(
            "Discord webhook URL is missing."
        )

    payload = {
        "username": "BTB X Growth Radar",

        "allowed_mentions": {
            "parse": []
        },

        "embeds": [
            embed
        ],
    }

    response = requests.post(
        webhook_url,
        json=payload,
        timeout=timeout_seconds,
    )

    if response.status_code not in (
        200,
        204,
    ):

        raise DiscordWebhookError(
            f"Discord webhook failed.\n"
            f"HTTP status: {response.status_code}\n"
            f"Response: {response.text[:1000]}"
        )
