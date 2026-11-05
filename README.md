# Hydra

A Discord music bot that plays audio from YouTube in voice channels. It also accepts Spotify links and plays the matching songs from YouTube.

Every command works two ways: as a slash command (`/play`) and as a text command (`h!play`).

## Features

- Play a song from a YouTube link or search terms
- Queue whole YouTube playlists (up to 50 tracks at a time)
- Play Spotify tracks, albums and playlists (up to 50 tracks at a time). Optional; see [Spotify setup](#5-spotify-optional).
- Skip, pause, resume, stop, shuffle and change the volume
- See the queue and the current track
- Separate queue for each server
- Leaves the voice channel after 5 minutes with nothing playing
- Supports Discord's end-to-end encrypted voice (DAVE)
- Runs in Docker, or directly with Python
- Built-in safeguards: opens YouTube links only, never pings anyone, and only listeners can control playback

## Commands

| Slash | Text | Short form | What it does |
|---|---|---|---|
| `/play <query>` | `h!play <query>` | `h!p` | Play or queue a song or playlist. `<query>` is a YouTube link, a Spotify link or search terms. |
| `/skip` | `h!skip` | `h!s` | Skip the current track. |
| `/pause` | `h!pause` | | Pause playback. |
| `/resume` | `h!resume` | | Resume playback. |
| `/stop` | `h!stop` | | Stop, clear the queue and leave the voice channel. |
| `/queue` | `h!queue` | `h!q` | Show the current track and the next 10 in the queue. |
| `/nowplaying` | `h!nowplaying` | `h!np` | Show the current track and its link. |
| `/volume <0-100>` | `h!volume <0-100>` | `h!vol` | Set the volume. Starts at 50. |
| `/shuffle` | `h!shuffle` | | Shuffle the queue. |
| | `h!help` | | List all text commands. |

Notes:
- Join a voice channel before you use `play`.
- `play` accepts YouTube links (youtube.com, youtu.be, music.youtube.com), Spotify links (open.spotify.com track, album or playlist links, or `spotify:` URIs) and search terms. Links to other sites are refused.
- Spotify short links (`spotify.link/...`) don't work. In Spotify, use **Share > Copy link** on a computer, or open the short link in a browser and copy the full `open.spotify.com` address.
- To use `skip`, `pause`, `resume`, `stop`, `volume` or `shuffle`, you must be in the same voice channel as Hydra.
- While Hydra is playing for people in one channel, `play` from another channel won't move it there. Join its channel to add songs.
- `queue` and `nowplaying` work from anywhere.
- Commands work only in servers, not in DMs.
- The text prefix is `h!` (or `H!`), so it doesn't clash with other bots that use `!`.

Examples:

```
/play never gonna give you up
h!p https://www.youtube.com/watch?v=dQw4w9WgXcQ
h!play https://www.youtube.com/playlist?list=PL...
h!play https://open.spotify.com/playlist/...
/play https://open.spotify.com/album/...
h!vol 30
```

## Requirements

To run with Docker, you need only [Docker](https://docs.docker.com/get-docker/) with Docker Compose. The image includes Python, FFmpeg and Node.js. Do setup steps 1, 2 and 4. In step 3, only clone the repo, and skip the virtual environment and `pip` steps. Then go to [Running with Docker](#running-with-docker).

To run directly with Python, you need:

- [Python](https://www.python.org/downloads/) 3.10 or newer (tested with 3.12)
- [FFmpeg](https://ffmpeg.org/download.html), available on your `PATH`
- [Node.js](https://nodejs.org/) on your `PATH`. `yt-dlp` uses it to solve YouTube's JavaScript challenges, and without it most videos fail to load.
- A Discord bot application (see [Setup](#setup))

Check that they're installed:

```bash
python --version
ffmpeg -version
node --version
```

## Setup

### 1. Create the Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**. Name it Hydra.
2. Open the **Bot** page.
3. Under **Privileged Gateway Intents**, turn on **Message Content Intent** and save. The `h!` text commands need it.
4. On the same page, click **Reset Token** and copy the token. Keep it secret: anyone who has it controls your bot.

### 2. Invite the bot to your server

Open this link after replacing `YOUR_CLIENT_ID` with the **Application ID** from the **General Information** page:

```
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=3165184&integration_type=0&scope=bot+applications.commands
```

It asks for the `bot` and `applications.commands` scopes and these permissions: View Channels, Send Messages, Embed Links, Connect and Speak.

### 3. Install

```bash
git clone https://github.com/<your-username>/hydra.git
cd hydra
python -m venv .venv
```

Activate the virtual environment:

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (Git Bash)
source .venv/Scripts/activate

# macOS / Linux
source .venv/bin/activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

### 4. Configure

Copy `.env.example` to `.env` and fill it in:

```env
DISCORD_TOKEN=your-bot-token-here
GUILD_ID=your-server-id
```

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | Yes | The bot token from the Developer Portal. |
| `GUILD_ID` | No | Your server ID. If set, slash commands appear in that server right away. If empty, they register for every server, which can take up to an hour to show up. |

To find your server ID, turn on **Developer Mode** in Discord (User Settings > Advanced). Then right-click your server and choose **Copy Server ID**.

Never commit `.env`. It's already listed in `.gitignore`.

### 5. Spotify (optional)

Skip this if you only want YouTube. Without these settings, Spotify links get a "Spotify isn't set up" reply and everything else works.

Spotify doesn't let bots stream its audio. Hydra reads each song's title and artists from Spotify, then plays the matching song from YouTube.

**Requirements (Spotify's rules since February 2026):**
- The Spotify account that owns the app needs **Spotify Premium**.
- Playlists: Spotify guarantees access only to playlists that this account **owns or collaborates on**. Other playlists may be refused. To play a friend's playlist, ask them to make you a collaborator, or copy its songs into a playlist of your own.
- Albums and single tracks work for any public link.

**Steps:**

1. Log in to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and verify your email address if it asks.
2. Click **Create app**, or open an app you already have and click **Edit**:
   - **Redirect URI:** `http://127.0.0.1:8888/callback`, exactly. Spotify no longer accepts `localhost` addresses.
   - **APIs used:** tick **Web API**.
   - Save.
3. Add the app's **Client ID** and **Client secret** (click **View client secret**) to `.env`:

   ```env
   SPOTIFY_CLIENT_ID=your-client-id
   SPOTIFY_CLIENT_SECRET=your-client-secret
   ```

4. Log in once. This needs a computer with a web browser, so on a Raspberry Pi or server, run it on your own PC and copy the result over:

   ```bash
   python spotify_auth.py
   ```

   It uses only the Python standard library, so no install is needed. It opens Spotify in your browser; click **Agree**. It then saves `SPOTIFY_REFRESH_TOKEN` in `.env`. The access is read-only: your profile and your playlists.

5. If the bot runs on another machine, copy the 3 `SPOTIFY_` lines into the `.env` file there.
6. Restart Hydra. The log shows `Spotify: on (logged in, playlists enabled)`.

The login can expire. The app's settings page shows a **Refresh Token Lifetime** (180 days for new apps). When Spotify links start failing with "Spotify login failed", run `python spotify_auth.py` again and copy the new `SPOTIFY_REFRESH_TOKEN` to the bot's `.env`.

## Running with Docker

From the project folder, with `.env` filled in:

```bash
docker compose up -d --build
```

| Task | Command |
|---|---|
| Start, or rebuild after code changes | `docker compose up -d --build` |
| See logs | `docker logs -f hydra` |
| Stop | `docker compose down` |
| Update yt-dlp (when YouTube breaks) | `docker compose build --no-cache && docker compose up -d` |

- The container restarts by itself after a crash or reboot (`restart: unless-stopped`), as long as Docker is running.
- `.env` is read when the container starts. It's never copied into the image (see `.dockerignore`).
- The image runs as a non-root user. It includes FFmpeg and Node.js 22, which `yt-dlp` needs for YouTube.
- Run only one copy of the bot at a time, either Docker or `python bot.py`. Two copies with the same token answer every command twice.

### On a Raspberry Pi

The Docker image builds on a Raspberry Pi with no changes. Use a 64-bit Raspberry Pi OS. On 32-bit, the `PyNaCl` voice library has no ready-made build and may fail to install.

To update the Pi after changes are pushed to GitHub, run this in the bot's folder on the Pi:

```bash
git pull
docker compose up -d --build
```

`.env` isn't in Git, so the Pi keeps its own copy. Add any new settings, such as the `SPOTIFY_` lines, to the Pi's `.env` by hand.

## Running with Python

With the virtual environment active:

```bash
python bot.py
```

When it's ready, you'll see:

```
INFO     hydra: Hydra is online as Hydra#1234
```

Hydra stays online only while this process runs. To keep it online all the time, use Docker, or run it with a process manager such as `systemd` or `nssm` (Windows). Either way, run it on a machine that stays on, such as a VPS or a Raspberry Pi.

## How it works

```
/play or h!play
      |
      v
yt-dlp search  --->  tracks added to the server's queue
      |
      v
play_next()  --->  yt-dlp resolves a fresh audio URL
      |
      v
FFmpeg decodes the audio  --->  volume control  --->  discord.py sends it to the voice channel
      |
      v
track ends  --->  play_next() starts the next track
```

- **One player per server.** Each server gets a `GuildPlayer` with its own queue, current track, volume and text channel for "Now playing" messages.
- **Search.** `yt-dlp` runs with `extract_flat`, so a playlist returns its track list quickly without loading every video. Plain text becomes a YouTube search, and Hydra queues the top result.
- **Spotify.** `spotify.py` reads the track list from Spotify's Web API, logged in as the app owner's account. Each song is queued as a YouTube search for `artists - title`, and the search runs only when that song starts. A 50-song playlist queues instantly, and only the songs that actually play are looked up. Occasionally YouTube's top result is a different version, such as a live or cover recording.
- **Audio URL at play time.** YouTube's direct audio links expire after a few hours. Hydra looks up the link only when a track starts, so a long queue never hits an expired link.
- **Playback.** FFmpeg streams the audio and reconnects if the stream drops. A volume control sits on top, so `volume` takes effect mid-track. When a track ends, the next one starts automatically.
- **No blocking.** `yt-dlp` runs in a worker thread so the bot stays responsive while it searches.
- **Idle timeout.** When the queue runs out, a 5-minute timer starts. If nothing plays before it ends, Hydra leaves the channel.
- **Encrypted voice.** Discord encrypts voice end to end (the DAVE protocol). `discord.py` handles this through the `davey` package.

### Security

- **Only YouTube and Spotify are fetched.** Hydra checks every link against a list of YouTube and Spotify hosts, and turns plain text into a YouTube search. `yt-dlp` also loads only its YouTube extractors, so a link to any other site fails there too. Spotify links are reduced to a type and a 22-character ID before any API call. Without these checks, a member could make the bot fetch addresses on the host's own network, such as a router's admin page.
- **Spotify access is read-only.** The login asks only to read your playlists (`playlist-read-private`, `playlist-read-collaborative`). The refresh token in `.env` is a secret like the Discord token.
- **No pings.** Hydra sends every message with mentions turned off. A video titled `@everyone` shows as plain text and doesn't notify anyone.
- **Only listeners control playback.** `skip`, `pause`, `resume`, `stop`, `volume` and `shuffle` work only for members in Hydra's voice channel. `play` can't pull Hydra away from a channel where it's playing.
- **The token stays out of Git and Docker images.** `.env` is listed in both `.gitignore` and `.dockerignore`.

## Project structure

```
hydra/
├── bot.py               # The bot: player, commands and startup
├── spotify.py           # Reads track lists from Spotify links
├── spotify_auth.py      # One-time Spotify login; saves the refresh token to .env
├── requirements.txt     # Pinned Python dependencies
├── Dockerfile           # Image with Python 3.12, FFmpeg and Node.js 22
├── docker-compose.yml   # Runs the container with .env and auto-restart
├── .dockerignore        # Keeps .env and .venv out of the image
├── .env.example         # Template for .env
└── .gitignore
```

## Dependencies

| Package | Purpose |
|---|---|
| `discord.py[voice]` 2.7.1 | Discord API, slash and text commands, voice |
| `davey` 0.1.7 | Encryption for Discord's end-to-end encrypted voice |
| `yt-dlp` | YouTube search and audio extraction |
| `yt-dlp-ejs` | Solves YouTube's JavaScript challenges, using Node.js |
| `python-dotenv` | Loads settings from `.env` |

**Keep `davey` at 0.1.7.** `davey` 0.2.0 changed its API, and `discord.py` 2.7.1 doesn't support the change. With 0.2.0, the bot joins and appears to play, but nobody hears anything. Discord drops the audio because it was never encrypted.

## Troubleshooting

| Problem | Fix |
|---|---|
| The bot joins but there's no sound | Check that `davey` is 0.1.7: `pip show davey`. If it isn't, run `pip install davey==0.1.7`. |
| Slash commands don't appear | Set `GUILD_ID` in `.env` and restart the bot, then press Ctrl+R in Discord. Without `GUILD_ID`, wait up to an hour. |
| A member can't use slash commands | In **Server Settings > Integrations > Hydra**, allow their role. Also make sure **Use Application Commands** is allowed in that channel. Until then, they can use `h!` commands. |
| `h!` commands do nothing | Turn on **Message Content Intent** on the bot's page in the Developer Portal. |
| `PrivilegedIntentsRequired` error at startup | Same fix: turn on **Message Content Intent**. |
| "Could not find anything" or videos fail | YouTube changes often. Update with `pip install -U yt-dlp yt-dlp-ejs`, and check that Node.js is installed. |
| "YouTube refused ..." or "3 songs in a row failed" | YouTube is blocking downloads from the bot's internet connection (logs show `HTTP error 403 Forbidden`). It happens more on home servers like a Raspberry Pi. Hydra already prefers the download methods that YouTube blocks least (`STREAM_OPTS` in `bot.py`). If it keeps happening, update yt-dlp, or wait a few hours. |
| `DISCORD_TOKEN is not set` | Create `.env` from `.env.example` and add your token. |
| FFmpeg not found | Install FFmpeg and add it to your `PATH`, then open a new terminal. |
| Another bot also answers | Use `/` and choose Hydra from the list, or use the `h!` prefix. |
| "Join #channel to control playback" | Join Hydra's voice channel, then try again. |
| "Only YouTube and Spotify links are supported" | Use a YouTube or Spotify link, or type the song name as search terms. |
| "Spotify isn't set up on this bot yet" | Add `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` to the bot's `.env` and restart. See [Spotify](#5-spotify-optional). |
| "Spotify won't share that playlist" | The playlist isn't owned by, or shared for collaboration with, the account Hydra is logged in as. Copy its songs into your own playlist, or ask the owner to make you a collaborator. Also check that `SPOTIFY_REFRESH_TOKEN` is set. |
| "Spotify login failed" | The login expired or `.env` has a wrong value. Run `python spotify_auth.py` again and copy the `SPOTIFY_` lines to the bot's `.env`. |
| "Use a Spotify track, album or playlist link" | Artist, podcast and `spotify.link` short links aren't supported. Copy the full `open.spotify.com` link. |
| `spotify_auth.py` says "INVALID_CLIENT: Invalid redirect URI" | In the app's settings on the Spotify dashboard, set the Redirect URI to exactly `http://127.0.0.1:8888/callback`. |
| A Spotify song plays the wrong version | YouTube's top match for `artists - title` was a cover or live version. Play it by YouTube link instead. |
| Every command gets 2 replies | Two copies of the bot are running, for example Docker and `python bot.py`. Stop one. |
| Container keeps restarting | Run `docker logs hydra`. Usually `.env` is missing or the token is wrong. |
