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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from analysis_metrics import build_fitness_analysis

TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
ACTIVITY_STREAMS_URL = "https://www.strava.com/api/v3/activities/{activity_id}/streams"
DATA_DIR = Path("data")
RAW_PATH = DATA_DIR / "activities_raw.json"
GEOJSON_PATH = DATA_DIR / "activities.geojson"
FITNESS_ANALYSIS_PATH = DATA_DIR / "fitness_analysis.json"
STREAMS_DIR = DATA_DIR / "activity_streams"
STREAM_FETCH_KEYS = ["heartrate", "time", "distance", "velocity_smooth", "moving"]
DEFAULT_STREAM_BACKFILL_LIMIT = 100


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


def fetch_activity_streams(access_token: str, activity_id: int) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"keys": ",".join(STREAM_FETCH_KEYS), "key_by_type": "true"}
    url = ACTIVITY_STREAMS_URL.format(activity_id=activity_id)
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code == 401:
        raise RuntimeError(
            "Failed to fetch activity streams: 401 Unauthorized. Strava token is missing required scope "
            "(need at least activity:read, often activity:read_all for private activities). "
            "Re-run the OAuth flow and update STRAVA_REFRESH_TOKEN/STRAVA_ACCESS_TOKEN."
        )
    if resp.status_code == 429:
        raise RuntimeError("Rate limited while fetching activity streams (429 Too Many Requests).")
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch activity streams for {activity_id}: {resp.status_code} {resp.text}")
    return resp.json()


def activities_to_geojson(activities: List[Dict[str, Any]]) -> Dict[str, Any]:
    import polyline

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


def activity_stream_path(activity_id: int, streams_dir: Path = STREAMS_DIR) -> Path:
    return streams_dir / f"{activity_id}.json"


def build_stream_cache_payload(activity_id: int, streams: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "activity_id": activity_id,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "keys_requested": STREAM_FETCH_KEYS,
        "streams": streams,
    }


def has_summary_heartrate(activity: Dict[str, Any]) -> bool:
    return activity.get("average_heartrate") is not None or activity.get("max_heartrate") is not None


def select_stream_backfill_candidates(
    activities: List[Dict[str, Any]],
    limit: int,
    streams_dir: Path = STREAMS_DIR,
) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []
    candidates: List[Dict[str, Any]] = []
    for act in activities:
        if act.get("type") != "Run":
            continue
        act_id = act.get("id")
        if act_id is None:
            continue
        if not has_summary_heartrate(act):
            continue
        if activity_stream_path(int(act_id), streams_dir).exists():
            continue
        candidates.append(act)
        if len(candidates) >= limit:
            break
    return candidates


def cache_missing_activity_streams(
    activities: List[Dict[str, Any]],
    access_token: str,
    limit: int = DEFAULT_STREAM_BACKFILL_LIMIT,
    streams_dir: Path = STREAMS_DIR,
) -> Dict[str, Any]:
    candidates = select_stream_backfill_candidates(activities, limit, streams_dir)
    cached = 0
    stopped_for_rate_limit = 0
    cached_dates: List[str] = []
    for act in candidates:
        act_id = int(act["id"])
        try:
            payload = build_stream_cache_payload(act_id, fetch_activity_streams(access_token, act_id))
        except RuntimeError as err:
            if "429" in str(err):
                print(f"Detailed stream backfill stopped after {cached} activities: {err}")
                stopped_for_rate_limit = 1
                break
            print(f"Skipping detailed streams for activity {act_id}: {err}")
            continue
        save_json(activity_stream_path(act_id, streams_dir), payload)
        cached += 1
        start_date = (act.get("start_date") or "")[:10]
        if start_date:
            cached_dates.append(start_date)
    return {
        "requested": len(candidates),
        "cached": cached,
        "stopped_for_rate_limit": stopped_for_rate_limit,
        "cached_start_date": min(cached_dates) if cached_dates else None,
        "cached_end_date": max(cached_dates) if cached_dates else None,
    }


def cache_activity_stream_by_id(
    activity_id: int,
    access_token: str,
    streams_dir: Path = STREAMS_DIR,
) -> Path:
    payload = build_stream_cache_payload(activity_id, fetch_activity_streams(access_token, activity_id))
    stream_path = activity_stream_path(activity_id, streams_dir)
    save_json(stream_path, payload)
    return stream_path


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
    parser.add_argument(
        "--stream-backfill-limit",
        type=int,
        default=DEFAULT_STREAM_BACKFILL_LIMIT,
        help=(
            "After refreshing summaries, fetch detailed streams for up to this many most-recent "
            "Run activities that have summary HR data but no cached stream file."
        ),
    )
    parser.add_argument(
        "--stream-activity-id",
        type=int,
        help="Fetch and cache detailed streams for one activity id, then exit.",
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
    if args.stream_activity_id is not None:
        stream_path = cache_activity_stream_by_id(args.stream_activity_id, tokens["access_token"])
        print(f"Wrote detailed activity streams to {stream_path}")
        return

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
    stream_backfill = cache_missing_activity_streams(
        merged_list,
        tokens["access_token"],
        limit=max(args.stream_backfill_limit, 0),
    )
    fitness_analysis = build_fitness_analysis(merged_list, streams_dir=STREAMS_DIR)
    save_json(FITNESS_ANALYSIS_PATH, fitness_analysis)

    print(f"Total cached activities: {len(merged_list)}")
    print(f"Wrote raw activities to {RAW_PATH}")
    print(f"Wrote GeoJSON to {GEOJSON_PATH}")
    print(f"Wrote fitness analysis to {FITNESS_ANALYSIS_PATH}")
    print(
        "Detailed HR stream cache: "
        f"requested {stream_backfill['requested']}, cached {stream_backfill['cached']}, "
        f"rate_limited={bool(stream_backfill['stopped_for_rate_limit'])}"
    )
    if stream_backfill["cached_start_date"] and stream_backfill["cached_end_date"]:
        print(
            "Detailed HR stream dates now available: "
            f"{stream_backfill['cached_start_date']} to {stream_backfill['cached_end_date']}"
        )
    else:
        print("Detailed HR stream dates now available: none newly cached")
    if tokens.get("expires_at"):
        expires_iso = datetime.fromtimestamp(tokens["expires_at"], timezone.utc).isoformat().replace("+00:00", "Z")
        print(f"Access token expires at {expires_iso}. Keep your refresh token safe.")


if __name__ == "__main__":
    main()
