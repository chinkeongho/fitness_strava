# Strava Activity Heatmap - Architecture & Specs

## Overview
This project turns Strava activity data into a local cache plus an interactive web UI with:
- a route heatmap and activity dashboard
- per-run detailed HR/pace inspection when stream data is available
- a separate analysis page backed by `data/fitness_analysis.json`

The system is composed of a Python data fetcher, a minimal Node.js HTTP server, and a static frontend (HTML/CSS/JS).

## Architecture
- Data fetcher: `fetch_strava.py` pulls activities from the Strava API, merges them with the local cache, backfills recent missing detailed streams, and emits GeoJSON plus longitudinal analysis JSON.
- Cache layer: JSON, GeoJSON, and per-activity stream files in `data/`.
- Web server: `server.js` serves static files, exposes `/refresh`, keeps `/api/*` protected via `API_KEY` when enabled, and exposes `/activity-stream` for dashboard-facing on-demand stream fetch/caching.
- Frontend UI:
  - `web/index.html` renders the heatmap dashboard and per-run HR/pace inspection
  - `web/analysis.html` renders the fitness/efficiency charts

### Architecture Diagram (Logical)
```text
                +------------------+
                |    Strava API    |
                +------------------+
                         ^
                         | OAuth + Activities
                         |
                 +-------------------+
                 | fetch_strava.py   |
                 |  (Python fetcher) |
                 +-------------------+
                         |
                         | writes
                         v
                 +-------------------+
                 |      data/        |
                 | activities_raw    |
                 | activities.geojson|
                 | activity_streams/ |
                 | fitness_analysis  |
                 +-------------------+
                         ^
                         | HTTP GET
                         |
+-----------+     +-------------------+     +---------------------+
| Browser   |<--->|     server.js     |<--->| zenquotes (proxy)   |
| web UI    |     | static + /refresh |     | via /quote          |
+-----------+     +-------------------+     +---------------------+
```

## Data Flow
1. OAuth tokens (refresh/access) live in environment variables.
2. `fetch_strava.py` refreshes the access token as needed.
3. Activities are fetched from Strava and merged into `data/activities_raw.json`.
4. `data/activities.geojson` is generated from the merged activities.
5. Recent missing detailed HR-enabled runs are cached under `data/activity_streams/<activity_id>.json`.
6. `data/fitness_analysis.json` is generated from a stream-preferred, summary-fallback policy.
7. The dashboard and analysis page load the relevant cache files to render maps, stats, run inspection, and trends.
8. The UI can trigger `POST /refresh` to pull new data and reload the cache.

### Sequence Flow: Initial Page Load
```text
Browser -> server.js: GET /web/
server.js -> Browser: index.html + assets
Browser -> server.js: GET /data/activities.geojson?ts=...
Browser -> server.js: GET /data/activities_raw.json?ts=...
server.js -> Browser: cached GeoJSON + raw JSON
Browser: render map layers, stats, calendar, charts
Browser -> server.js: GET /quote (optional)
server.js -> zenquotes: GET /api/random
zenquotes -> server.js: quote payload
server.js -> Browser: quote JSON
Browser: render motivation block
```

### Sequence Flow: Refresh Button
```text
Browser -> server.js: POST /refresh
server.js -> fetch_strava.py: spawn process
fetch_strava.py -> Strava API: refresh token (if needed)
fetch_strava.py -> Strava API: GET /athlete/activities (paged)
Strava API -> fetch_strava.py: activities
fetch_strava.py -> Strava API: GET /activities/{id}/streams (bounded recent backfill)
fetch_strava.py -> data/: write activities_raw.json + activities.geojson + activity_streams/* + fitness_analysis.json
fetch_strava.py -> server.js: exit 0, stdout
server.js -> Browser: 200 { status: "ok", output }
Browser -> server.js: GET /data/activities.geojson?ts=...
Browser -> server.js: GET /data/activities_raw.json?ts=...
Browser -> server.js: GET /data/fitness_analysis.json?ts=...
server.js -> Browser: updated cached files
Browser: re-render dashboard + analysis data
```

