#!/usr/bin/env python3
"""Publish CFB totals graphics from the live totals odds CSV.

Over quotes use best_over_edge/best_price. Under quotes use the new
best_under_valid_edge/best_under_price fields, which are priced against
actual Under odds. Legacy best_under_edge is deliberately never used.
"""
import argparse
import csv
import io
import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont
from requests_oauthlib import OAuth1

ET = ZoneInfo("America/New_York")
SOURCE = Path("trash-schedule/CFB_Odds/Data/totals_odds.csv")
CROSSWALK = Path("CFB Teams Full Crosswalk.csv")
LEDGER = Path("outputs/cfb_x_totals_posted.json")
WEEKDAY_SLOTS = {"9:37", "11:37", "13:37", "15:37", "17:37"}
SATURDAY_SLOTS = {"9:22", "11:22", "14:22", "17:22", "20:22"}
GREEN, PINK = "#27E26F", "#FF7CB8"
WHITE, MUTED = "#F7F7F7", "#B5B5B5"


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def kickoff_time(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ET)
    except (ValueError, AttributeError):
        return None


def source_rows():
    with SOURCE.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise RuntimeError("totals_odds.csv is empty")
    with CROSSWALK.open(newline="", encoding="utf-8-sig") as source:
        teams = {r["btb_team"]: r for r in csv.DictReader(source)}
    week = max(int(r["week"]) for r in rows if r.get("week", "").isdigit())
    games = []
    for r in rows:
        if str(r.get("week")) != str(week) or "@" not in r.get("game", ""):
            continue
        away, home = r["game"].split("@", 1)
        r["away_short"] = teams.get(away, {}).get("btb_team_short") or away
        r["home_short"] = teams.get(home, {}).get("btb_team_short") or home
        r["away_logo"] = teams.get(away, {}).get("logo")
        r["home_logo"] = teams.get(home, {}).get("logo")
        r["kickoff"] = kickoff_time(r.get("commence_time"))
        if r["kickoff"] and number(r.get("model_prediction")) is not None and number(r.get("market_line")) is not None:
            games.append(r)
    return week, games


def quote(row):
    """Pick the strongest valid side; never treat an Over price as an Under price."""
    options = []
    for side, prefix, edge_key in (
        ("Over", "best", "best_over_edge"),
        ("Under", "best_under", "best_under_valid_edge"),
    ):
        edge = number(row.get(edge_key))
        line = number(row.get(prefix + "_line"))
        price = number(row.get(prefix + "_price"))
        prob_key = "best_over_probability" if side == "Over" else "best_under_cover_probability"
        probability = number(row.get(prob_key))
        if None not in (edge, line, price, probability):
            options.append({"side": side, "edge": edge, "line": line,
                            "price": price, "probability": probability,
                            "book": row.get("best_book" if side == "Over" else "best_under_book")})
    if options:
        best = max(options, key=lambda q: q["edge"])
        if (best["side"] == "Over" and best["edge"] < 0.03
                and number(row["model_prediction"]) < number(row["market_line"])
                and not any(q["side"] == "Under" for q in options)):
            # A legacy Over-only CSV cannot establish an Under price.
            return {"side": "Under", "edge": None, "line": number(row["market_line"]),
                    "price": None, "probability": number(row.get("under_probability")), "book": None}
        return best
    # Older CSVs lack a priced Under quote. Show the model direction as a
    # no-bet preview without inventing a price or edge.
    side = "Under" if number(row["model_prediction"]) < number(row["market_line"]) else "Over"
    probability = number(row.get("under_probability" if side == "Under" else "over_probability"))
    return {"side": side, "edge": None, "line": number(row["market_line"]),
            "price": None, "probability": probability, "book": None}


def load_ledger():
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"posts": []}


def slot_now(now):
    key = f"{now.hour}:{now.minute:02d}"
    allowed = SATURDAY_SLOTS if now.weekday() == 5 else WEEKDAY_SLOTS if now.weekday() < 5 else set()
    # GitHub Actions may start late, but do not publish into a later slot.
    for slot in allowed:
        hour, minute = map(int, slot.split(":"))
        start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if start <= now < start + timedelta(minutes=20):
            return slot
    return None


