#!/usr/bin/env python3
"""Post one CFB model graphic in an Eastern-time hourly slot.

Reads the latest week manifest and stores a persistent posting ledger in the repo.
Requires X OAuth 1.0a user credentials. Manual runs default to dry-run.
"""
import argparse
import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from requests_oauthlib import OAuth1

ET = ZoneInfo('America/New_York')
ROOT = Path('outputs/cfb_matchup_articles')
LEDGER = Path('outputs/cfb_x_posted.json')
BET_HOUR = 12
HOURS = tuple(range(9, 17))
SATURDAY_CSV = Path('trash-schedule/CFB_Odds/Data/spreads_odds.csv')


def latest_manifest():
    choices = []
    for p in ROOT.glob('week_*/weekly_matchup_articles.json'):
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
            choices.append((int(data['season']), int(data['week']), p, data))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
    if not choices:
        raise RuntimeError('No weekly_matchup_articles.json found')
    return max(choices, key=lambda x: (x[0], x[1]))


def load_ledger():
    return json.loads(LEDGER.read_text(encoding='utf-8')) if LEDGER.exists() else {'posts': []}


def load_kickoffs(week):
    """Read the latest source slate; missing or ambiguous kickoffs are ineligible."""
    with SATURDAY_CSV.open(newline='', encoding='utf-8-sig') as source:
        rows = csv.DictReader(source)
        times = {}
        for row in rows:
            if str(row.get('week', '')).strip() != str(week):
                continue
            try:
                kickoff = datetime.fromisoformat(row['commence_time'].replace('Z', '+00:00'))
                kickoff = kickoff.astimezone(ET)
            except (KeyError, ValueError, AttributeError):
                continue
            game = row.get('game', '').strip()
            if game:
                times.setdefault(game, set()).add(kickoff)
    return {game: next(iter(values)) for game, values in times.items() if len(values) == 1}


def saturday_slot(now):
    if now.hour not in range(9, 20) or (now.hour == 19 and now.minute >= 30):
        return None
    minute = 7 if now.minute < 30 else 37
    if now.minute < minute or now.minute >= minute + 20:
        return None
    return f'{now.hour}:{minute:02d}'


def select_saturday(manifest, ledger, season, week, now, slot, kickoffs):
    date = now.date().isoformat()
    if any(p['date'] == date and str(p['hour']) == slot for p in ledger['posts']):
        return None, 'This Saturday slot is already posted', None
    bets_today = sum(p['date'] == date and p['kind'] == 'Bet' for p in ledger['posts'])
    # If an earlier run failed, use later slots to make up the five bet posts.
    # Keep a ten-minute margin before kickoff, including workflow delays.
    for want_bet in ((True, False) if bets_today < 5 else (False,)):
        candidates = [(r, kickoffs[r['game']]) for r in manifest['articles']
                      if eligible(r, want_bet) and r.get('game') in kickoffs
                      and kickoffs[r['game']] > now + timedelta(minutes=10)]
        if candidates:
            break
    if not candidates:
        return None, 'No eligible pre-kickoff matchup', None
    kind = 'Bet' if want_bet else 'No Bet'
    week_id = f'{season}-week-{week}'
    used = {p['game'] for p in ledger['posts'] if p['season_week'] == week_id}
    fresh = [(r, kickoff) for r, kickoff in candidates if r['game'] not in used]
    if fresh:
        candidates = fresh
    else:
        # Repeats are allowed on Saturday, but only after all eligible
        # unused games of this classification have been exhausted.
        recent = [p['game'] for p in ledger['posts'] if p['date'] == date][-2:]
        alternatives = [(r, kickoff) for r, kickoff in candidates if r['game'] not in recent]
        if alternatives:
            candidates = alternatives
    candidates.sort(key=lambda pair: (pair[1], -float(pair[0]['edge']) if want_bet else pair[0]['game']))
    row, kickoff = candidates[0]
    return row, kind, kickoff


def eligible(row, want_bet):
    try:
        edge = float(row['edge'])
    except (KeyError, TypeError, ValueError):
        return False
    # Treat disagreement between computed edge and classification as ineligible.
    if bool(row.get('has_bet')) != (edge >= 0.03):
        return False
    assets = row.get('social_assets') or {}
    return (edge >= 0.03) == want_bet and bool(assets.get('x_model_graphic'))


def select(manifest, ledger, season, week, date, hour):
    identifier = f'{season}-week-{week}'
    posted = ledger['posts']
    if any(p['date'] == date and p['hour'] == hour for p in posted):
        return None, 'This slot is already posted'
    used = {p['game'] for p in posted if p['season_week'] == identifier}
    daily_bet = any(p['date'] == date and p['kind'] == 'Bet' for p in posted)
    want_bet = hour == BET_HOUR
    if want_bet and daily_bet:
        return None, 'Daily bet already posted'
    candidates = [r for r in manifest['articles'] if r.get('game') not in used and eligible(r, want_bet)]
    if not candidates:
        return None, 'No eligible unused matchup'
    # High edge first for bet; stable game ordering for the seven no-bet slots.
    candidates.sort(key=(lambda r: (-float(r['edge']), r['game'])) if want_bet else (lambda r: r['game']))
    return candidates[0], 'Bet' if want_bet else 'No Bet'


def check_x_response(response, operation):
    if response.ok:
        return
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    # X's structured error identifies permissions, entitlement, duplicate text,
    # or other policy issues; do not print request headers or credentials.
    details = {key: payload[key] for key in ('title', 'detail', 'type', 'message', 'errors')
               if key in payload}
    raise RuntimeError(f'X {operation} failed (HTTP {response.status_code}): '
                       + json.dumps(details, ensure_ascii=False))


