#!/usr/bin/env python3
"""
Guide the Strava OAuth flow, exchange the auth code, and write tokens into .env.
This still requires a manual click/approval step (Strava OAuth), but removes the curl/URL copy-paste.
"""
import argparse
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import dotenv_values, load_dotenv

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


def write_env(env_file: Path, existing: dict, updates: dict) -> None:
    merged = {**existing, **updates}
    lines = []
    for key, value in merged.items():
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def wait_for_code(redirect_uri: str) -> str:
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    expected_path = parsed.path or "/"

    class Handler(BaseHTTPRequestHandler):
        server_version = "StravaCodeCatcher/1.0"

        def do_GET(self):
            parsed_path = urlparse(self.path)
            params = parse_qs(parsed_path.query)
            code = params.get("code", [None])[0]
            if code:
                self.server.code = code
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h2>Code received. You can close this tab.</h2>")
                # Stop the server after responding
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code parameter.")

        def log_message(self, fmt, *args):
            return

    httpd = HTTPServer((host, port), Handler)
    httpd.code = None
    print(f"Waiting for Strava redirect at http://{host}:{port}{expected_path} ...")
    try:
        httpd.handle_request()
    finally:
        httpd.server_close()
    if not httpd.code:
        raise SystemExit("No code received. Check redirect URI and try again.")
    return httpd.code


def main() -> None:
    parser = argparse.ArgumentParser(description="Automate Strava OAuth token retrieval and .env update.")
    parser.add_argument("--redirect-uri", default="http://localhost:3000/auth/strava", help="Must match Strava app.")
    parser.add_argument("--scopes", default="read,activity:read_all", help="Comma-separated scopes.")
    parser.add_argument("--env-file", default=".env", help="Path to .env to read/write.")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open the browser.")
    parser.add_argument(
        "--no-listen",
        action="store_true",
        help="Do not start a local HTTP listener; instead prompt you to paste the code manually.",
    )
    args = parser.parse_args()

    load_dotenv(args.env_file)
    env = dotenv_values(args.env_file)

    client_id = env.get("STRAVA_CLIENT_ID") or os.environ.get("STRAVA_CLIENT_ID")
    client_secret = env.get("STRAVA_CLIENT_SECRET") or os.environ.get("STRAVA_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise SystemExit(f"Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in {args.env_file} first.")

    auth_url = build_auth_url(client_id, args.redirect_uri, args.scopes)
    print("Open this URL and approve:")
    print(auth_url)
    if not args.no_browser:
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass
    print()

    if args.no_listen:
        code = input("Paste the 'code' query param from the redirected URL: ").strip()
    else:
        code = wait_for_code(args.redirect_uri)
        print(f"Received code: {code}")

    if not code:
        raise SystemExit("No code provided. Aborting.")

    tokens = exchange_code(client_id, client_secret, code, args.redirect_uri)

    scope_val = tokens.get("scope") or args.scopes

    updates = {
        "STRAVA_ACCESS_TOKEN": tokens.get("access_token", ""),
        "STRAVA_ACCESS_TOKEN_EXPIRES_AT": tokens.get("expires_at", ""),
        "STRAVA_REFRESH_TOKEN": tokens.get("refresh_token", ""),
        "STRAVA_ACCESS_TOKEN_SCOPE": scope_val,
    }

    env_file = Path(args.env_file)
    existing = env if env else {}
    write_env(env_file, existing, updates)

    print("\nUpdated tokens in", env_file)
    print("Scope from Strava (or requested):", scope_val)
    print("Access token expires at:", tokens.get("expires_at"))
    print("Run ./scripts/check_token_scopes.py to verify activity:read_all is present.")


if __name__ == "__main__":
    main()
