#!/usr/bin/env python3
"""
Helper to build the Strava OAuth URL and exchange an auth code for tokens.
Examples:
  python scripts/strava_auth_helper.py
  python scripts/strava_auth_helper.py --redirect-uri http://localhost:8000/auth/strava
  python scripts/strava_auth_helper.py --exchange AUTH_CODE_HERE
"""
import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

TOKEN_URL = "https://www.strava.com/oauth/token"


def build_auth_url(client_id: str, redirect_uri: str, scopes: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "approval_prompt": "force",
        "scope": scopes,
    }
    return f"https://www.strava.com/oauth/authorize?{urlencode(params)}"


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Failed to exchange code: {resp.status_code} {resp.text}")
    return resp.json()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Build Strava OAuth URL and exchange an auth code for tokens.")
    parser.add_argument(
        "--redirect-uri",
        default="http://localhost:3000/auth/strava",
        help="Must match the redirect URI configured in your Strava app.",
    )
    parser.add_argument(
        "--scopes",
        default="read,activity:read_all",
        help="Comma-separated scopes.",
    )
    parser.add_argument(
        "--exchange",
        metavar="CODE",
        help="If provided, exchange this auth code for tokens.",
    )
    args = parser.parse_args()

    client_id = os.environ.get("STRAVA_CLIENT_ID")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit("Missing STRAVA_CLIENT_ID or STRAVA_CLIENT_SECRET in environment/.env")

    auth_url = build_auth_url(client_id, args.redirect_uri, args.scopes)
    print("Authorize URL (open in browser and approve):")
    print(auth_url)
    print()

    if args.exchange:
        tokens = exchange_code(client_id, client_secret, args.exchange, args.redirect_uri)
        expires_at = tokens.get("expires_at")
        scope = tokens.get("access_token", "hidden"), tokens.get("refresh_token", "hidden")
        print("Copy these into .env:")
        print(f"STRAVA_ACCESS_TOKEN={tokens.get('access_token')}")
        print(f"STRAVA_ACCESS_TOKEN_EXPIRES_AT={expires_at}")
        print(f"STRAVA_REFRESH_TOKEN={tokens.get('refresh_token')}")
        print(f"(Scope: {tokens.get('scope')})")
    else:
        print("After approval, grab the `code` query param from the redirect URL and run:")
        script = Path(__file__).name
        print(f"python scripts/{script} --exchange YOUR_CODE_HERE --redirect-uri {args.redirect_uri}")


if __name__ == "__main__":
    main()