def select(games, ledger, week, now, slot, force_no_bet=False):
    date = now.date().isoformat()
    if any(p["date"] == date and p["slot"] == slot for p in ledger["posts"]):
        return None, None, "This totals slot is already posted"
    bet_posted = any(p["date"] == date and p["kind"] == "BET" for p in ledger["posts"])
    eligible = [(r, quote(r)) for r in games if r["kickoff"] > now + timedelta(minutes=10)]
    bets = [(r, q) for r, q in eligible if q["edge"] is not None and q["edge"] >= 0.03]
    no_bets = [(r, q) for r, q in eligible if q["edge"] is None or q["edge"] < 0.03]
    pool = no_bets if force_no_bet else (bets if bets and not bet_posted else no_bets)
    if not pool:
        return None, None, "No eligible pre-kickoff totals matchup"
    used = {p["game"] for p in ledger["posts"] if p["week"] == week}
    fresh = [(r, q) for r, q in pool if r["game"] not in used]
    if fresh:
        pool = fresh
    else:
        recent = [p["game"] for p in ledger["posts"] if p["date"] == date][-2:]
        alternatives = [(r, q) for r, q in pool if r["game"] not in recent]
        if alternatives:
            pool = alternatives
        elif recent:
            alternatives = [(r, q) for r, q in pool if r["game"] != recent[-1]]
            if alternatives:
                pool = alternatives
    pool.sort(key=lambda item: (item[0]["kickoff"], -item[1]["edge"] if item[1]["edge"] is not None else 1))
    r, q = pool[0]
    return r, q, "BET" if q["edge"] is not None and q["edge"] >= 0.03 else "NO BET"


def font(size, bold=False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return ImageFont.truetype(path, size)


def logo(url):
    if not url or url == "NA":
        return None
    try:
        response = requests.get(url, timeout=12)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content)).convert("RGBA")
        image.thumbnail((105, 105))
        return image
    except (requests.RequestException, OSError):
        return None


