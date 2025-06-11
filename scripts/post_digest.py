import os
import requests
import textwrap
from openai import OpenAI, RateLimitError
from datetime import datetime, timedelta

# -------------------------------------------------------------------
# 1) Load config from environment
# -------------------------------------------------------------------
LEAGUE = os.getenv('LEAGUE', 'NFL').upper()  
WEBHOOK_ENV = f"DISCORD_WEBHOOK_{LEAGUE}"
DISCORD_WEBHOOK = os.getenv(WEBHOOK_ENV)
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if not DISCORD_WEBHOOK:
    raise ValueError(f"No webhook set for league {LEAGUE} (env var {WEBHOOK_ENV})")
if not OPENAI_API_KEY:
    raise ValueError("Missing OPENAI_API_KEY environment variable")

# -------------------------------------------------------------------
# 2) Initialize OpenAI client
# -------------------------------------------------------------------
client = OpenAI(api_key=OPENAI_API_KEY)

# -------------------------------------------------------------------
# 3) Build the dynamic prompt for each league
# -------------------------------------------------------------------
def build_prompt(league: str) -> str:
    if league == "NFL":
        return (
            "You’re a professional sports news editor. "
            "Give me a bullet-point NFL news digest covering the last 24 hours, "
            "including: general news summary, signings, minicamp holdouts, contract updates, and injuries. "
            "Only report factual events—do not invent or fictionalize."
        )
    elif league in ("CFB", "NCAAF"):
        return (
            "You’re a professional sports news editor. "
            "Give me a bullet-point College Football news digest covering the last 24 hours, "
            "including: general news summary, portal transfers, injuries, and NIL money updates. "
            "Only report factual events—do not invent or fictionalize."
        )
    else:
        return (
            f"You’re a professional sports news editor. "
            f"Summarize the last 24 hours of {league} news in bullet points. "
            "Only report factual events—do not invent or fictionalize."
        )

# -------------------------------------------------------------------
# 4) Fetch the digest from OpenAI
# -------------------------------------------------------------------
def fetch_digest(league: str) -> str:
    prompt = build_prompt(league)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a professional sports news editor. "
                "Summarize only factual events from reputable sources—do not invent or fictionalize anything. "
                "If you’re unsure, say you don’t know."
            )
        },
        {"role": "user", "content": prompt}
    ]

    try:
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-3.5-turbo"),
            messages=messages
        )
        return resp.choices[0].message.content.strip()
    except RateLimitError:
        return f"⚠️ Could not generate {league} digest today: quota exceeded."

# -------------------------------------------------------------------
# 5) Post content to Discord, handling length & errors
# -------------------------------------------------------------------
MAX_DISCORD_CHARS = 2000

def post_to_discord(content: str):
    # Validate webhook URL
    if not DISCORD_WEBHOOK.startswith("https://discord.com/api/webhooks/"):
        raise ValueError(f"Invalid Discord webhook URL: {DISCORD_WEBHOOK}")

    # Prevent empty posts
    if not content or not content.strip():
        raise ValueError("Cannot send empty content to Discord")

    # Split into <=2000-char chunks, preserving line breaks
    chunks = textwrap.wrap(content, MAX_DISCORD_CHARS, 
                           break_long_words=False, replace_whitespace=False)

    for idx, chunk in enumerate(chunks, start=1):
        payload = {"content": chunk}
        resp = requests.post(DISCORD_WEBHOOK, json=payload)
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            # Include Discord’s error message for debugging
            raise RuntimeError(
                f"Discord returned {resp.status_code} on chunk {idx}: {resp.text}"
            ) from e

# -------------------------------------------------------------------
# 6) Main execution: fetch & post
# -------------------------------------------------------------------
if __name__ == "__main__":
    digest = fetch_digest(LEAGUE)
    post_to_discord(digest)
