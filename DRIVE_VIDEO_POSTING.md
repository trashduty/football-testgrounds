# Drive videos to X

The caption workflow creates drafts. The separate **Post Drive Videos to X** workflow connects the account, previews a reviewed item, and publishes videos. Google Drive remains the video store.

## One-time account connection

1. Store the public OAuth 2.0 Client ID as a repository Actions **variable** named `OAUTH_2_CLIENT_ID`. If it was previously a secret, copy the ID from the X developer console into the variable and delete the old Client ID secret. GitHub masks secrets in summaries, which can break the authorization link. Keep `OAUTH_2_CLIENT_SECRET` and `GOOGLE_SERVICE_ACCOUNT_JSON` as **secrets**. The Client ID must be the OAuth 2.0 Client ID from the same X app, not the OAuth 1.0 Consumer/API Key.
2. In the X developer app's OAuth 2.0 user authentication settings, add this exact callback URI: `http://127.0.0.1:8765/callback`. Use the confidential **Web App, Automated App or Bot** client type. The website URL can remain `https://www.btb-analytics.com`.
3. In GitHub Actions, open **Post Drive Videos to X**, click **Run workflow**, choose `start_auth`, and run on `main`.
4. Wait for that run to succeed. In its Summary, open the authorization link while signed into the intended X account and approve the requested permissions.
5. Your browser will redirect to the loopback callback and may show a connection error. That is expected: no web server is running there. Copy the ENTIRE URL from the address bar, including `code` and `state`, into a new repository Actions secret called `OAUTH_2_AUTHORIZATION_RESPONSE`. Do not paste it in chat, source files, or workflow inputs.
6. Immediately run the workflow again with action `finish_auth`. It should report the connected X username. If the code expired, repeat from `start_auth` and replace the response secret.
7. Delete `OAUTH_2_AUTHORIZATION_RESPONSE` after success. Tokens are now saved encrypted and automatically refreshed.

The client ID and secret identify the app; this authorization grants permission to the posting account. The workflow requests `tweet.read tweet.write users.read media.write offline.access`.

## Immediate local connection (recommended if Actions token exchange fails)

A successful browser approval followed by HTTP 400 at the token endpoint may involve an expired or reused authorization code, mismatched credentials, or another token parameter. The workflow now reports safe OAuth error categories, and skips video dependency installation during authentication.

To avoid the runner queue between approval and exchange, run the connection on your own computer:

1. Add `http://127.0.0.1:8765/callback` to the SAME confidential Web App's callback URLs.
2. Download `scripts/post_drive_video_to_x.py` from this repository. Install Python's `cryptography` package before connecting.
3. From a local terminal run `python post_drive_video_to_x.py --action connect_local` (on Windows, `py` can replace `python`).
4. Enter the OAuth 2.0 Client ID and Client Secret when prompted. The secret is hidden. Use the same secret stored as `OAUTH_2_CLIENT_SECRET` in GitHub. A browser opens; approve access. A temporary listener on 127.0.0.1 receives the callback and exchanges it immediately.
5. Wait for the terminal to confirm the connected X username. Upload ONLY the generated encrypted file `outputs/drive_videos/x_oauth.enc` to that exact path on `main`, replacing the earlier pending-authorization file.
6. You do NOT need `finish_auth` or the `OAUTH_2_AUTHORIZATION_RESPONSE` secret with this method. Delete that temporary secret if it remains.
7. Run `preview`, then `publish_now` from GitHub Actions as described below.

No live post is created by local authorization. No Google credentials are needed locally. Keep plaintext credentials and callback URLs out of GitHub source and chat. The terminal prints the absolute path of the encrypted file to upload. If the port is occupied or authorization fails, close the program using that port or correct the reported issue and run `connect_local` again.

## First post and schedule

1. In `outputs/drive_videos/queue.json`, review a caption and set only that video's `approved` to the JSON boolean `true`.
2. Run action `preview` to see the next eligible filename and exact caption. This makes no X API request.
3. Run action `publish_now` to publish ONE approved unused video immediately, outside the scheduled window. It uses the real caption with no test suffix. It counts toward the daily cap and will not be posted again.
4. After checking the post on X, add a repository Actions VARIABLE (not a secret) named `DRIVE_VIDEO_POSTING_ENABLED` with value `true`.

Scheduled runs post at 11 AM and 6 PM America/New_York, adjusting for daylight saving time. GitHub may delay runs; the script permits posting during the corresponding hour and skips runs delayed beyond it. It does not catch up missed slots. Maximum two successful video posts per local day, including manual tests. Each Drive file ID posts only once. If the approved queue runs out, the workflow does nothing. Approve more videos to resume. Set the enable variable to `false` to pause scheduled publishing.

Manual actions always work regardless of the enable variable. `publish` obeys the time window; `publish_now` bypasses the window but still requires approval and respects the daily cap.

## State and recovery

- `outputs/drive_videos/x_oauth.enc` contains authenticated encryption of OAuth credentials, pending PKCE authorization, and connected account identity. The encryption key is derived from the high-entropy X client secret. Plaintext tokens are never committed or printed. Changing the client secret requires reauthorizing; remove the old encrypted file first if it can no longer be decrypted.
- `outputs/drive_videos/posted.json` is the posting ledger. It is separate from the draft queue, so refreshing drafts cannot erase posting history.
- Both workflows share one concurrency group. The publisher checkpoints token rotation and posting reservations to `main` before continuing. GitHub Actions must have repository contents write permission.
- A timeout or server error during post creation leaves a `pending` reservation and blocks further posts. Check X before editing that record: if the post exists, set `status` to `posted` and add its `x_post_id`; if it definitively does not exist, set `status` to `rejected`. Do not clear a reservation blindly.
- If a video changes in Drive after caption generation, posting stops until its caption is regenerated and approved again.
- Videos are normalized to MP4/H.264/AAC on the runner, uploaded in chunks, and awaited until processing completes. No video is stored in GitHub. API credits and the account's video entitlements still apply.

The live authorization and post test require your account consent. No live post is created by installing these files or running `start_auth`, `finish_auth`, or `preview`.
