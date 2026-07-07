#!/usr/bin/env python3
"""
Refresh the Strava token and print the scopes. Useful for confirming you have activity:read_all.
"""
import os
import re
from dotenv import load_dotenv
import requests

TOKEN_URL = "https://www.strava.com/oauth/token"


def has_activity_read_all(scope: str | None) -> bool:
    if not scope:
        return False
    scopes = {part for part in re.split(r"[\s,]+", scope) if part}
    return "activity:read_all" in scopes


def main() -> None:
    load_dotenv()
    client_id = os.environ.get("STRAVA_CLIENT_ID")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET")
    refresh_token = os.environ.get("STRAVA_REFRESH_TOKEN")
    scope_hint = os.environ.get("STRAVA_ACCESS_TOKEN_SCOPE")
    if not all([client_id, client_secret, refresh_token]):
        raise SystemExit("Missing STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET / STRAVA_REFRESH_TOKEN in env/.env")

    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Failed to refresh token: {resp.status_code} {resp.text}")

    payload = resp.json()
    scope = payload.get("scope") or scope_hint
    print("Scopes:", scope)
    print("Access token refreshed:", bool(payload.get("access_token")))
    print("Refresh token refreshed:", bool(payload.get("refresh_token")))
    print("Expires at:", payload.get("expires_at"))
    if not has_activity_read_all(scope):
        print("If scopes do not include activity:read_all, re-run the OAuth flow with scopes=read,activity:read_all.")


if __name__ == "__main__":
    main()
