#!/usr/bin/env python3
"""
Refresh the Strava token and print the scopes. Useful for confirming you have activity:read_all.
"""
import os
from dotenv import load_dotenv
import requests

TOKEN_URL = "https://www.strava.com/oauth/token"


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
    print("Access token (do not commit):", payload.get("access_token"))
    print("Refresh token (do not commit):", payload.get("refresh_token"))
    print("Expires at:", payload.get("expires_at"))
    if not scope or "activity:read_all" not in scope:
        print("If scopes do not include activity:read_all, re-run the OAuth flow with scopes=read,activity:read_all.")


if __name__ == "__main__":
    main()
