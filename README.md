# Strava Activity Heatmap

Turns your Strava activities into an interactive heatmap so you can explore where you've run, ridden, hiked, or skied.

![Strava heatmap screenshot](static/screenshot.png)

## What it does
- Pulls activities from the Strava API using your own OAuth app and refresh token.
- Expands the encoded activity polylines into GeoJSON and merges them into a heat layer.
- Keeps a local cache so subsequent syncs are incremental and stay within Strava rate limits.
- Ships an interactive map UI (desktop and mobile friendly) with heat/line toggles.
- Can export a static bundle for sharing without exposing your Strava credentials.

## Prerequisites
- Strava account and an API application (create one at https://www.strava.com/settings/api).
- Refresh token with `read_all` and `activity:read_all` scope for the athlete whose data you want to plot.
- Runtime: use the language/tooling defined in this repo; the examples below assume a Python-based fetcher plus a web front end.

## Configure Strava access
1. Create a Strava API app. Set the Authorization Callback Domain to `localhost` (or the host you plan to use).
2. Authorize your account to get a one-time code (replace placeholders):
   ```
   https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&response_type=code&redirect_uri=http://localhost:3000/auth/strava&approval_prompt=force&scope=read,activity:read_all
   ```
3. Exchange the code for a long-lived refresh token:
   ```
   curl -X POST https://www.strava.com/oauth/token \
     -d client_id=$STRAVA_CLIENT_ID \
     -d client_secret=$STRAVA_CLIENT_SECRET \
     -d code=THE_CODE_YOU_GOT \
     -d grant_type=authorization_code
   ```
   Save the `refresh_token` from the response.
4. Add your secrets to a `.env` (or your secret manager):
   ```
   STRAVA_CLIENT_ID=xxxx
   STRAVA_CLIENT_SECRET=xxxx
   STRAVA_REFRESH_TOKEN=xxxx
   PORT=3000
   ```
   Keep `.env` out of git. Tokens in this repo are sample values only; rotate real tokens if they were shared.

## Running locally
The workflow is fetch -> render -> view.
- Install deps: `python -m pip install -r requirements.txt`.
- Pull activities and cache them locally: `python fetch_strava.py --after 2024-01-01` (writes `data/activities_raw.json` and `data/activities.geojson`). If `--after` is omitted and a cache exists, the script auto-uses the latest cached start_date to fetch incrementally.
- Serve the heatmap UI via the Node server (static + quote proxy): `npm install` then `npm run serve` (uses PORT env var, default 8000) and open `http://localhost:${PORT:-8000}/web/`.
- One-shot helper: `AFTER=2024-01-01 ./scripts/deploy_local.sh` (auto-creates `.venv`, installs deps, loads `.env`, fetches, then serves on `http://localhost:${PORT:-8000}/web/` via `node server.js`).

### Refreshing Strava tokens / scopes
- Build an auth URL: `python scripts/strava_auth_helper.py` (uses `STRAVA_CLIENT_ID/SECRET` from `.env`).
- Open the URL, approve with scopes `read,activity:read_all`, then copy the `code` from the redirect.
- Exchange the code: `python scripts/strava_auth_helper.py --exchange PASTE_CODE_HERE`
- Paste the printed `STRAVA_ACCESS_TOKEN`, `STRAVA_ACCESS_TOKEN_EXPIRES_AT`, and `STRAVA_REFRESH_TOKEN` into `.env`.
- Verify scopes quickly: `python scripts/check_token_scopes.py` (should include `activity:read_all`).
- One-shot version: `python scripts/bootstrap_strava_token.py` starts a local listener, opens the auth URL, captures the code automatically, exchanges it, and writes tokens into `.env`. Use `--redirect-uri` to match your Strava app settings if you changed it (default `http://localhost:3000/auth/strava`).

## Remote deploy
- Ensure your `.env` has valid STRAVA tokens (with `activity:read_all`) and is present locally so it can be synced.
- Deploy over SSH (rsync + remote venv + fetch + serve):  
`./scripts/deploy.sh --host sentinan-dsp --port 8020 --user ubuntu --path /home/ubuntu/strava-heatmap --after 2024-01-01 --start-server 1 --server-name fitness.sentinan.com`  
Positional fallback: `./scripts/deploy.sh sentinan-dsp 8020` (defaults: host=sentinan-dsp, user=ubuntu, path=/home/ubuntu/strava-heatmap, port=8020, server_name=fitness.sentinan.com).  
On the remote it will rsync the project, build a venv, run `fetch_strava.py`, install npm deps, set up systemd (`strava-heatmap.service`) running `node server.js` on port 8020 (serves static + /quote proxy), and configure nginx on port 80 to proxy to 127.0.0.1:8020.  
- Optional HTTPS (Let's Encrypt via nginx plugin): add `--enable-ssl 1 --ssl-email you@example.com` when deploying (requires DNS for `--server-name` pointing at the host and sudo to install certbot). The script installs certbot if missing, obtains/renews a cert for the server name, and configures HTTP->HTTPS redirects.
- After deploy with server start, open `http://your.server/web/` (or https if enabled). The data folder lives under `REMOTE_PATH/data/`. Logs are in the journal for the systemd service.

## Data flow
- Strava API -> local cache (JSON/GeoJSON) -> heatmap layers -> web UI.
- Incremental syncs help stay under Strava rate limits; keep cached data out of git.
- Keep your Strava privacy zones enabled and avoid publishing raw GPS tracks.

## Troubleshooting
- `401 Unauthorized`: refresh tokens expire if access is revoked; re-run the OAuth flow.
- Missing paths: ensure your `.env` is loaded before running the fetcher.
- Empty map: verify activities have GPS streams.

## Roadmap
- Sport/date filters and activity type toggles.
- CI check to keep secrets and caches out of commits.
- Optional static export for sharing without credentials.
