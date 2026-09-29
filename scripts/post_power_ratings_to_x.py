#!/usr/bin/env python3
"""Post the current CFB or NFL power-rating scatter plot to X."""
import argparse
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from requests_oauthlib import OAuth1

ET = ZoneInfo("America/New_York")
LEDGER = Path("outputs/power_ratings_x_posted.json")


def current_metadata(sport, now, test_now):
    season = now.year - (now.month <= 2)
    folder = Path("docs/data" if sport == "cfb" else "docs/nfl/data") / str(season)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    if int(meta["season"]) != season:
        raise RuntimeError(f"{sport.upper()} metadata season is not {season}")
    built = datetime.fromisoformat(meta["generated_utc"].replace("Z", "+00:00"))
    if built.tzinfo is None:
        built = built.replace(tzinfo=timezone.utc)
    age = now - built.astimezone(ET)
    if age < timedelta(minutes=-10) or age > (timedelta(days=7) if test_now else timedelta(hours=24)):
        raise RuntimeError(f"{sport.upper()} ratings are stale: generated {built.isoformat()}")
    return season, folder, meta


def render_site_plot(sport, season, destination):
    # Capture the Plotly chart visitors actually see, including team logos.
    from PIL import Image
    from playwright.sync_api import sync_playwright

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1450, "height": 1100}, device_scale_factor=1)
            page.goto(
                f"http://127.0.0.1:{server.server_port}/docs/"
                + ("index.html" if sport == "cfb" else "nfl/index.html"),
                wait_until="domcontentloaded",
            )
            page.select_option("#season", str(season))
            page.select_option("#metric", "power")
            page.wait_for_function(
                """season => {
                  const chart = document.querySelector('#chart');
                  return chart && chart.data && chart.data.length &&
                    chart.layout && String(chart.layout.title?.text || '').includes(String(season)) &&
                    chart.querySelector('.main-svg');
                }""",
                arg=season,
                timeout=45000,
            )
            page.wait_for_function(
                """() => document.querySelectorAll('#chart .images image').length >= 20""",
                timeout=45000,
            )
            # SVG image tags can exist before their external logo files load.
            page.wait_for_timeout(2000)
            page.locator("#chart").screenshot(path=str(destination), animations="disabled", timeout=45000)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    with Image.open(destination) as graphic:
        graphic = graphic.convert("RGB")
        # A points-only plot is nearly grayscale. Require substantial logo color
        # so a failed external image load cannot become a public X post.
        colorful = sum(
            1 for red, green, blue in graphic.resize((400, 300)).getdata()
            if max(red, green, blue) - min(red, green, blue) > 45
        )
    if colorful < 150:
        raise RuntimeError(f"{sport.upper()} plot has too few rendered logo pixels ({colorful})")


def x_response(response, operation):
    if response.ok:
        return response.json()
    try:
        payload = response.json()
        detail = {key: payload[key] for key in ("title", "detail", "errors") if key in payload}
    except ValueError:
        detail = {}
    raise RuntimeError(f"X {operation} failed (HTTP {response.status_code}): {detail}")


def post(image, body):
    keys = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
    missing = [key for key in keys if not os.getenv(key)]
    if missing:
        raise RuntimeError("Missing X OAuth 1.0a secrets: " + ", ".join(missing))
    auth = OAuth1(*(os.environ[key] for key in keys))
    with image.open("rb") as source:
        media = x_response(requests.post(
            "https://api.x.com/2/media/upload", auth=auth,
            files={"media": (image.name, source, "image/png")},
            data={"media_category": "tweet_image"}, timeout=60,
        ), "media upload")
    result = x_response(requests.post(
        "https://api.x.com/2/tweets", auth=auth,
        json={"text": body, "media": {"media_ids": [str(media["data"]["id"])]}},
        timeout=60,
    ), "post creation")
    return str(result["data"]["id"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=("cfb", "nfl"), required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--test-now", action="store_true", help="Manual post outside the normal day/time")
    parser.add_argument("--repost", action="store_true", help="Publish a corrected image for an already posted week")
    args = parser.parse_args()
    if args.test_now and not args.publish:
        parser.error("--test-now requires --publish")
    if args.repost and not (args.test_now and args.publish):
        parser.error("--repost requires --publish --test-now")

    now = datetime.now(ET)
    expected = (0, 10) if args.sport == "cfb" else (1, 14)
    if args.publish and not args.test_now and (now.weekday(), now.hour) != expected:
        print("Outside the power-ratings posting window; skipping")
        return

    season, folder, meta = current_metadata(args.sport, now, args.test_now)
    entering_week = int(meta["thru_week"]) + 1
    identifier = f"{args.sport}-{season}-entering-{entering_week}"
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"posts": []}
    if args.publish and not args.repost and any(entry["id"] == identifier for entry in ledger["posts"]):
        print(f"Already posted {identifier}; skipping")
        return

    image = Path("output") / args.sport / str(season) / "btb_scatter_x.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    render_site_plot(args.sport, season, image)
    if not image.is_file() or not image.stat().st_size:
        raise RuntimeError(f"Missing or empty power-rating plot: {image}")

    body = f"BTB's {season} {args.sport.upper()} Power Ratings — Entering Week {entering_week}"
    if args.repost:
        body += " | Updated chart"
    print(f"{identifier}: {image} ({image.stat().st_size} bytes)\n{body}")
    if not args.publish:
        print("DRY RUN: no X post or ledger update")
        return
    post_id = post(image, body)
    ledger["posts"].append({"id": identifier, "post_id": post_id, "posted_at_et": now.isoformat()})
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    print(f"Published X post ID {post_id}")


if __name__ == "__main__":
    main()
