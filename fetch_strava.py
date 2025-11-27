#!/usr/bin/env python3
"""
Fetch Strava activities and emit cached JSON/GeoJSON for the heatmap UI.
Requires env vars:
  STRAVA_CLIENT_ID
  STRAVA_CLIENT_SECRET
  STRAVA_REFRESH_TOKEN
Optional:
  STRAVA_ACCESS_TOKEN
  STRAVA_ACCESS_TOKEN_EXPIRES_AT (ISO 8601 or epoch seconds)
"""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import polyline
import requests
from dotenv import load_dotenv

TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
DATA_DIR = Path("data")
RAW_PATH = DATA_DIR / "activities_raw.json"
GEOJSON_PATH = DATA_DIR / "activities.geojson"


def parse_iso_or_epoch(raw: Optional[str]) -> int:
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> Dict[str, Any]:
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to refresh token: {resp.status_code} {resp.text}")
    return resp.json()


def _has_activity_scope(scope_value: Optional[str]) -> bool:
    if not scope_value:
        return False
    scopes = {s.strip() for s in scope_value.split(",")}
    return "activity:read" in scopes or "activity:read_all" in scopes


def get_access_token(env: Dict[str, str]) -> Dict[str, Any]:
    now = int(time.time())
    expires_at = parse_iso_or_epoch(env.get("STRAVA_ACCESS_TOKEN_EXPIRES_AT"))
    access_token = env.get("STRAVA_ACCESS_TOKEN")
    scope_hint = env.get("STRAVA_ACCESS_TOKEN_SCOPE")
    if access_token and expires_at and expires_at > now + 300:
        return {
            "access_token": access_token,
            "refresh_token": env.get("STRAVA_REFRESH_TOKEN"),
            "expires_at": expires_at,
            "scope": scope_hint,
        }

    refreshed = refresh_access_token(
        env["STRAVA_CLIENT_ID"],
        env["STRAVA_CLIENT_SECRET"],
        env["STRAVA_REFRESH_TOKEN"],
    )
    scope = refreshed.get("scope") or scope_hint
    return {
        "access_token": refreshed["access_token"],
        "refresh_token": refreshed.get("refresh_token", env.get("STRAVA_REFRESH_TOKEN")),
        "expires_at": refreshed.get("expires_at", 0),
        "scope": scope,
    }


def fetch_activities(access_token: str, after_ts: Optional[int]) -> List[Dict[str, Any]]:
    activities: List[Dict[str, Any]] = []
    page = 1
    headers = {"Authorization": f"Bearer {access_token}"}
    while True:
        params = {"page": page, "per_page": 200}
        if after_ts:
            params["after"] = after_ts
        resp = requests.get(ACTIVITIES_URL, headers=headers, params=params, timeout=30)
        if resp.status_code == 401:
            raise RuntimeError(
                "Failed to fetch activities: 401 Unauthorized. Strava token is missing required scope "
                "(need at least activity:read, often activity:read_all for private activities). "
                "Re-run the OAuth flow and update STRAVA_REFRESH_TOKEN/STRAVA_ACCESS_TOKEN."
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to fetch activities: {resp.status_code} {resp.text}")
        batch = resp.json()
        if not batch:
            break
        activities.extend(batch)
        page += 1
    return activities


def activities_to_geojson(activities: List[Dict[str, Any]]) -> Dict[str, Any]:
    features: List[Dict[str, Any]] = []
    for act in activities:
        summary_poly = act.get("map", {}).get("summary_polyline")
        if not summary_poly:
            continue
        try:
            coords = polyline.decode(summary_poly)
        except (ValueError, TypeError):
            continue
        line = [[lon, lat] for lat, lon in coords]  # GeoJSON expects [lon, lat]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": act.get("id"),
                    "name": act.get("name"),
                    "type": act.get("type"),
                    "start_date": act.get("start_date"),
                    "distance_m": act.get("distance"),
                    "moving_time_s": act.get("moving_time"),
                    "elapsed_time_s": act.get("elapsed_time"),
                    "elevation_gain_m": act.get("total_elevation_gain"),
                },
                "geometry": {"type": "LineString", "coordinates": line},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def parse_after(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    return int(datetime.fromisoformat(value).timestamp())


def parse_activity_start_ts(activity: Dict[str, Any]) -> Optional[int]:
    start = activity.get("start_date")
    if not start:
        return None
    try:
        return int(datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def latest_start_ts(activities: List[Dict[str, Any]]) -> Optional[int]:
    latest = None
    for act in activities:
        ts = parse_activity_start_ts(act)
        if ts is None:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def main() -> None:
    load_dotenv()
    env = {k: v for k, v in os.environ.items() if k.startswith("STRAVA_")}
    required = ["STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN"]
    missing = [key for key in required if key not in env]
    if missing:
        raise SystemExit(f"Missing required env vars: {', '.join(missing)}")

    parser = argparse.ArgumentParser(description="Fetch Strava activities and emit GeoJSON for heatmaps.")
    parser.add_argument(
        "--after",
        help="Fetch activities after this date (YYYY-MM-DD) or epoch seconds.",
    )
    args = parser.parse_args()

    cached_activities: List[Dict[str, Any]] = []
    if RAW_PATH.exists():
        try:
            cached_activities = json.loads(RAW_PATH.read_text(encoding="utf-8"))
        except Exception:
            cached_activities = []

    after_ts = parse_after(args.after)
    auto_after = None
    now_ts = int(time.time())
    if after_ts is None and cached_activities:
        auto_after = latest_start_ts(cached_activities)
        after_ts = auto_after
    if after_ts and after_ts > now_ts:
        # Guard against future timestamps (e.g., if cache has future-dated activities)
        print(f"Cached latest activity is in the future ({after_ts}); capping to now.")
        after_ts = now_ts
    if after_ts:
        print(f"Incremental fetch after {after_ts} (auto-from-cache: {auto_after is not None})")

    tokens = get_access_token(env)
    if not _has_activity_scope(tokens.get("scope")):
        raise SystemExit(
            "Current token is missing activity scope (need activity:read or activity:read_all). "
            "Re-run OAuth with scopes=read,activity:read_all and update STRAVA_REFRESH_TOKEN / STRAVA_ACCESS_TOKEN."
        )
    new_activities = fetch_activities(tokens["access_token"], after_ts)
    print(f"Fetched {len(new_activities)} new activities.")

    # Merge with cache by activity id
    merged: Dict[Any, Dict[str, Any]] = {}
    for act in cached_activities:
        act_id = act.get("id")
        if act_id is not None:
            merged[act_id] = act
    for act in new_activities:
        act_id = act.get("id")
        if act_id is not None:
            merged[act_id] = act

    merged_list = list(merged.values())
    merged_list.sort(key=lambda a: parse_activity_start_ts(a) or 0, reverse=True)

    save_json(RAW_PATH, merged_list)
    geojson = activities_to_geojson(merged_list)
    save_json(GEOJSON_PATH, geojson)

    print(f"Total cached activities: {len(merged_list)}")
    print(f"Wrote raw activities to {RAW_PATH}")
    print(f"Wrote GeoJSON to {GEOJSON_PATH}")
    if tokens.get("expires_at"):
        expires_iso = datetime.utcfromtimestamp(tokens["expires_at"]).isoformat() + "Z"
        print(f"Access token expires at {expires_iso}. Keep your refresh token safe.")


if __name__ == "__main__":
    main()
