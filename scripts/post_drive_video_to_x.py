#!/usr/bin/env python3
"""Authorize X and post reviewed Drive videos, at most twice per Eastern day."""
import argparse
import base64
import hashlib
import getpass
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

ROOT = Path('outputs/drive_videos')
STATE = ROOT / 'x_oauth.enc'
LEDGER = ROOT / 'posted.json'
QUEUE = ROOT / 'queue.json'
REDIRECT = 'http://127.0.0.1:8765/callback'
SCOPES = {'tweet.read', 'tweet.write', 'users.read', 'media.write', 'offline.access'}
ET = ZoneInfo('America/New_York')


class ApiError(RuntimeError):
    def __init__(self, status, detail=None):
        self.status = status
        super().__init__(f'X API HTTP {status}; ' + (detail or 'inspect app permissions, credits, and video eligibility'))


def token_error_detail(body):
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return 'OAuth token exchange failed (response details withheld)'
    categories = {'invalid_request', 'invalid_grant', 'invalid_client', 'unauthorized_client',
                  'unsupported_grant_type', 'invalid_scope', 'server_error', 'temporarily_unavailable'}
    category = data.get('error')
    if not isinstance(category, str) or category not in categories:
        category = 'unrecognized_error'
    description = str(data.get('error_description', '')).lower()
    hints = []
    for words, hint in [
        (('expired',), 'X reported expiry'),
        (('authorization code', 'authorization_code'), 'X rejected the one-time authorization code'),
        (('code_verifier', 'code verifier', 'pkce'), 'X reported a PKCE/verifier issue'),
        (('redirect_uri', 'redirect uri'), 'X reported a callback URI issue'),
        (('client_secret', 'client secret'), 'X reported a client secret issue'),
        (('client_id', 'client id'), 'X reported a client ID issue')]:
        if any(word in description for word in words):
            hints.append(hint)
    return 'OAuth token exchange: ' + category + ('. ' + '; '.join(hints) if hints else '') + '. No response values were logged.'