## Components

### 1) Fetcher (Python)
- Entry point: `fetch_strava.py`.
- Responsibilities:
  - Refresh access tokens using `STRAVA_REFRESH_TOKEN`.
  - Pull paginated activities from Strava.
  - Merge with cached activities by activity id.
  - Backfill detailed per-activity stream data for recent missing HR-enabled runs.
  - Emit raw JSON, GeoJSON, and analysis outputs.
- Outputs:
  - `data/activities_raw.json` (array of Strava activity objects).
  - `data/activities.geojson` (FeatureCollection of LineString routes).
  - `data/activity_streams/<activity_id>.json`
  - `data/fitness_analysis.json`
- Incremental behavior:
  - If cache exists and `--after` is omitted, uses the latest cached activity timestamp.
  - Guards against future timestamps when computing `after`.
- Required env vars:
  - `STRAVA_CLIENT_ID`
  - `STRAVA_CLIENT_SECRET`
  - `STRAVA_REFRESH_TOKEN`
- Optional env vars:
  - `STRAVA_ACCESS_TOKEN`
  - `STRAVA_ACCESS_TOKEN_EXPIRES_AT` (ISO 8601 or epoch seconds)
  - `STRAVA_ACCESS_TOKEN_SCOPE`

### 2) Server (Node)
- Entry point: `server.js`.
- Responsibilities:
  - Serves static files from repo root and `/web/`.
  - `POST /refresh` runs `fetch_strava.py` to update the cache.
  - `GET /activity-stream?id=<activity_id>` returns a cached stream file or fetches and caches it on demand for the dashboard.
  - `GET /quote` proxies a quote API to avoid browser CORS issues.
- Runtime behavior:
  - Chooses Python runtime in order: `.venv/bin/python3`, `FETCH_PYTHON`, `python3`.
  - Serializes refresh requests; returns 429 if a refresh is in flight.
  - Serves `index.html` for `/` (redirects to `/web/`).
- Env vars:
  - `PORT` (default 8000)
  - `FETCH_PYTHON` (optional override for Python path)

### 3) Frontend (Static)
- Entry points:
  - `web/index.html`
  - `web/analysis.html`
- Responsibilities:
  - Dashboard:
    - load `data/activities.geojson` and `data/activities_raw.json`
    - render a Leaflet map with heat and line layers
    - build a 12-month calendar heatmap of daily minutes
    - compute stats and latest route
    - inspect individual runs with detailed HR/pace if a stream is cached or fetched on demand
  - Analysis:
    - load `data/fitness_analysis.json`
    - render a combined multi-zone fitness chart and a separate efficiency chart
    - show methodology, caveats, and source mix
    - support mouse-driven zoom with a visible selection band, auto-filled start/end fields, and reset/double-click reset
  - Display a daily motivation quote (local fallback or `/quote` proxy) on the dashboard.
- Libraries:
  - Leaflet + Leaflet.heat for mapping layers.
  - Chart.js for the cumulative minutes chart.
- Cache-busting:
  - Requests append a timestamp query param to avoid stale data.

## Data Formats

### `data/activities_raw.json`
- JSON array of Strava activity objects, sorted by start_date descending.
- Used by the UI for stats, calendar minutes, and latest activity details.

### `data/activities.geojson`
- GeoJSON FeatureCollection.
- Each feature:
  - `geometry.type`: `LineString`
  - `geometry.coordinates`: `[lon, lat]` pairs
  - `properties` include:
    - `id`, `name`, `type`, `start_date`, `distance_m`, `moving_time_s`, `elapsed_time_s`, `elevation_gain_m`

### `data/activity_streams/<activity_id>.json`
- JSON payload containing:
  - `activity_id`
  - `fetched_at`
  - `keys_requested`
  - `streams` keyed by Strava stream name (`heartrate`, `time`, `distance`, `velocity_smooth`, `moving`)
- Used by the dashboard’s per-run detailed HR/pace inspector and by analysis generation when stream data is available.

