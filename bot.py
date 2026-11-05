"""Hydra - a Discord bot that plays YouTube audio in voice channels, from YouTube or Spotify links."""

import asyncio
import logging
import os
import random
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlparse

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import spotify

load_dotenv()
log = logging.getLogger("hydra")

MAX_PLAYLIST_ITEMS = 50
IDLE_TIMEOUT_SECONDS = 300

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}

# Base options shared by search and stream resolution.
# YouTube needs a JS runtime to solve its signature challenges; Node is used here.
# Only YouTube extractors are loaded, so yt-dlp never fetches arbitrary sites
# (e.g. internal network addresses) on a user's behalf.
_BASE_YTDL = {
    "quiet": True,
    "no_warnings": True,
    "js_runtimes": {"node": {}},
    "allowed_extractors": ["youtube.*"],
}
SEARCH_OPTS = {**_BASE_YTDL, "extract_flat": "in_playlist", "playlistend": MAX_PLAYLIST_ITEMS}
# On networks YouTube distrusts, the default clients' audio links need a "PO token";
# without one YouTube answers 403 Forbidden after the first song. The web_safari
# client's HLS streams don't need one, so prefer them (smallest video, since HLS has
# no audio-only streams), and fall back to the tv_simply client for other videos.
STREAM_OPTS = {
    **_BASE_YTDL,
    "format": "bestaudio[protocol^=m3u8]/best[protocol^=m3u8][height<=360]/bestaudio/best",
    "extractor_args": {"youtube": {"player_client": ["web_safari", "tv_simply"]}},
    "noplaylist": True,
}

# A song that ends this soon after starting failed to stream (e.g. YouTube refused it).
MIN_PLAY_SECONDS = 3
# Stop after this many failed songs in a row instead of skipping through the whole queue.
MAX_FAILURES_IN_ROW = 3

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


