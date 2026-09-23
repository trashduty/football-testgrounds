
"""Post one CFB model graphic in an Eastern-time hourly slot.

Reads the latest week manifest and stores a persistent posting ledger in the repo.
Requires X OAuth 1.0a user credentials. Manual runs default to dry-run.
"""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from requests_oauthlib import OAuth1

ET = ZoneInfo('America/New_York')
ROOT = Path('outputs/cfb_matchup_articles')
LEDGER = Path('outputs/cfb_x_posted.json')
BET_HOUR = 12
HOURS = tuple(range(9, 17))


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


def post(row, folder):
    needed = ('X_API_KEY', 'X_API_SECRET', 'X_ACCESS_TOKEN', 'X_ACCESS_TOKEN_SECRET')
    missing = [k for k in needed if not os.getenv(k)]
    if missing:
        raise RuntimeError('Missing GitHub secrets: ' + ', '.join(missing))
    auth = OAuth1(*(os.environ[k] for k in needed))
    assets = row['social_assets']
    image = folder / assets['x_model_graphic']
    caption = (folder / assets['x_caption']).read_text(encoding='utf-8').strip()
    title = f"{row['away_short']} vs {row['home_short']} Prediction"
    body = title + '\n\n' + caption
    if not image.is_file():
        raise FileNotFoundError(image)
    with image.open('rb') as fp:
        response = requests.post('https://api.x.com/2/media/upload',
                                 auth=auth, files={'media': (image.name, fp, 'image/png')},
                                 data={'media_category': 'tweet_image'}, timeout=60)
    response.raise_for_status()
    media_id = str(response.json()['data']['id'])
    response = requests.post('https://api.x.com/2/tweets', auth=auth,
                             json={'text': body, 'media': {'media_ids': [media_id]}}, timeout=60)
    response.raise_for_status()
    return response.json()['data']['id']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--publish', action='store_true', help='Actually publish; default is dry-run')
    parser.add_argument('--hour', type=int, help='Dry-run only: preview a particular ET hour')
    args = parser.parse_args()
    now = datetime.now(ET)
    hour = args.hour if args.hour is not None else now.hour
    if args.publish and args.hour is not None:
        parser.error('--hour cannot be combined with --publish')
    if now.weekday() > 3 or hour not in HOURS or (args.publish and now.minute > 30):
        print('Outside Monday–Thursday 9:00–16:30 Eastern; skipping')
        return
    season, week, path, manifest = latest_manifest()
    # Avoid posting a prior week's slate if generation failed to update it.
    generated = datetime.fromisoformat(manifest['generated_at_utc']).astimezone(ET)
    if (now - generated).total_seconds() > 7 * 86400:
        print('Manifest is more than seven days old; skipping')
        return
    date = now.date().isoformat()
    ledger = load_ledger()
    row, kind = select(manifest, ledger, season, week, date, hour)
    if row is None:
        print(kind)
        return
    print(f"{date} {hour}:00 ET | {kind} | {row['game']} | edge={row['edge']}")
    if not args.publish:
        print('DRY RUN: no X API call and no ledger update')
        return
    post_id = post(row, path.parent)
    ledger['posts'].append({'date': date, 'hour': hour, 'season_week': f'{season}-week-{week}',
                            'game': row['game'], 'kind': kind, 'post_id': str(post_id)})
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=2) + '\n', encoding='utf-8')
    print(f'Published X post ID {post_id}')


if __name__ == '__main__':
    main()
