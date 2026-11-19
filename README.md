# Discord Music Bot

A Discord bot that streams music from YouTube into voice channels.

## Features

- Play YouTube links, playlists, or plain search terms (`$play never gonna give you up`)
- Separate queue for each server
- Skip, pause, resume, stop, and view the queue
- YouTube auto-generated mixes (`list=RD...`) play only the linked video instead of an endless list
- Leaves the voice channel automatically when idle or when everyone else has left

## Commands

| Command | Aliases | Description |
|---|---|---|
| `$play <url or search>` | | Play a song or playlist, or add it to the queue |
| `$join` | | Join your voice channel |
| `$skip` | `$next` | Skip the current song |
| `$pause` / `$resume` | | Pause or resume playback |
| `$stop` | | Stop playback and clear the queue |
| `$nowplaying` | `$np` | Show the current song |
| `$plist` | `$queue`, `$q` | Show the queue |
| `$clearlist` | | Clear upcoming songs |
| `$disconnect` | `$leave`, `$dc` | Leave the voice channel |
| `$shutdown` | `$poweroff` | Turn the bot off (bot owner only) |

## Built With

- [discord.py](https://github.com/Rapptz/discord.py)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [FFmpeg](https://ffmpeg.org/)
- [Node.js](https://nodejs.org/) (used by yt-dlp to handle YouTube's JavaScript challenges)

## How to Run

1. Install Python 3.10+, [FFmpeg](https://ffmpeg.org/download.html) and [Node.js](https://nodejs.org/).
2. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications) and enable the **Message Content** intent.
3. Install dependencies:
   ```
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`. Set `FFMPEG_PATH` if `ffmpeg` isn't on your PATH.
5. (Optional) Export your YouTube cookies to `cookies.txt` and set `COOKIES_FILE` so age-restricted videos work.
6. Run it:
   ```
   python Bot.py
   ```