@dataclass
class Track:
    title: str
    url: str  # what yt-dlp resolves: a YouTube URL, or a "ytsearch1:" query for Spotify tracks
    duration: int | None
    requester: str
    link: str  # shown to users: the YouTube or Spotify page

    @property
    def length(self) -> str:
        if not self.duration:
            return "live"
        m, s = divmod(int(self.duration), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def _to_ytdl_query(query: str) -> str | None:
    """Return a YouTube URL or an explicit YouTube search; None for non-YouTube links."""
    query = query.strip()
    looks_like_url = "://" in query or query.lower().startswith(("www.", "youtube.com", "youtu.be", "m.youtube.com"))
    if not looks_like_url:
        # Explicit prefix so yt-dlp never guesses that plain text like "example.com" is a URL.
        return f"ytsearch1:{query}"
    url = query if "://" in query else f"https://{query}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in YOUTUBE_HOSTS:
        return None
    return url


def _search(query: str) -> list[dict]:
    """Return flat entries for a URL, playlist, or search query."""
    with yt_dlp.YoutubeDL(SEARCH_OPTS) as ydl:
        info = ydl.extract_info(query, download=False)
    if not info:
        return []
    entries = info.get("entries")
    if entries is None:
        return [info]
    return [e for e in entries if e]


def _stream_url(target: str) -> str:
    """Resolve a fresh direct audio URL (they expire, so resolve at play time).

    `target` is a YouTube URL or a "ytsearch1:" query (Spotify tracks are only
    matched to a YouTube video here, when they start playing).
    """
    with yt_dlp.YoutubeDL(STREAM_OPTS) as ydl:
        info = ydl.extract_info(target, download=False)
    if "entries" in info:
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise LookupError(f"no YouTube result for {target!r}")
        info = entries[0]
    return info["url"]


def _entry_url(entry: dict) -> str:
    url = entry.get("webpage_url") or entry.get("url") or ""
    if not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={entry.get('id') or url}"
    return url


class GuildPlayer:
    """Queue and playback state for one server."""

    def __init__(self, bot: "Hydra", guild: discord.Guild):
        self.bot = bot
        self.guild = guild
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self.volume = 0.5
        self.channel: discord.abc.Messageable | None = None
        self.skipping = False  # set by the skip command, so a quick skip doesn't count as a failure
        self._failures = 0
        self._idle_task: asyncio.Task | None = None

    @property
    def vc(self) -> discord.VoiceClient | None:
        return self.guild.voice_client  # type: ignore[return-value]

    async def play_next(self) -> None:
        vc = self.vc
        if not vc or not vc.is_connected():
            self.current = None
            return
        if not self.queue:
            self.current = None
            self._start_idle_timer()
            return

        self._cancel_idle_timer()
        track = self.queue.popleft()
        self.current = track
        try:
            stream = await asyncio.to_thread(_stream_url, track.url)
        except Exception as e:
            log.warning("Failed to resolve %s: %s", track.url, e)
            await self._say(f"Could not play **{track.title}**, skipping.")
            if not await self._count_failure():
                await self.play_next()
            return

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(stream, **FFMPEG_OPTS), volume=self.volume
        )
        started = time.monotonic()

        def after(err: Exception | None) -> None:
            if err:
                log.error("Player error: %s", err)
            elapsed = time.monotonic() - started
            asyncio.run_coroutine_threadsafe(self._after_track(elapsed), self.bot.loop)

        vc.play(source, after=after)
        await self._say(f"Now playing: **{track.title}** [{track.length}] - requested by {track.requester}")

    async def _after_track(self, elapsed: float) -> None:
        skipped, self.skipping = self.skipping, False
        vc = self.vc
        if vc and vc.is_connected() and not skipped and elapsed < MIN_PLAY_SECONDS:
            # FFmpeg quit almost at once, so the stream was refused (e.g. 403 Forbidden).
            title = self.current.title if self.current else "that song"
            log.warning("%s stopped after %.1fs; the stream was probably refused", title, elapsed)
            await self._say(f"YouTube refused **{title}**, skipping.")
            if await self._count_failure():
                return
        else:
            self._failures = 0
        await self.play_next()

    async def _count_failure(self) -> bool:
        """Count a failed song; after several in a row, stop and return True."""
        self._failures += 1
        if self._failures < MAX_FAILURES_IN_ROW:
            return False
        self._failures = 0
        self.queue.clear()
        self.current = None
        self._start_idle_timer()
        await self._say(
            f"{MAX_FAILURES_IN_ROW} songs in a row failed, so I stopped and cleared the queue. "
            "YouTube may be refusing downloads right now. Try again later."
        )
        return True

    async def _say(self, text: str) -> None:
        if self.channel:
            try:
                await self.channel.send(text)
            except discord.HTTPException:
                pass

    def _start_idle_timer(self) -> None:
        self._cancel_idle_timer()
        self._idle_task = self.bot.loop.create_task(self._idle_disconnect())

    def _cancel_idle_timer(self) -> None:
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None

    async def _idle_disconnect(self) -> None:
        await asyncio.sleep(IDLE_TIMEOUT_SECONDS)
        vc = self.vc
        if vc and not vc.is_playing() and not vc.is_paused():
            await vc.disconnect()
            await self._say("Left the voice channel after being idle.")

    async def stop(self) -> None:
        self.queue.clear()
        self.current = None
        self._cancel_idle_timer()
        if self.vc:
            await self.vc.disconnect()


class Hydra(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # needed to read "h!" commands
        # A unique prefix so Hydra doesn't clash with other bots that use "!".
        # No pings: video titles and names are user-controlled and could contain @everyone.
        super().__init__(
            command_prefix=["h!", "H!"],
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.players: dict[int, GuildPlayer] = {}
        client_id, client_secret = os.getenv("SPOTIFY_CLIENT_ID"), os.getenv("SPOTIFY_CLIENT_SECRET")
        self.spotify = (
            spotify.SpotifyClient(client_id, client_secret, os.getenv("SPOTIFY_REFRESH_TOKEN") or None)
            if client_id and client_secret
            else None
        )

    async def close(self) -> None:
        if self.spotify:
            await self.spotify.close()
        await super().close()

    def player(self, guild: discord.Guild) -> GuildPlayer:
        if guild.id not in self.players:
            self.players[guild.id] = GuildPlayer(self, guild)
        return self.players[guild.id]

    async def setup_hook(self) -> None:
        # Commands are hybrid: each one works as /name and h!name.
        # A dev guild syncs instantly; global sync can take up to an hour to appear.
        guild_id = os.getenv("GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Hydra is online as %s", self.user)
        if self.spotify is None:
            log.info("Spotify: off (set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to enable)")
        else:
            log.info("Spotify: on (%s)", "logged in, playlists enabled" if self.spotify.has_user_login
                     else "no SPOTIFY_REFRESH_TOKEN, so playlists won't work; run spotify_auth.py")
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="/play"))

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        usage = f"`h!{ctx.command.qualified_name} {ctx.command.signature}`" if ctx.command else ""
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"Missing `{error.param.name}`. Usage: {usage}")
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"Invalid value. Usage: {usage}")
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("Use Hydra commands in a server.")
        elif isinstance(error, NotInBotVoiceChannel):
            await ctx.send(str(error))
        else:
            log.error("Command %s failed", ctx.command, exc_info=error)
            await ctx.send("Something went wrong running that command.")


