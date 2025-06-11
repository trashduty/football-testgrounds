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

    # The response is a list of article dicts
    items = resp.json()  
    articles = []
    for art in items:
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
