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
    prompt = build_prompt(league)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.choices[0].message.content

def post_to_discord(content: str):
    r = requests.post(DISCORD_WEBHOOK, json={"content": content})
    r.raise_for_status()

if __name__ == "__main__":
    digest = fetch_digest(LEAGUE)
    post_to_discord(digest)