bot = Hydra()


def _player_or_none(ctx: commands.Context) -> GuildPlayer | None:
    return bot.players.get(ctx.guild.id) if ctx.guild else None


class NotInBotVoiceChannel(commands.CheckFailure):
    pass


def in_bot_voice_channel():
    """Only members in Hydra's voice channel may control playback."""

    async def predicate(ctx: commands.Context) -> bool:
        vc = ctx.guild.voice_client if ctx.guild else None
        if vc is None:
            return True  # nothing to control; the command reports that itself
        voice = getattr(ctx.author, "voice", None)
        if not voice or voice.channel != vc.channel:
            raise NotInBotVoiceChannel(f"Join {vc.channel.mention} to control playback.")
        return True

    return commands.check(predicate)


class PlayError(Exception):
    """A problem with the requested song, with a message safe to show in Discord."""


async def _find_tracks(query: str, requester: str) -> tuple[str | None, list[Track]]:
    """Return (name of the album/playlist if any, tracks) for a YouTube/Spotify link or search."""
    if spotify.is_spotify_link(query):
        ref = spotify.parse_link(query)
        if ref is None:
            raise PlayError("Use a Spotify track, album or playlist link, like `https://open.spotify.com/playlist/...`.")
        if bot.spotify is None:
            raise PlayError("Spotify isn't set up on this bot yet. YouTube links and search still work.")
        try:
            name, items = await bot.spotify.tracks(*ref, limit=MAX_PLAYLIST_ITEMS)
        except spotify.SpotifyError as e:
            log.warning("Spotify lookup failed for %s %s: %s", *ref, e.__cause__ or e)
            raise PlayError(str(e)) from None
        tracks = [
            Track(
                title=f"{t.artists} - {t.title}" if t.artists else t.title,
                # Matched to a YouTube video only when it starts playing, so big playlists queue instantly.
                url=f"ytsearch1:{t.artists} - {t.title}",
                duration=t.duration,
                requester=requester,
                link=t.url,
            )
            for t in items
        ]
        return (name if ref[0] != "track" else None), tracks

    ytdl_query = _to_ytdl_query(query)
    if ytdl_query is None:
        raise PlayError("Only YouTube and Spotify links are supported. You can also type search terms.")
    try:
        entries = await asyncio.to_thread(_search, ytdl_query)
    except Exception as e:
        log.warning("Search failed for %r: %s", query, e)
        raise PlayError("Could not find anything for that query.") from None
    tracks = []
    for e in entries:
        url = _entry_url(e)
        tracks.append(Track(e.get("title") or "Unknown title", url, e.get("duration"), requester, link=url))
    return None, tracks


@bot.hybrid_command(aliases=["p"], help="Play a song or playlist from YouTube or Spotify (link or search terms)")
@app_commands.describe(query="YouTube or Spotify link, or search terms")
@commands.guild_only()
async def play(ctx: commands.Context, *, query: str) -> None:
    member = ctx.author
    if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
        await ctx.send("Join a voice channel first.")
        return

    player = bot.player(ctx.guild)
    vc = player.vc
    # Don't pull Hydra away from people who are listening in another channel.
    if vc and vc.channel != member.voice.channel and (player.current or player.queue):
        await ctx.send(f"I'm already playing in {vc.channel.mention}. Join it to add songs.")
        return

    # Slash commands must be answered within 3 seconds; searching and joining voice can take longer.
    await ctx.defer()

    async with ctx.typing():
        try:
            collection, tracks = await _find_tracks(query, member.display_name)
        except PlayError as e:
            await ctx.send(str(e))
            return
    if not tracks:
        await ctx.send("No results.")
        return

    player.channel = ctx.channel
    vc = player.vc
    if not vc:
        vc = await member.voice.channel.connect(self_deaf=True)
    elif vc.channel != member.voice.channel:
        await vc.move_to(member.voice.channel)

    player.queue.extend(tracks)

    if len(tracks) == 1 and not collection:
        await ctx.send(f"Queued: **{tracks[0].title}** [{tracks[0].length}]")
    else:
        source = f" from **{collection}**" if collection else ""
        await ctx.send(f"Queued **{len(tracks)}** tracks{source}.")

    # `current` is set before the stream URL resolves, so this also guards
    # against two quick play calls both starting playback.
    if player.current is None:
        await player.play_next()