### `data/fitness_analysis.json`
- JSON artifact containing:
  - `methodology_version`
  - `source_policy`
  - `comparability_rule`
  - `coverage`
  - `series`
  - `caveats`
- Used by `web/analysis.html`.

## API / Routes
- `GET /` -> `index.html` (redirects to `/web/`).
- `GET /web/` -> UI.
- `GET /web/analysis.html` -> analysis UI.
- `GET /data/activities.geojson` -> heatmap source.
- `GET /data/activities_raw.json` -> stats source.
- `GET /data/fitness_analysis.json` -> analysis source.
- `POST /refresh` -> runs fetcher, returns JSON `{ status, output }`.
- `GET /activity-stream?id=<activity_id>` -> dashboard-facing detailed stream route; returns cached stream JSON or fetches it on demand.
- `GET /quote` -> returns JSON payload from zenquotes proxy.
- `GET /api/latest` -> returns the latest cached activity.
- `GET /api/date?date=YYYY-MM-DD` -> returns activities for the given date.

### API Auth (External Calls)
- Set `API_KEY` on the server to require authentication.
- Supply the key via `X-API-Key` header or `api_key` query param.
- If `API_KEY` is unset, the API routes are open.

Notes:
- `/activity-stream` is intentionally outside the `API_KEY`-protected `/api/*` namespace so the dashboard can fetch per-run detailed streams without embedding the key in the page.
- `/api/*` remains protected when `API_KEY` is set.

Example (bash):
```bash
export API_KEY="change-me-please"
node server.js
```

Example (`.env`):
```text
API_KEY=change-me-please
```

### API Examples
```bash
curl -H "X-API-Key: YOUR_KEY" http://localhost:8000/api/latest
curl "http://localhost:8000/api/date?date=2024-01-15&api_key=YOUR_KEY"
```

### API Response Schemas
```json
// GET /api/latest
{
  "latest": {
    "id": 1234567890,
    "name": "Morning Run",
    "type": "Run",
    "start_date": "2024-01-15T07:21:33Z",
    "distance": 10532.4,
    "moving_time": 3120,
    "elapsed_time": 3342,
    "total_elevation_gain": 184.5,
    "...": "other Strava fields"
  }
}
```

```json
// GET /api/date?date=YYYY-MM-DD
{
  "date": "2024-01-15",
  "activities": [
    {
      "id": 1234567890,
      "name": "Morning Run",
      "type": "Run",
      "start_date": "2024-01-15T07:21:33Z",
      "distance": 10532.4,
      "moving_time": 3120,
      "elapsed_time": 3342,
      "total_elevation_gain": 184.5,
      "...": "other Strava fields"
    }
  ]
}
```

Notes:
- `latest` is `null` when no cached activities exist.
- `activities` is an empty array when no activities match the date.

## Operations

### Local Run
- Fetch data:
  - `python fetch_strava.py --after 2024-01-01`
- Serve UI:
  - `npm run serve` (or `node server.js`).
  - analysis page: `http://localhost:8000/web/analysis.html`

### Optional Helpers
- `scripts/start_local.sh` and `scripts/deploy_local.sh` bootstrap local env + fetch + serve.
- `scripts/deploy.sh` provisions a remote host, sets up systemd + nginx, and enables HTTPS.

## Security & Privacy
- Keep `.env` out of git and rotate tokens if leaked.
- The UI uses local cache files; do not publish raw GPS data unless you intend to share it.
- Strava scopes must include `activity:read_all` if private activities are desired.

## Known Constraints
- Requires valid Strava OAuth tokens and a refresh token with the correct scopes.
- Map rendering depends on external CDN assets (Leaflet, Chart.js, fonts).
- Quote proxy relies on `https://zenquotes.io/api/random` availability.

## Extensibility
- Add filters (sport, date range) in `web/index.html` UI logic.
- Introduce new cache outputs (e.g., per-sport stats) in `fetch_strava.py`.
- Replace the quote source by modifying `/quote` in `server.js`.
