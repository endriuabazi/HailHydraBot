# Hydra

A Discord music bot that plays audio from YouTube in voice channels.

Every command works two ways: as a slash command (`/play`) and as a text command (`h!play`).

## Features

- Play a song from a YouTube link or search terms
- Queue whole YouTube playlists (up to 50 tracks at a time)
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
| `/play <query>` | `h!play <query>` | `h!p` | Play or queue a song or playlist. `<query>` is a YouTube URL or search terms. |
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
- `play` accepts YouTube links (youtube.com, youtu.be, music.youtube.com) or search terms. Links to other sites are refused.
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
- **Audio URL at play time.** YouTube's direct audio links expire after a few hours. Hydra looks up the link only when a track starts, so a long queue never hits an expired link.
- **Playback.** FFmpeg streams the audio and reconnects if the stream drops. A volume control sits on top, so `volume` takes effect mid-track. When a track ends, the next one starts automatically.
- **No blocking.** `yt-dlp` runs in a worker thread so the bot stays responsive while it searches.
- **Idle timeout.** When the queue runs out, a 5-minute timer starts. If nothing plays before it ends, Hydra leaves the channel.
- **Encrypted voice.** Discord encrypts voice end to end (the DAVE protocol). `discord.py` handles this through the `davey` package.

### Security

- **Only YouTube is fetched.** Hydra checks every link against a list of YouTube hosts, and turns plain text into a YouTube search. `yt-dlp` also loads only its YouTube extractors, so a link to any other site fails there too. Without this, a member could make the bot fetch addresses on the host's own network, such as a router's admin page.
- **No pings.** Hydra sends every message with mentions turned off. A video titled `@everyone` shows as plain text and doesn't notify anyone.
- **Only listeners control playback.** `skip`, `pause`, `resume`, `stop`, `volume` and `shuffle` work only for members in Hydra's voice channel. `play` can't pull Hydra away from a channel where it's playing.
- **The token stays out of Git and Docker images.** `.env` is listed in both `.gitignore` and `.dockerignore`.

## Project structure

```
hydra/
├── bot.py               # The bot: player, commands and startup
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
| `DISCORD_TOKEN is not set` | Create `.env` from `.env.example` and add your token. |
| FFmpeg not found | Install FFmpeg and add it to your `PATH`, then open a new terminal. |
| Another bot also answers | Use `/` and choose Hydra from the list, or use the `h!` prefix. |
| "Join #channel to control playback" | Join Hydra's voice channel, then try again. |
| "Only YouTube links are supported" | Use a YouTube link, or type the song name as search terms. |
| Every command gets 2 replies | Two copies of the bot are running, for example Docker and `python bot.py`. Stop one. |
| Container keeps restarting | Run `docker logs hydra`. Usually `.env` is missing or the token is wrong. |