@bot.hybrid_command(aliases=["s"], help="Skip the current track")
@commands.guild_only()
@in_bot_voice_channel()
async def skip(ctx: commands.Context) -> None:
    player = _player_or_none(ctx)
    if not player or not player.vc or not (player.vc.is_playing() or player.vc.is_paused()):
        await ctx.send("Nothing is playing.")
        return
    player.skipping = True
    player.vc.stop()  # triggers the after-callback, which plays the next track
    await ctx.send("Skipped.")


@bot.hybrid_command(help="Pause playback")
@commands.guild_only()
@in_bot_voice_channel()
async def pause(ctx: commands.Context) -> None:
    player = _player_or_none(ctx)
    if not player or not player.vc or not player.vc.is_playing():
        await ctx.send("Nothing is playing.")
        return
    player.vc.pause()
    await ctx.send("Paused.")


@bot.hybrid_command(help="Resume playback")
@commands.guild_only()
@in_bot_voice_channel()
async def resume(ctx: commands.Context) -> None:
    player = _player_or_none(ctx)
    if not player or not player.vc or not player.vc.is_paused():
        await ctx.send("Nothing is paused.")
        return
    player.vc.resume()
    await ctx.send("Resumed.")


@bot.hybrid_command(help="Stop playback, clear the queue, and leave")
@commands.guild_only()
@in_bot_voice_channel()
async def stop(ctx: commands.Context) -> None:
    player = _player_or_none(ctx)
    if not player or not player.vc:
        await ctx.send("Not connected.")
        return
    await player.stop()
    await ctx.send("Stopped and left the channel.")


@bot.hybrid_command(name="queue", aliases=["q"], help="Show the queue")
@commands.guild_only()
async def show_queue(ctx: commands.Context) -> None:
    player = _player_or_none(ctx)
    if not player or (not player.current and not player.queue):
        await ctx.send("The queue is empty.")
        return
    lines = []
    if player.current:
        lines.append(f"**Now:** {player.current.title} [{player.current.length}]")
    for i, t in enumerate(list(player.queue)[:10], start=1):
        lines.append(f"`{i}.` {t.title} [{t.length}]")
    if len(player.queue) > 10:
        lines.append(f"...and {len(player.queue) - 10} more")
    embed = discord.Embed(title="Hydra queue", description="\n".join(lines), color=0x2ECC71)
    await ctx.send(embed=embed)


@bot.hybrid_command(aliases=["np"], help="Show the current track")
@commands.guild_only()
async def nowplaying(ctx: commands.Context) -> None:
    player = _player_or_none(ctx)
    if not player or not player.current:
        await ctx.send("Nothing is playing.")
        return
    t = player.current
    await ctx.send(f"Now playing: **{t.title}** [{t.length}]\n<{t.link}>")


@bot.hybrid_command(aliases=["vol"], help="Set volume (0-100)")
@app_commands.describe(level="Volume from 0 to 100")
@commands.guild_only()
@in_bot_voice_channel()
async def volume(ctx: commands.Context, level: int) -> None:
    if not 0 <= level <= 100:
        await ctx.send("Volume must be between 0 and 100.")
        return
    player = bot.player(ctx.guild)
    player.volume = level / 100
    if player.vc and isinstance(player.vc.source, discord.PCMVolumeTransformer):
        player.vc.source.volume = player.volume
    await ctx.send(f"Volume set to {level}%.")


@bot.hybrid_command(help="Shuffle the queue")
@commands.guild_only()
@in_bot_voice_channel()
async def shuffle(ctx: commands.Context) -> None:
    player = _player_or_none(ctx)
    if not player or len(player.queue) < 2:
        await ctx.send("Not enough tracks to shuffle.")
        return
    items = list(player.queue)
    random.shuffle(items)
    player.queue = deque(items)
    await ctx.send("Queue shuffled.")


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and add your bot token.")
    discord.utils.setup_logging(level=logging.INFO)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
