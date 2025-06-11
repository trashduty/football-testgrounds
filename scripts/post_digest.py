import os, requests, textwrap
from datetime import datetime, timedelta
from openai import OpenAI, RateLimitError

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG & CLIENTS
# ─────────────────────────────────────────────────────────────────────────────
LEAGUE            = os.getenv('LEAGUE', 'NFL').upper()
WEBHOOK_ENV       = f"DISCORD_WEBHOOK_{LEAGUE}"
DISCORD_WEBHOOK   = os.getenv(WEBHOOK_ENV)
OPENAI_API_KEY    = os.getenv('OPENAI_API_KEY')
MAX_DISCORD_CHARS = 2000

if not DISCORD_WEBHOOK or not OPENAI_API_KEY:
    raise ValueError("Make sure DISCORD_WEBHOOK_<LEAGUE> and OPENAI_API_KEY are set")

openai = OpenAI(api_key=OPENAI_API_KEY)

# ─────────────────────────────────────────────────────────────────────────────
# 1) FETCH ESPN ARTICLES
# ─────────────────────────────────────────────────────────────────────────────
def fetch_espn_articles(league: str):
    """Pull the top 10 news items from ESPN’s JSON endpoint for NFL or CFB."""
    if league == "NFL":
        url = "http://site.api.espn.com/apis/site/v2/sports/football/nfl/news"
    else:
        url = "http://site.api.espn.com/apis/site/v2/sports/football/college-football/news"

    resp = requests.get(url, params={"limit": 10})
    resp.raise_for_status()
    data = resp.json().get("articles", [])
    articles = []
    for art in data:
        title = art.get("headline") or art.get("title")
        desc  = art.get("description") or art.get("summary") or ""
        src   = "ESPN"
        if title and desc:
            articles.append({"title": title.strip(), "src": src, "desc": desc.strip()})
    return articles

# ─────────────────────────────────────────────────────────────────────────────
# 2) BUILD THE PROMPT FROM ESPN ARTICLES
# ─────────────────────────────────────────────────────────────────────────────
def build_prompt(league: str, articles: list) -> str:
    header = (
        "You’re a professional sports news editor. "
        "Here are today’s top headlines. Summarize them under these headings:"
    )
    if league == "NFL":
        header += " General News, Signings, Minicamp Holdouts, Contract Updates, Injuries."
    else:
        header += " General News, Portal Transfers, Injuries, NIL Money Updates."
    header += " Only use the information provided—do not invent anything.\n\n"

    lines = []
    for art in articles:
        lines.append(f"- **{art['title']}** ({art['src']}): {art['desc']}")
    return header + "\n".join(lines)

# ─────────────────────────────────────────────────────────────────────────────
# 3) FETCH & SUMMARIZE
# ─────────────────────────────────────────────────────────────────────────────
def fetch_digest(league: str) -> str:
    arts = fetch_espn_articles(league)
    if not arts:
        return f"⚠️ No recent {league} articles found."

    prompt = build_prompt(league, arts)
    try:
        resp = openai.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-3.5-turbo"),
            messages=[
                {"role":"system","content":"You are a concise, factual sports news editor."},
                {"role":"user",  "content":prompt}
            ]
        )
        return resp.choices[0].message.content.strip()
    except RateLimitError:
        return f"⚠️ Could not generate {league} digest today: quota exceeded."

# ─────────────────────────────────────────────────────────────────────────────
# 4) DISCORD POSTING (with chunking)
# ─────────────────────────────────────────────────────────────────────────────
def post_to_discord(content: str):
    if not content.strip():
        raise ValueError("Empty content")
    if not DISCORD_WEBHOOK.startswith("https://discord.com/api/webhooks/"):
        raise ValueError("Invalid webhook URL")

    chunks = textwrap.wrap(content, MAX_DISCORD_CHARS,
                           break_long_words=False, replace_whitespace=False)
    for chunk in chunks:
        r = requests.post(DISCORD_WEBHOOK, json={"content": chunk})
        r.raise_for_status()

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    summary = fetch_digest(LEAGUE)
    post_to_discord(summary)
