"""Read track names from Spotify links via the official Web API.

Spotify doesn't allow streaming its audio, so Hydra only reads each track's
title and artists here; bot.py then plays the matching song from YouTube.

Auth: with SPOTIFY_REFRESH_TOKEN (from spotify_auth.py) requests act as the
app owner's account, which Spotify requires for reading playlist contents
(only playlists that account owns or collaborates on, since February 2026).
Without it, the app's own Client Credentials token is used, which can't read
playlist contents.
"""

import base64
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import aiohttp

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_HOSTS = {"open.spotify.com", "play.spotify.com"}
KINDS = ("track", "album", "playlist")
# Spotify IDs are 22 base62 characters; checking this keeps user input out of API paths.
_ID_RE = re.compile(r"^[0-9A-Za-z]{22}$")


class SpotifyError(Exception):
    """Error with a message that is safe to show in Discord."""


class _NotAccessible(Exception):
    """Spotify answered 403/404 for an item."""


@dataclass
class SpotifyTrack:
    title: str
    artists: str
    duration: int | None  # seconds
    url: str


def parse_link(query: str) -> tuple[str, str] | None:
    """Return (kind, id) for a Spotify track/album/playlist link or URI, else None."""
    query = query.strip()
    if query.startswith("spotify:"):
        parts = query.split(":")
        if len(parts) == 3 and parts[1] in KINDS and _ID_RE.match(parts[2]):
            return parts[1], parts[2]
        return None
    url = query if "://" in query else f"https://{query}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in SPOTIFY_HOSTS:
        return None
    # Paths look like /track/ID, /intl-de/album/ID or /user/x/playlist/ID.
    segments = [s for s in parsed.path.split("/") if s]
    for kind, value in zip(segments, segments[1:]):
        if kind in KINDS and _ID_RE.match(value):
            return kind, value
    return None


def is_spotify_link(query: str) -> bool:
    q = query.strip().lower()
    return q.startswith("spotify:") or any(host in q for host in SPOTIFY_HOSTS) or "spotify.link" in q


class SpotifyClient:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str | None = None):
        self._basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self._refresh_token = refresh_token
        self._token: str | None = None
        self._expires_at = 0.0
        self._session: aiohttp.ClientSession | None = None

    @property
    def has_user_login(self) -> bool:
        return bool(self._refresh_token)

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._session

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._expires_at - 60:
            return self._token
        if self._refresh_token:
            form = {"grant_type": "refresh_token", "refresh_token": self._refresh_token}
        else:
            form = {"grant_type": "client_credentials"}
        session = await self._http()
        async with session.post(TOKEN_URL, data=form, headers={"Authorization": f"Basic {self._basic}"}) as r:
            data = await r.json(content_type=None)
            if r.status != 200:
                raise SpotifyError("Spotify login failed. Check the Spotify settings in `.env`.") from RuntimeError(
                    f"token endpoint {r.status}: {data.get('error')}"
                )
        self._token = data["access_token"]
        self._expires_at = time.monotonic() + data.get("expires_in", 3600)
        # Spotify may rotate the refresh token; keep using the newest one in memory.
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        return self._token

    async def _get(self, url: str, params: dict | None = None) -> dict:
        session = await self._http()
        headers = {"Authorization": f"Bearer {await self._access_token()}"}
        async with session.get(url, params=params, headers=headers) as r:
            if r.status == 200:
                return await r.json()
            if r.status in (403, 404):
                raise _NotAccessible()
            if r.status == 429:
                raise SpotifyError("Spotify is rate limiting Hydra. Try again in a minute.")
            raise SpotifyError("Spotify returned an error. Try again later.") from RuntimeError(f"GET {url}: {r.status}")

    def _market(self) -> str:
        # A user token knows its country; an app-only token needs one, or tracks count as unavailable.
        return "from_token" if self.has_user_login else "US"

    async def tracks(self, kind: str, item_id: str, limit: int) -> tuple[str, list[SpotifyTrack]]:
        """Return (name of the track/album/playlist, up to `limit` tracks)."""
        market = self._market()
        try:
            if kind == "track":
                t = await self._get(f"{API}/tracks/{item_id}", {"market": market})
                track = _to_track(t)
                return track.title, [track]
            if kind == "album":
                album = await self._get(f"{API}/albums/{item_id}", {"market": market})
                return album["name"], await self._paged(f"{API}/albums/{item_id}/tracks", market, limit, key=None)
            playlist = await self._get(f"{API}/playlists/{item_id}", {"market": market, "fields": "name"})
            return playlist["name"], await self._paged(f"{API}/playlists/{item_id}/items", market, limit, key="item")
        except _NotAccessible:
            if kind == "playlist":
                raise SpotifyError(
                    "Spotify won't share that playlist. It only allows playlists owned by, "
                    "or shared for collaboration with, the Spotify account Hydra is logged in as."
                ) from None
            raise SpotifyError(f"Couldn't find that Spotify {kind}. It may be private or unavailable.") from None

    async def _paged(self, url: str, market: str, limit: int, key: str | None) -> list[SpotifyTrack]:
        tracks: list[SpotifyTrack] = []
        next_url: str | None = url
        params: dict | None = {"market": market, "limit": 50}
        while next_url and len(tracks) < limit:
            page = await self._get(next_url, params)
            params = None  # the `next` URL already carries the query string
            for entry in page.get("items", []):
                # Playlist entries wrap the track in `item` (older responses used `track`).
                t = (entry.get(key) or entry.get("track")) if key else entry
                if t and t.get("type", "track") == "track" and t.get("name"):
                    tracks.append(_to_track(t))
            next_url = page.get("next")
        return tracks[:limit]


def _to_track(t: dict) -> SpotifyTrack:
    artists = ", ".join(a["name"] for a in t.get("artists", []) if a.get("name"))
    duration_ms = t.get("duration_ms")
    url = (t.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/track/{t.get('id', '')}"
    return SpotifyTrack(
        title=t["name"],
        artists=artists,
        duration=duration_ms // 1000 if duration_ms else None,
        url=url,
    )
