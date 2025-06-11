import os, requests
from openai import OpenAI
from datetime import datetime, timedelta

# Determine league and corresponding webhook
LEAGUE = os.getenv('LEAGUE', 'NFL').upper()
WEBHOOK_ENV = f"DISCORD_WEBHOOK_{LEAGUE}"
DISCORD_WEBHOOK = os.getenv(WEBHOOK_ENV)
if not DISCORD_WEBHOOK:
    raise ValueError(f"No webhook for {LEAGUE} (env {WEBHOOK_ENV})")

# OpenAI client
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

def build_prompt(league: str) -> str:
    """Return a tailored prompt for the given league."""
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')
    if league == "NFL":
        return (
            f"Give me a daily NFL news digest for {yesterday}, in bullet points, "
            "covering: general news summary, signings, minicamp holdouts, contract updates, and injuries."
        )
    elif league in ("CFB", "NCAAF"):
        return (
            f"Give me a daily College Football news digest for {yesterday}, in bullet points, "
            "covering: general news summary, portal transfers, injuries, and NIL money updates."
        )
    else:
        # fallback
        return f"Summarize {league} news from {yesterday} in bullet points."

def fetch_digest(league: str) -> str:
    """Ask GPT for a factual, real‐world digest—no fictional examples or disclaimers."""
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')
    user_prompt = build_prompt(league)
    
    messages = [
        {
            "role": "system",
            "content": (
                "You are a professional sports news editor. "
                "Summarize only factual events from reputable sources—do not invent or fictionalize anything. "
                "If you’re unsure, say you don’t know."
            )
        },
        {"role": "user", "content": user_prompt}
    ]
    
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-3.5-turbo"),
        messages=messages
    )
    return resp.choices[0].message.content


import requests
import textwrap

MAX_DISCORD_CHARS = 2000

def post_to_discord(content: str):
    """
    Posts content to the Discord webhook, splitting into multiple messages
    if it exceeds Discord's 2,000-character limit.
    """
    if not DISCORD_WEBHOOK.startswith("https://discord.com/api/webhooks/"):
        raise ValueError(f"Invalid webhook URL: {DISCORD_WEBHOOK}")

    if not content or not content.strip():
        raise ValueError("Cannot send empty content to Discord")

    # Split into chunks under the character limit, splitting on newlines when possible
    chunks = textwrap.wrap(content, MAX_DISCORD_CHARS, break_long_words=False, replace_whitespace=False)
    for i, chunk in enumerate(chunks, 1):
        payload = {"content": chunk}
        resp = requests.post(DISCORD_WEBHOOK, json=payload)
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            # Include the response body in the error for debugging
            raise RuntimeError(f"Discord returned {resp.status_code}: {resp.text}") from e


if __name__ == "__main__":
    digest = fetch_digest(LEAGUE)
    post_to_discord(digest)