def weekly_bet_count(manifest):
    """Count distinct games classified as bets in the weekly manifest."""
    return len({row['game'] for row in manifest['articles'] if eligible(row, True)})


def format_line(value):
    number = float(value)
    return f'{number:+g}' if number > 0 else f'{number:g}'


def format_price(value):
    number = float(value)
    return f'{number:+g}' if number > 0 else f'{number:g}'


def build_post_text(row, kind, bet_count, kickoff=None, slot=None):
    title = f"{row['away_short']} vs {row['home_short']} Prediction"
    if kind == 'Bet':
        verdict = (f"BET: {row['bet_short']} {format_line(row['best_line'])} "
                   f"({format_price(row['best_price'])}) | {float(row['edge']):.1%} edge")
    else:
        verdict = 'NO BET: Does not meet our 3% edge threshold.'
    model = (f"Our model makes {row['bet_short']} {format_line(row['model_prediction'])}; "
             f"market: {format_line(row['market_line'])}.")
    count = f"There are {bet_count} {'game' if bet_count == 1 else 'games'} this week that clear our 3% edge threshold."
    link = 'Full list: https://btb-analytics.com'
    timing = (f"Kickoff {kickoff:%-I:%M%p} ET · {slot}"
              if kickoff is not None and slot else None)
    parts = (title, model, verdict, timing, count, link) if timing else (title, model, verdict, count, link)
    body = '\n\n'.join(p for p in parts if p)
    if len(body) > 280:
        parts = (title, model, verdict, count, link)
        body = '\n\n'.join(p for p in parts if p)
    if len(body) > 280:
        raise ValueError(f'X post exceeds 280 characters ({len(body)})')
    return body


def post(row, folder, kind, bet_count, kickoff=None, slot=None):
    needed = ('X_API_KEY', 'X_API_SECRET', 'X_ACCESS_TOKEN', 'X_ACCESS_TOKEN_SECRET')
    missing = [k for k in needed if not os.getenv(k)]
    if missing:
        raise RuntimeError('Missing GitHub secrets: ' + ', '.join(missing))
    auth = OAuth1(*(os.environ[k] for k in needed))
    assets = row['social_assets']
    image = folder / assets['x_model_graphic']
    body = build_post_text(row, kind, bet_count, kickoff, slot)
    print(f'Post text ({len(body)} characters): {body}')
    if not image.is_file():
        raise FileNotFoundError(image)
    with image.open('rb') as fp:
        response = requests.post('https://api.x.com/2/media/upload',
                                 auth=auth, files={'media': (image.name, fp, 'image/png')},
                                 data={'media_category': 'tweet_image'}, timeout=60)
    check_x_response(response, 'media upload')
    media_id = str(response.json()['data']['id'])
    response = requests.post('https://api.x.com/2/tweets', auth=auth,
                             json={'text': body, 'media': {'media_ids': [media_id]}}, timeout=60)
    check_x_response(response, 'post creation')
    return response.json()['data']['id']

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--publish', action='store_true', help='Actually publish; default is dry-run')
    parser.add_argument('--test-now', action='store_true', help='Publish one no-bet matchup immediately')
    parser.add_argument('--hour', type=int, help='Dry-run only: preview a particular ET hour')
    args = parser.parse_args()
    now = datetime.now(ET)
    hour = args.hour if args.hour is not None else now.hour
    if args.publish and args.hour is not None:
        parser.error('--hour cannot be combined with --publish')
    if args.test_now and not args.publish:
        parser.error('--test-now requires --publish')
    saturday = now.weekday() == 5 and not args.test_now
    slot = saturday_slot(now) if saturday else None
    if not args.test_now and not (saturday and slot) and (now.weekday() > 4 or hour not in HOURS or (args.publish and now.minute > 30)):
        print('Outside posting schedule; skipping')
        return
    season, week, path, manifest = latest_manifest()
    # Avoid posting a prior week's slate if generation failed to update it.
    generated = datetime.fromisoformat(manifest['generated_at_utc']).astimezone(ET)
    if (now - generated).total_seconds() > 7 * 86400:
        print('Manifest is more than seven days old; skipping')
        return
    date = now.date().isoformat()
    ledger = load_ledger()
    kickoff = None
    if saturday:
        row, kind, kickoff = select_saturday(manifest, ledger, season, week, now, slot, load_kickoffs(week))
    elif args.test_now:
        # Test runs consume a real no-bet matchup but never occupy a scheduled slot.
        used = {p['game'] for p in ledger['posts'] if p['season_week'] == f'{season}-week-{week}'}
        candidates = sorted((r for r in manifest['articles']
                             if r.get('game') not in used and eligible(r, False)),
                            key=lambda r: r['game'])
        row, kind = (candidates[0], 'No Bet') if candidates else (None, 'No eligible unused no-bet matchup')
    else:
        row, kind = select(manifest, ledger, season, week, date, hour)
    if row is None:
        print(kind)
        return
    print(f"{date} {slot if saturday else f'{hour}:00'} ET | {kind} | {row['game']} | edge={row['edge']}")
    if not args.publish:
        print('DRY RUN: no X API call and no ledger update')
        return
    post_id = post(row, path.parent, kind, weekly_bet_count(manifest), kickoff, slot)
    ledger['posts'].append({'date': date, 'hour': f'test-{now.isoformat()}' if args.test_now else (slot if saturday else hour),
                            'season_week': f'{season}-week-{week}',
                            'game': row['game'], 'kind': kind, 'post_id': str(post_id)})
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=2) + '\n', encoding='utf-8')
    print(f'Published X post ID {post_id}')


if __name__ == '__main__':
    main()
