"""One-time Spotify login for Hydra.

Spotify only shares playlist contents with an app that is logged in as a user
who owns or collaborates on the playlist. This script logs in once in your
browser and saves a refresh token as SPOTIFY_REFRESH_TOKEN in .env.

Usage (standard library only, no install needed):
    python spotify_auth.py

Before running, add this Redirect URI to your Spotify app's settings:
    http://127.0.0.1:8888/callback
"""

import base64
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ENV_PATH = Path(__file__).with_name(".env")
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "playlist-read-private playlist-read-collaborative"


def read_env() -> dict[str, str]:
    values = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def save_env_value(key: str, value: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    env = read_env()
    client_id, client_secret = env.get("SPOTIFY_CLIENT_ID"), env.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env first.")

    state = secrets.token_urlsafe(16)
    auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
        }
    )
    result: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server API)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if urllib.parse.urlparse(self.path).path != "/callback":
                self.send_error(404)
                return
            if query.get("state", [""])[0] != state:
                result["error"] = "state mismatch (the login was not started by this script)"
            elif "error" in query:
                result["error"] = query["error"][0]
            else:
                result["code"] = query.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Hydra is logged in to Spotify. You can close this tab." if "code" in result else "Login failed. Check the terminal."
            self.wfile.write(f"<h2>{msg}</h2>".encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 8888), Callback)
    print("Opening Spotify login in your browser. If it doesn't open, visit:\n" + auth_url)
    webbrowser.open(auth_url)
    while not result:
        server.handle_request()
    server.server_close()

    if "error" in result:
        sys.exit(f"Spotify login failed: {result['error']}")

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    body = urllib.parse.urlencode(
        {"grant_type": "authorization_code", "code": result["code"], "redirect_uri": REDIRECT_URI}
    ).encode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=body,
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            tokens = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Token exchange failed: HTTP {e.code} {e.read().decode(errors='replace')[:200]}")

    save_env_value("SPOTIFY_REFRESH_TOKEN", tokens["refresh_token"])
    print(f"Saved SPOTIFY_REFRESH_TOKEN to {ENV_PATH.name}. Restart Hydra to use it.")


if __name__ == "__main__":
    main()
