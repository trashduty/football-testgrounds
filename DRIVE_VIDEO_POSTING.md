# Drive videos to X

The caption workflow creates drafts. The separate **Post Drive Videos to X** workflow connects the account, previews a reviewed item, and publishes videos. Google Drive remains the video store.

## One-time account connection

1. Keep the existing secrets `OAUTH_2_CLIENT_ID`, `OAUTH_2_CLIENT_SECRET`, and `GOOGLE_SERVICE_ACCOUNT_JSON`.
2. In the X developer app's OAuth 2.0 user authentication settings, add this exact callback URI: `http://127.0.0.1:8765/callback`. Use the confidential **Web App, Automated App or Bot** client type. The website URL can remain `https://www.btb-analytics.com`.
3. In GitHub Actions, open **Post Drive Videos to X**, click **Run workflow**, choose `start_auth`, and run on `main`.
4. Wait for that run to succeed. In its Summary, open the authorization link while signed into the intended X account and approve the requested permissions.
5. Your browser will redirect to the loopback callback and may show a connection error. That is expected: no web server is running there. Copy the ENTIRE URL from the address bar, including `code` and `state`, into a new repository Actions secret called `OAUTH_2_AUTHORIZATION_RESPONSE`. Do not paste it in chat, source files, or workflow inputs.
6. Immediately run the workflow again with action `finish_auth`. It should report the connected X username. If the code expired, repeat from `start_auth` and replace the response secret.
7. Delete `OAUTH_2_AUTHORIZATION_RESPONSE` after success. Tokens are now saved encrypted and automatically refreshed.

The client ID and secret identify the app; this authorization grants permission to the posting account. The workflow requests `tweet.read tweet.write users.read media.write offline.access`.

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
