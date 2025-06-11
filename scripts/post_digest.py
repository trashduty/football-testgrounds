import os
import requests
import textwrap
from openai import OpenAI, RateLimitError

# ─────────────────────────────────────────────────────────────────────────────
# 0) CONFIG & ENV
# ─────────────────────────────────────────────────────────────────────────────
LEAGUE            = os.getenv('LEAGUE', 'NFL').upper()
WEBHOOK_ENV       = f"DISCORD_WEBHOOK_{LEAGUE}"
DISCORD_WEBHOOK   = os.getenv(WEBHOOK_ENV)
OPENAI_API_KEY    = os.getenv('OPENAI_API_KEY')
RAPIDAPI_KEY      = os.getenv('RAPIDAPI_KEY')
RAPIDAPI_HOST     = os.getenv('RAPIDAPI_HOST')
MAX_DISCORD_CHARS = 2000

for var, val in [
    ("DISCORD_WEBHOOK", DISCORD_WEBHOOK),
    ("OPENAI_API_KEY", OPENAI_API_KEY),
    ("RAPIDAPI_KEY", RAPIDAPI_KEY),
    ("RAPIDAPI_HOST", RAPIDAPI_HOST),
]:
    if not val:
        raise ValueError(f"Missing required environment variable: {var}")

openai = OpenAI(api_key=OPENAI_API_KEY)

# ─────────────────────────────────────────────────────────────────────────────
# 1) FETCH ESPN ARTICLES
# ─────────────────────────────────────────────────────────────────────────────
def fetch_espn_articles(league: str):
    """Pull the top 8 news items from ESPN’s NFL or CFB news endpoint."""
    if league == "NFL":
        url = "http://site.api.espn.com/apis/site/v2/sports/football/nfl/news"
    else:
        url = "http://site.api.espn.com/apis/site/v2/sports/football/college-football/news"

    resp = requests.get(url, params={"limit": 8})
    resp.raise_for_status()
    data = resp.json().get("articles", [])
    articles = []
    for art in data:
        title = art.get("headline") or art.get("title")
        desc  = art.get("description") or art.get("summary") or ""
        link  = art.get("links", {}).get("web", {}).get("href", "")
        if title and desc and link:
            articles.append({
                "title": title.strip(),
                "src":   "ESPN",
                "desc":  desc.strip(),
                "url":   link.strip()
            })
    return articles

# ─────────────────────────────────────────────────────────────────────────────
# 2) FETCH RAPIDAPI HEADLINES
# ─────────────────────────────────────────────────────────────────────────────
def fetch_rapidapi_articles(league: str):
    """Fetch the top 5 sports headlines from RapidAPI’s /top-headlines endpoint."""
    host = RAPIDAPI_HOST
    url  = f"https://{host}/top-headlines"
    headers = {
        "X-RapidAPI-Key":  RAPIDAPI_KEY,
        "X-RapidAPI-Host": host
    }
    params = {
        "category": "nfl" if league == "NFL" else "college-football",
        "limit":    5
    }
    resp = requests.get(url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json().get("articles", [])
    articles = []
    for art in data:
        title = art.get("title") or art.get("headline")
        desc  = art.get("description") or art.get("summary") or ""
        link  = art.get("url") or art.get("link")
        src   = art.get("source", {}).get("name", "RapidAPI")
        if title and desc and link:
            articles.append({
                "title": title.strip(),
                "src":   src.strip(),
                "desc":  desc.strip(),
                "url":   link.strip()
            })
    return articles

# ─────────────────────────────────────────────────────────────────────────────
# 3) COMBINE & DEDUPE
# ─────────────────────────────────────────────────────────────────────────────
def fetch_combined_articles(league: str):
    espn = fetch_espn_articles(league)
    rap  = fetch_rapidapi_articles(league)
    seen = set()
    combined = []
    for art in espn + rap:
        key = (art["title"], art["src"])
        if key not in seen:
            seen.add(key)
            combined.append(art)
    return combined

# ─────────────────────────────────────────────────────────────────────────────
# 4) BUILD GPT PROMPT
# ─────────────────────────────────────────────────────────────────────────────
def build_prompt(league: str, articles: list) -> str:
    header = (
        "You’re a professional sports news editor. "
        "Below are today’s top headlines—include title, source, description, and URL.  "
        "Summarize them in bullet points under these headings:"
    )
    if league == "NFL":
        header += " General News, Signings, Minicamp Holdouts, Contract Updates, Injuries."
    else:
        header += " General News, Portal Transfers, Injuries, NIL Money Updates."
    header += " Keep bullets concise and only use provided info.\n\n"

    lines = []
    for art in articles:
        lines.append(
            f"- **{art['title']}** ({art['src']}): {art['desc']}  {art['url']}"
        )
    return header + "\n".join(lines)

# ─────────────────────────────────────────────────────────────────────────────
# 5) FETCH & SUMMARIZE VIA OPENAI
# ─────────────────────────────────────────────────────────────────────────────
def fetch_digest(league: str) -> str:
    articles = fetch_combined_articles(league)
    if not articles:
        return f"⚠️ No recent {league} articles found."

    prompt = build_prompt(league, articles)
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
# 6) POST TO DISCORD (with chunking)
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