def request(method, path, token=None, payload=None, raw=None, content_type=None, basic=None):
    headers = {}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if basic:
        headers['Authorization'] = 'Basic ' + base64.b64encode(':'.join(basic).encode()).decode()
    if payload is not None:
        raw, content_type = json.dumps(payload).encode(), 'application/json'
    if content_type:
        headers['Content-Type'] = content_type
    req = urllib.request.Request('https://api.x.com' + path, data=raw, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        if path == '/2/oauth2/token':
            raise ApiError(error.code, token_error_detail(error.read(16384))) from None
        # Do not log response bodies or authorization codes.
        raise ApiError(error.code) from None


def cipher():
    secret = os.environ['OAUTH_2_CLIENT_SECRET']
    if not secret:
        raise ValueError('OAUTH_2_CLIENT_SECRET is empty')
    key = hashlib.sha256(b'BTB Drive X OAuth state v1\x00' + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.replace(path)


def save_state(state):
    ROOT.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix('.tmp')
    temp.write_bytes(cipher().encrypt(json.dumps(state).encode()))
    temp.replace(STATE)


def read_state():
    if not STATE.exists():
        raise ValueError('Run start_auth and finish_auth first')
    return json.loads(cipher().decrypt(STATE.read_bytes()))


def checkpoint():
    """Durably save rotated tokens and pre-post reservations before proceeding."""
    if os.getenv('PERSIST_TO_GIT') != 'true':
        return
    for path in (STATE, LEDGER):
        if path.exists():
            subprocess.run(['git', 'add', str(path)], check=True)
    if subprocess.run(['git', 'diff', '--staged', '--quiet']).returncode == 0:
        return
    subprocess.run(['git', 'commit', '-m', 'Save encrypted X authorization and video posting state'], check=True)
    subprocess.run(['git', 'pull', '--rebase', 'origin', 'main'], check=True)
    subprocess.run(['git', 'push', 'origin', 'HEAD:main'], check=True)


def summary(text):
    print(text, flush=True)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as target:
            target.write(text + '\n\n')


def authorization_url(client_id, redirect, state, verifier):
    client_id = client_id.strip()
    if not client_id or '*' in client_id or any(c.isspace() for c in client_id):
        raise ValueError('OAUTH_2_CLIENT_ID must be the unmasked OAuth 2.0 Client ID from X, without whitespace')
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return 'https://x.com/i/oauth2/authorize?' + urllib.parse.urlencode({
        'response_type': 'code', 'client_id': client_id,
        'redirect_uri': redirect, 'scope': ' '.join(sorted(SCOPES)),
        'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'},
        quote_via=urllib.parse.quote)


def start_auth():
    previous = read_state() if STATE.exists() else {}
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    previous['pending_auth'] = {'verifier': verifier, 'state': state, 'created': time.time(),
                                'redirect': os.getenv('OAUTH_2_REDIRECT_URI') or REDIRECT}
    save_state(previous)
    checkpoint()
    url = authorization_url(os.environ['OAUTH_2_CLIENT_ID'], previous['pending_auth']['redirect'], state, verifier)
    summary('Open this link while signed into the X account to post from:\n\n[Authorize BTB on X](' + url + ')')
    summary('Callback to register in the same X app: `' + previous['pending_auth']['redirect'] + '`\n\n'
            'Request checked: response_type=code, PKCE=S256, state present. Requested permissions: '
            '`' + ' '.join(sorted(SCOPES)) + '`.')
    summary('After approving, the loopback callback may show a connection error. Copy the entire address '
            'from the address bar into repository secret OAUTH_2_AUTHORIZATION_RESPONSE, then run finish_auth promptly.')


def exchange(fields):
    return request('POST', '/2/oauth2/token', raw=urllib.parse.urlencode(fields).encode(),
                   content_type='application/x-www-form-urlencoded',
                   basic=(os.environ['OAUTH_2_CLIENT_ID'], os.environ['OAUTH_2_CLIENT_SECRET']))


def store_tokens(state, tokens):
    if not tokens.get('access_token') or not tokens.get('refresh_token'):
        raise ValueError('X did not return both access and refresh tokens; authorize offline.access')
    if tokens.get('scope') and not SCOPES.issubset(set(tokens['scope'].split())):
        raise ValueError('X token is missing a requested scope; authorize again')
    state.update(tokens=tokens, expires_at=time.time() + int(tokens.get('expires_in', 7200)))
    save_state(state)
    checkpoint()


def parse_callback(value, redirect):
    """Normalize copied link wrappers without disclosing the secret callback."""
    value = value.strip().lstrip('\ufeff').strip()
    match = re.fullmatch(r'\[[^\]]*\]\(([^\s]+)\)', value)
    if match:
        value = match.group(1)
    value = value.strip('`<>\"\'').strip()
    if not value:
        raise ValueError('OAUTH_2_AUTHORIZATION_RESPONSE is empty. Save the full browser address AFTER approving X access.')
    try:
        response = urllib.parse.urlsplit(value)
        expected = urllib.parse.urlsplit(redirect)
    except ValueError:
        raise ValueError('The authorization-response secret is not a valid URL. Copy the entire browser address after approving X access.') from None
    if (response.scheme, response.netloc, response.path) != (expected.scheme, expected.netloc, expected.path):
        raise ValueError('OAUTH_2_AUTHORIZATION_RESPONSE must start with ' + redirect +
                         '? and contain code= and state=. Use the browser address AFTER approving X access, '
                         'not the initial x.com authorization link, a token, or the callback address alone. '
                         'Do not paste the secret into chat or logs.')
    params = urllib.parse.parse_qs(response.query)
    if params.get('error'):
        # Only emit standard, allowlisted categories. Never print arbitrary query
        # values or error_description, which could contain sensitive information.
        reasons = {
            'access_denied': 'X did not grant consent. Confirm the intended account and approve a fresh request.',
            'invalid_scope': 'X rejected the requested permissions. Check OAuth scopes and app configuration.',
            'invalid_request': 'X rejected an authorization parameter. Check the client ID, callback registration, and latest authorization link.',
            'unauthorized_client': 'X did not allow this app to use this authorization flow. Check OAuth 2.0 user authentication and client type.',
            'unsupported_response_type': 'X rejected the requested authorization flow.',
            'server_error': 'X reported a server error. Retry authorization with a fresh link.',
            'temporarily_unavailable': 'X reported temporary unavailability. Retry authorization later.',
        }
        category = params['error'][0]
        if category not in reasons:
            raise ValueError('X authorization error: unrecognized category (details withheld). Inspect only the error= value locally; do not share the full callback URL.')
        raise ValueError(f'X authorization error: {category}. {reasons[category]} No authorization code was exchanged.')
    if not params.get('code') or not params.get('state'):
        raise ValueError('Callback URL is missing code= or state=. Copy the ENTIRE address after approving access, including the query string.')
    if len(params['code']) != 1 or len(params['state']) != 1:
        raise ValueError('Callback URL contains duplicate authorization parameters. Start a fresh authorization.')
    return params


def finish_auth():
    state = read_state()
    pending = state.get('pending_auth')
    if not pending or time.time() - pending['created'] > 3600:
        raise ValueError('Missing or old authorization session; run start_auth again')
    params = parse_callback(os.environ.get('OAUTH_2_AUTHORIZATION_RESPONSE', ''), pending['redirect'])
    received_state = params.get('state', [''])[0]
    if not secrets.compare_digest(received_state, pending['state']):
        raise ValueError('Authorization state mismatch; run start_auth again')
    code = params.get('code', [''])[0]
    if not code:
        raise ValueError('Authorization was declined or callback has no code')
    tokens = exchange({'grant_type': 'authorization_code', 'code': code,
                       'redirect_uri': pending['redirect'], 'code_verifier': pending['verifier']})
    state.pop('pending_auth')
    store_tokens(state, tokens)
    me = request('GET', '/2/users/me', token=tokens['access_token'])['data']
    state.update(user_id=me['id'], username=me['username'])
    save_state(state)
    checkpoint()
    summary(f'Connected X account: @{me["username"]}. No video posted.')
    if os.getenv('GITHUB_ACTIONS') == 'true':
        summary('Delete the temporary OAUTH_2_AUTHORIZATION_RESPONSE secret.')


def connect_local():
    """Exchange the callback immediately on this computer, bypassing Actions queues."""
    if os.getenv('GITHUB_ACTIONS') == 'true':
        raise ValueError('connect_local must run on your computer, not a GitHub runner')
    os.environ['PERSIST_TO_GIT'] = 'false'
    os.environ['OAUTH_2_CLIENT_ID'] = (os.getenv('OAUTH_2_CLIENT_ID') or input('X OAuth 2.0 Client ID: ')).strip()
    os.environ['OAUTH_2_CLIENT_SECRET'] = os.getenv('OAUTH_2_CLIENT_SECRET') or getpass.getpass('X OAuth 2.0 Client Secret (hidden): ')
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # The request URL contains a private, one-time authorization code.

        def do_GET(self):
            if urllib.parse.urlsplit(self.path).path != '/callback':
                self.send_error(404)
                return
            self.server.callback = REDIRECT + ('?' + urllib.parse.urlsplit(self.path).query if '?' in self.path else '')
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(b'Callback received. Return to your terminal for the connection result. You may close this tab.')

    with HTTPServer(('127.0.0.1', 8765), CallbackHandler) as server:
        server.timeout, server.callback = 1, None
        save_state({'pending_auth': {'verifier': verifier, 'state': state, 'created': time.time(), 'redirect': REDIRECT}})
        url = authorization_url(os.environ['OAUTH_2_CLIENT_ID'], REDIRECT, state, verifier)
        print('Opening X authorization. Approve access in your browser. No video will be posted.', flush=True)
        print('If the browser does not open, open this link locally: ' + url, flush=True)
        webbrowser.open(url)
        deadline = time.monotonic() + 600
        while server.callback is None and time.monotonic() < deadline:
            server.handle_request()
        if server.callback is None:
            raise ValueError('No callback received within 10 minutes; run connect_local again')
        os.environ['OAUTH_2_AUTHORIZATION_RESPONSE'] = server.callback
        finish_auth()  # Exchange immediately; no dependency installation or runner queue.
    print('Upload this ENCRYPTED file to the same path in your GitHub repository: ' + str(STATE.resolve()), flush=True)
    print('Keep OAUTH_2_CLIENT_SECRET in GitHub identical to the secret entered here. Do not upload any plaintext credentials.', flush=True)


def access_token():
    state = read_state()
    if not state.get('tokens'):
        raise ValueError('Run finish_auth first')
    if time.time() >= state['expires_at'] - 120:
        tokens = exchange({'grant_type': 'refresh_token', 'refresh_token': state['tokens']['refresh_token']})
        # Some providers omit an unchanged refresh token; preserve it in that case.
        tokens.setdefault('refresh_token', state['tokens']['refresh_token'])
        store_tokens(state, tokens)
    return state['tokens']['access_token']


def choose(queue, ledger, now, test_now=False):
    if any(p['status'] == 'pending' for p in ledger['posts']):
        raise ValueError('A previous post has an uncertain outcome. Check X and resolve its pending ledger entry before retrying.')
    today = now.date().isoformat()
    posts = [p for p in ledger['posts'] if p['status'] == 'posted' and p['date'] == today]
    if len(posts) >= 2:
        return None, 'Two videos already posted today'
    if not test_now and now.hour not in (11, 18):
        return None, 'Outside the 11 AM / 6 PM Eastern posting hours'
    slot = f'{today}-{now.hour:02d}'
    if not test_now and any(p['slot'] == slot for p in posts):
        return None, 'This slot is already posted'
    used = {p['drive_file_id'] for p in ledger['posts'] if p['status'] == 'posted'}
    for row in queue['videos']:
        if (row.get('approved') is True and row.get('evergreen') is True
                and row['drive_file_id'] not in used and not row.get('posted_at') and not row.get('x_post_id')):
            caption = row.get('caption', '').strip()
            if not caption or len(caption) > 240 or not caption.isascii() or 'http' in caption.lower():
                raise ValueError(f'Caption must be plain ASCII, 1-240 characters, no links: {row["filename"]}')
            return row, slot
    return None, 'No approved unused videos remain'


def download_video(row, path):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    from draft_drive_video_captions import validate_download
    creds = service_account.Credentials.from_service_account_info(
        json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']),
        scopes=['https://www.googleapis.com/auth/drive.readonly'])
    drive = build('drive', 'v3', credentials=creds, cache_discovery=False)
    file = drive.files().get(fileId=row['drive_file_id'], fields='id,size,md5Checksum,modifiedTime', supportsAllDrives=True).execute()
    if file['modifiedTime'] != row['drive_modified_time']:
        raise ValueError('Video changed since caption approval; regenerate and review it first')
    with path.open('wb') as destination:
        download = MediaIoBaseDownload(destination, drive.files().get_media(fileId=row['drive_file_id']), chunksize=8*1024*1024)
        done = False
        while not done:
            _, done = download.next_chunk(num_retries=3)
    validate_download(path, file)


def upload_video(path, token):
    result = request('POST', '/2/media/upload/initialize', token, payload={
        'media_type': 'video/mp4', 'total_bytes': path.stat().st_size, 'media_category': 'tweet_video'})
    media_id = str(result['data']['id'])
    with path.open('rb') as source:
        index = 0
        for chunk in iter(lambda: source.read(4*1024*1024), b''):
            boundary = 'BTB' + secrets.token_hex(16)
            raw = (f'--{boundary}\r\nContent-Disposition: form-data; name="segment_index"\r\n\r\n{index}\r\n'
                   f'--{boundary}\r\nContent-Disposition: form-data; name="media"; filename="chunk.mp4"\r\n'
                   'Content-Type: application/octet-stream\r\n\r\n').encode() + chunk + f'\r\n--{boundary}--\r\n'.encode()
            request('POST', f'/2/media/upload/{media_id}/append', token, raw=raw,
                    content_type=f'multipart/form-data; boundary={boundary}')
            index += 1
    data = request('POST', f'/2/media/upload/{media_id}/finalize', token)['data']
    deadline = time.monotonic() + 600
    while data.get('processing_info'):
        info = data['processing_info']
        if info['state'] == 'succeeded':
            break
        if info['state'] == 'failed' or time.monotonic() > deadline:
            raise ValueError('X video processing failed or timed out')
        time.sleep(min(30, max(1, int(info.get('check_after_secs', 2)))))
        data = request('GET', '/2/media/upload?' + urllib.parse.urlencode({'command': 'STATUS', 'media_id': media_id}), token)['data']
    return media_id


def post(test_now=False, publish=False):
    queue = json.loads(QUEUE.read_text())
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {'posts': []}
    now = datetime.now(ET)
    row, slot = choose(queue, ledger, now, test_now)
    if not row:
        summary(slot)
        return
    summary(f'Selected: {row["filename"]}\n\n{row["caption"]}')
    if not publish:
        summary('Preview only. No download or X request made.')
        return
    token = access_token()
    # Confirm the authorized account matches the saved identity before publishing.
    me = request('GET', '/2/users/me', token)['data']
    if me['id'] != read_state().get('user_id'):
        raise ValueError('Connected account changed; run authorization again')
    with tempfile.TemporaryDirectory() as tmp:
        raw, video = Path(tmp) / 'source', Path(tmp) / 'video.mp4'
        download_video(row, raw)
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(raw),
                        '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-preset', 'fast',
                        '-crf', '22', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1',
                        '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k',
                        '-movflags', '+faststart', str(video)], check=True)
        media_id = upload_video(video, token)
    entry = {'drive_file_id': row['drive_file_id'], 'date': now.date().isoformat(),
             'slot': slot if not test_now else 'manual-' + secrets.token_hex(8), 'status': 'pending',
             'attempted_at': now.isoformat(), 'media_id': media_id}
    ledger['posts'].append(entry)
    write_json(LEDGER, ledger)
    checkpoint()  # If persistence fails, do not send the post request.
    try:
        response = request('POST', '/2/tweets', token, payload={
            'text': row['caption'], 'media': {'media_ids': [media_id]}})
    except ApiError as error:
        if 400 <= error.status < 500 and error.status != 408:
            entry['status'] = 'rejected'
            write_json(LEDGER, ledger)
            checkpoint()
        raise  # Timeouts/server errors remain pending; do not blindly retry.
    entry.update(status='posted', x_post_id=response['data']['id'], posted_at=datetime.now(ET).isoformat())
    write_json(LEDGER, ledger)
    checkpoint()
    summary('Posted: https://x.com/i/status/' + entry['x_post_id'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=['start_auth', 'finish_auth', 'connect_local', 'preview', 'publish', 'publish_now'], default='preview')
    args = parser.parse_args()
    if args.action == 'connect_local':
        connect_local()
    elif args.action == 'start_auth':
        start_auth()
    elif args.action == 'finish_auth':
        finish_auth()
    else:
        post(test_now=args.action in ('preview', 'publish_now'), publish=args.action != 'preview')


if __name__ == '__main__':
    main()
