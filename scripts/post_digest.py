import os, requests
from openai import OpenAI
from datetime import datetime, timedelta

# Pick league via ENV
LEAGUE = os.getenv('LEAGUE', 'NFL').upper()  
WEBHOOK_ENV = f"DISCORD_WEBHOOK_{LEAGUE}"
DISCORD_WEBHOOK = os.getenv(WEBHOOK_ENV)
if not DISCORD_WEBHOOK:
    raise ValueError(f"No webhook for {LEAGUE} (env {WEBHOOK_ENV})")

client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

def fetch_digest(league):
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')
    prompt = f"Summarize {league} news from {yesterday} in bullet points."
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}]
    )
    return resp.choices[0].message.content

def post_to_discord(content):
    r = requests.post(DISCORD_WEBHOOK, json={"content": content})
    r.raise_for_status()

if __name__ == "__main__":
    digest = fetch_digest(LEAGUE)
    post_to_discord(digest)
