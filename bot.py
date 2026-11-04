"""Hydra - a Discord bot that plays YouTube audio in voice channels."""

import asyncio
import logging
import os
import random
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlparse

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

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
STREAM_OPTS = {**_BASE_YTDL, "format": "bestaudio/best", "noplaylist": True}

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


@dataclass
class Track:
    title: str
    url: str
    duration: int | None
    requester: str

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


def _stream_url(page_url: str) -> str:
    """Resolve a fresh direct audio URL (they expire, so resolve at play time)."""
    with yt_dlp.YoutubeDL(STREAM_OPTS) as ydl:
        info = ydl.extract_info(page_url, download=False)
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
            await self.play_next()
            return

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(stream, **FFMPEG_OPTS), volume=self.volume
        )

        def after(err: Exception | None) -> None:
            if err:
                log.error("Player error: %s", err)
            asyncio.run_coroutine_threadsafe(self.play_next(), self.bot.loop)

        vc.play(source, after=after)
        await self._say(f"Now playing: **{track.title}** [{track.length}] - requested by {track.requester}")

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


@bot.hybrid_command(aliases=["p"], help="Play a song or playlist from YouTube (URL or search terms)")
@app_commands.describe(query="YouTube URL or search terms")
@commands.guild_only()
async def play(ctx: commands.Context, *, query: str) -> None:
    member = ctx.author
    if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
        await ctx.send("Join a voice channel first.")
        return
    ytdl_query = _to_ytdl_query(query)
    if ytdl_query is None:
        await ctx.send("Only YouTube links are supported. You can also type search terms.")
        return

    player = bot.player(ctx.guild)
    vc = player.vc
    # Don't pull Hydra away from people who are listening in another channel.
    if vc and vc.channel != member.voice.channel and (player.current or player.queue):
        await ctx.send(f"I'm already playing in {vc.channel.mention}. Join it to add songs.")
        return

    # Slash commands must be answered within 3 seconds; joining voice and searching can take longer.
    await ctx.defer()
    player.channel = ctx.channel

    if not vc:
        vc = await member.voice.channel.connect(self_deaf=True)
    elif vc.channel != member.voice.channel:
        await vc.move_to(member.voice.channel)

    async with ctx.typing():
        try:
            entries = await asyncio.to_thread(_search, ytdl_query)
        except Exception as e:
            log.warning("Search failed for %r: %s", query, e)
            await ctx.send("Could not find anything for that query.")
            return
    if not entries:
        await ctx.send("No results.")
        return

    tracks = [
        Track(
            title=e.get("title") or "Unknown title",
            url=_entry_url(e),
            duration=e.get("duration"),
            requester=member.display_name,
        )
        for e in entries
    ]
    player.queue.extend(tracks)

    if len(tracks) == 1:
        await ctx.send(f"Queued: **{tracks[0].title}** [{tracks[0].length}]")
    else:
        await ctx.send(f"Queued **{len(tracks)}** tracks.")

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
    await ctx.send(f"Now playing: **{t.title}** [{t.length}]\n<{t.url}>")


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