def graphic(row, selection, kind):
    canvas = Image.new("RGBA", (1200, 675), "#050505")
    draw = ImageDraw.Draw(canvas)
    draw.text((45, 35), "COLLEGE FOOTBALL TOTALS MODEL", font=font(19, True), fill=GREEN)
    matchup = f"{row['away_short']} @ {row['home_short']}"
    size = 46
    while size > 25 and draw.textbbox((0, 0), matchup, font=font(size, True))[2] > 850:
        size -= 1
    draw.text((45, 90), matchup, font=font(size, True), fill=WHITE)
    for x, url in ((940, row["away_logo"]), (1070, row["home_logo"])):
        image = logo(url)
        if image:
            canvas.alpha_composite(image, (x - image.width // 2, 90 - image.height // 2))
    for x, heading, value, color in (
        (45, "MARKET TOTAL", number(row["market_line"]), WHITE),
        (625, "OUR MODEL TOTAL", number(row["model_prediction"]), GREEN),
    ):
        draw.rounded_rectangle((x, 185, x + 530, 365), radius=18, fill="#111111", outline="#2A2A2A", width=2)
        draw.text((x + 25, 210), heading, font=font(19, True), fill=MUTED)
        draw.text((x + 25, 260), f"{value:g}", font=font(64, True), fill=color)
    difference = abs(number(row["model_prediction"]) - number(row["market_line"]))
    draw.text((45, 390), f"{difference:g}-POINT MODEL / MARKET GAP", font=font(22, True), fill=WHITE)
    best = f"{selection['side']} {selection['line']:g}"
    if selection["price"] is not None:
        best += f" {selection['price']:+g}"
    values = (best,
              f"{selection['probability']:.1%}" if selection["probability"] is not None else "N/A",
              f"{selection['edge']:.1%}" if selection["edge"] is not None else "UNVERIFIED",
              kind)
    first_label = "BEST NUMBER" if selection["price"] is not None else "MODEL SIDE"
    for i, (heading, value) in enumerate(zip((first_label, "MODEL COVER PROB.", "EDGE", "OUR CALL"), values)):
        x = 45 + i * 285
        draw.rounded_rectangle((x, 435, x + 260, 555), radius=14, fill="#171717", outline="#2A2A2A", width=2)
        draw.text((x + 17, 451), heading, font=font(14, True), fill=MUTED)
        size = 27
        while size > 15 and draw.textbbox((0, 0), value, font=font(size, True))[2] > 225:
            size -= 1
        draw.text((x + 17, 486), value, font=font(size, True),
                  fill=GREEN if kind == "BET" and heading == "OUR CALL" else PINK if heading == "OUR CALL" else WHITE)
    draw.text((45, 587), f"Kickoff {row['kickoff']:%a %-I:%M %p} ET", font=font(18), fill=MUTED)
    draw.text((45, 625), "BTB ANALYTICS  |  WE SHOW OUR WORK", font=font(20, True), fill=GREEN)
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def text_for(row, selection, kind, slot):
    title = f"{row['away_short']} vs {row['home_short']} Total Prediction"
    model = f"Our model total: {number(row['model_prediction']):g}; market total: {number(row['market_line']):g}."
    if kind == "BET":
        verdict = f"BET: {selection['side']} {selection['line']:g} ({selection['price']:+g}) | {selection['edge']:.1%} edge."
    elif selection["edge"] is None:
        verdict = f"NO BET: {selection['side']} price unavailable to verify a 3% edge."
    else:
        verdict = "NO BET: Does not meet our 3% edge threshold."
    body = "\n\n".join((title, model, verdict, f"Kickoff {row['kickoff']:%-I:%M %p} ET"))
    if len(body) > 280:
        body = "\n\n".join((title, model, verdict))
    if len(body) > 280:
        raise ValueError(f"Post exceeds 280 characters: {len(body)}")
    return body


def check(response, label):
    if response.ok:
        return response.json()
    try:
        payload = response.json()
        detail = {k: payload[k] for k in ("title", "detail", "errors") if k in payload}
    except ValueError:
        detail = {}
    raise RuntimeError(f"X {label} failed (HTTP {response.status_code}): {detail}")


def publish(row, selection, kind, slot):
    keys = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
    if any(not os.getenv(k) for k in keys):
        raise RuntimeError("Missing X OAuth 1.0a repository secrets")
    auth = OAuth1(*(os.environ[k] for k in keys))
    body = text_for(row, selection, kind, slot)
    image = graphic(row, selection, kind)
    print(f"Posting {kind} {row['game']}: {body}")
    media = check(requests.post("https://api.x.com/2/media/upload", auth=auth,
                                files={"media": ("cfb_totals.png", image, "image/png")},
                                data={"media_category": "tweet_image"}, timeout=60), "media upload")
    post = check(requests.post("https://api.x.com/2/tweets", auth=auth,
                               json={"text": body, "media": {"media_ids": [str(media["data"]["id"])]}},
                               timeout=60), "post creation")
    return str(post["data"]["id"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", help="Post; omit to preview selection")
    parser.add_argument("--test-now", action="store_true", help="Post one no-bet total immediately outside the schedule")
    args = parser.parse_args()
    if args.test_now and not args.publish:
        parser.error("--test-now requires --publish")
    now = datetime.now(ET)
    slot = f"test-{now:%Y%m%dT%H%M%S%f}" if args.test_now else slot_now(now)
    if not slot:
        print("Outside the totals posting schedule; skipping")
        return
    week, games = source_rows()
    ledger = load_ledger()
    row, selection, kind = select(games, ledger, week, now, slot, force_no_bet=args.test_now)
    if row is None:
        print(kind)
        return
    print(f"{now.date()} {slot} ET | Week {week} | {kind} | {row['game']} | kickoff {row['kickoff']}")
    body = text_for(row, selection, kind, slot)
    print(body)
    if not args.publish:
        print("DRY RUN: no post or ledger update")
        return
    post_id = publish(row, selection, kind, slot)
    ledger["posts"].append({"date": now.date().isoformat(), "slot": slot,
                            "week": week, "game": row["game"],
                            "kind": kind, "post_id": post_id})
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    print(f"Published X post ID {post_id}")


if __name__ == "__main__":
    main()
