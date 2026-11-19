import asyncio
import logging
import os
import sys
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from yt_dlp import YoutubeDL

# -----------------------------
# CONFIG (from .env)
# -----------------------------

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
COOKIES_FILE = os.getenv("COOKIES_FILE")
STARTUP_CHANNEL_ID = os.getenv("STARTUP_CHANNEL_ID")
IDLE_TIMEOUT = int(os.getenv("IDLE_TIMEOUT", "300"))  # seconds
VOLUME = os.getenv("VOLUME", "0.25")

PREFIX = "$"
MAX_QUEUE_DISPLAY = 15

log = logging.getLogger("musicbot")

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": f'-vn -filter:a "volume={VOLUME}"',
}

BASE_YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "js_runtimes": {"node": {}},
}
if COOKIES_FILE:
    if os.path.isfile(COOKIES_FILE):
        BASE_YDL_OPTS["cookiefile"] = COOKIES_FILE
    else:
        log.warning("COOKIES_FILE %s not found, continuing without cookies", COOKIES_FILE)

# -----------------------------
# BOT SETUP
# -----------------------------

intents = discord.Intents.default()
intents.message_content = True  # Required for prefix commands
intents.voice_states = True     # Required for voice channel functionality

bot = commands.Bot(command_prefix=PREFIX, intents=intents, case_insensitive=True)


@dataclass
class Track:
    title: str
    url: str  # page URL; the stream URL is resolved right before playing (they expire)


class GuildPlayer:
    """Queue and playback state for one server."""

    def __init__(self):
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self.text_channel: discord.abc.Messageable | None = None
        self.lock = asyncio.Lock()
        self.idle_since = time.monotonic()

    def reset(self):
        self.queue.clear()
        self.current = None


players: dict[int, GuildPlayer] = {}
startup_done = False


def get_player(guild_id: int) -> GuildPlayer:
    if guild_id not in players:
        players[guild_id] = GuildPlayer()
    return players[guild_id]


def short_error(e: Exception) -> str:
    return str(e)[:300]


async def send(player: GuildPlayer, message: str):
    if player.text_channel:
        try:
            await player.text_channel.send(message)
        except discord.HTTPException:
            log.exception("Failed to send message")


# -----------------------------
# YT-DLP HELPERS (blocking, run in a thread)
# -----------------------------

def fetch_tracks(query: str) -> list[Track]:
    """Turn a URL / search query into a list of tracks without resolving streams."""
    playlist_id = parse_qs(urlparse(query).query).get("list", [""])[0]
    opts = {
        **BASE_YDL_OPTS,
        "extract_flat": "in_playlist",
        # YouTube auto-generated mixes (RD...) are endless, so only take the video
        "noplaylist": playlist_id.startswith("RD"),
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)

    if not info:
        return []
    entries = info.get("entries")
    items = entries if entries is not None else [info]

    tracks = []
    for entry in items:
        if not entry:
            continue
        url = entry.get("webpage_url") or entry.get("url")
        if url:
            tracks.append(Track(entry.get("title") or "Unknown Track", url))
    return tracks


def resolve_stream(page_url: str) -> str:
    """Get a fresh direct audio stream URL for one track."""
    opts = {**BASE_YDL_OPTS, "format": "bestaudio*/best", "noplaylist": True}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(page_url, download=False)
    if info and "entries" in info:
        info = next((e for e in info["entries"] if e), None)
    if not info or not info.get("url"):
        raise RuntimeError("No playable stream found")
    return info["url"]


# -----------------------------
# PLAYBACK
# -----------------------------

def after_playback(guild: discord.Guild, error: Exception | None):
    # Runs in the audio thread, so hand control back to the event loop
    if error:
        log.error("Playback error in %s: %s", guild.name, error)
    asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)


async def play_next(guild: discord.Guild):
    player = get_player(guild.id)
    async with player.lock:
        vc = guild.voice_client
        if not vc or not vc.is_connected():
            player.current = None
            return
        if vc.is_playing() or vc.is_paused():
            return

        was_playing = player.current is not None

        while player.queue:
            track = player.queue.popleft()
            try:
                stream_url = await asyncio.to_thread(resolve_stream, track.url)
            except Exception as e:
                log.warning("Could not load %s: %s", track.title, e)
                await send(player, f"❌ Skipping **{track.title}**: {short_error(e)}")
                continue

            vc = guild.voice_client
            if not vc or not vc.is_connected():
                player.current = None
                return

            try:
                source = discord.FFmpegPCMAudio(stream_url, executable=FFMPEG_PATH, **FFMPEG_OPTIONS)
                vc.play(source, after=lambda err: after_playback(guild, err))
            except Exception as e:
                log.exception("Could not start FFmpeg")
                player.current = None
                await send(player, f"❌ Could not start playback: {short_error(e)}")
                return

            player.current = track
            await send(player, f"🎵 Now playing: **{track.title}**")
            return

        player.current = None
        player.idle_since = time.monotonic()
        if was_playing:
            await send(player, "✅ Playlist finished.")


async def ensure_voice(ctx: commands.Context) -> discord.VoiceClient | None:
    """Connect to / move to the author's voice channel. Returns None on failure."""
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("Please join a voice channel first.")
        return None

    channel = ctx.author.voice.channel
    vc = ctx.voice_client

    if vc and not vc.is_connected():
        try:
            await vc.disconnect(force=True)
        except Exception:
            pass
        vc = None

    try:
        if vc is None:
            await ctx.send(f"Joining {channel.name}")
            vc = await channel.connect()
        elif vc.channel != channel:
            await ctx.send(f"Moving to {channel.name}")
            await vc.move_to(channel)
    except Exception as e:
        await ctx.send(f"❌ Could not connect to voice: {short_error(e)}")
        return None

    player = get_player(ctx.guild.id)
    player.text_channel = ctx.channel
    player.idle_since = time.monotonic()
    return vc


# -----------------------------
# EVENTS & BACKGROUND TASKS
# -----------------------------

@bot.event
async def on_ready():
    global startup_done
    if startup_done:  # on_ready fires again on every reconnect
        return
    startup_done = True

    log.info("Logged in as %s", bot.user)
    if not idle_check.is_running():
        idle_check.start()

    if STARTUP_CHANNEL_ID:
        try:
            channel = bot.get_channel(int(STARTUP_CHANNEL_ID))
            if channel:
                await channel.send("Bot is online")
        except (ValueError, discord.HTTPException):
            log.warning("Could not send startup message to %s", STARTUP_CHANNEL_ID)


@bot.event
async def on_voice_state_update(member, before, after):
    # Bot was disconnected (by a command, a kick, or the idle check): forget its state
    if member.id == bot.user.id and after.channel is None:
        players.pop(member.guild.id, None)


@tasks.loop(seconds=30)
async def idle_check():
    """Leave voice after IDLE_TIMEOUT with nothing playing or nobody listening."""
    now = time.monotonic()
    for vc in list(bot.voice_clients):
        player = get_player(vc.guild.id)
        alone = all(m.bot for m in vc.channel.members)
        busy = vc.is_playing() or vc.is_paused() or player.lock.locked()

        if busy and not alone:
            player.idle_since = now
            continue

        if now - player.idle_since >= IDLE_TIMEOUT:
            reason = "nobody is listening" if alone else "nothing is playing"
            await send(player, f"👋 Leaving because {reason}.")
            player.reset()
            await vc.disconnect(force=True)


@bot.check
async def only_in_servers(ctx):
    if ctx.guild is None:
        raise commands.NoPrivateMessage()
    return True


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.NotOwner):
        await ctx.send("❌ Only the bot owner can do that.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Usage: `{PREFIX}{ctx.command.qualified_name} {ctx.command.signature}`")
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("Commands only work in servers.")
    else:
        log.error("Error in command %s", ctx.command, exc_info=error)
        await ctx.send("❌ Something went wrong.")


# -----------------------------
# COMMANDS
# -----------------------------

@bot.command()
async def join(ctx):
    """Join your voice channel."""
    vc = ctx.voice_client
    if vc and vc.is_connected() and ctx.author.voice and vc.channel == ctx.author.voice.channel:
        await ctx.send("Already in your channel!")
        return
    await ensure_voice(ctx)


@bot.command()
async def play(ctx, *, song: str):
    """Play a YouTube link, playlist, or search query."""
    vc = await ensure_voice(ctx)
    if not vc:
        return
    player = get_player(ctx.guild.id)

    try:
        async with ctx.typing():
            tracks = await asyncio.to_thread(fetch_tracks, song)
    except Exception as e:
        log.warning("Lookup failed for %r: %s", song, e)
        await ctx.send(f"❌ Error: {short_error(e)}")
        return

    if not tracks:
        await ctx.send("❌ No results found.")
        return

    busy = vc.is_playing() or vc.is_paused() or player.lock.locked()
    player.queue.extend(tracks)

    if len(tracks) > 1:
        await ctx.send(f"➕ Added **{len(tracks)}** tracks to the playlist.")
    elif busy:
        await ctx.send(f"➕ Added **{tracks[0].title}** to the playlist.")

    if not busy:
        await play_next(ctx.guild)


@bot.command(name="next", aliases=["skip"])
async def next_song(ctx):
    """Skip the current song."""
    vc = ctx.voice_client
    if not vc or not (vc.is_playing() or vc.is_paused()):
        await ctx.send("❌ Nothing is playing.")
        return
    await ctx.send("⏭ Skipping...")
    vc.stop()  # triggers after_playback -> play_next


@bot.command()
async def stop(ctx):
    """Stop playback and clear the playlist."""
    vc = ctx.voice_client
    player = get_player(ctx.guild.id)
    player.reset()
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
    await ctx.send("⏹ Stopped and cleared the playlist.")


@bot.command()
async def pause(ctx):
    """Pause the current song."""
    vc = ctx.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send("⏸ Paused.")
    else:
        await ctx.send("❌ Nothing is playing.")


@bot.command()
async def resume(ctx):
    """Resume a paused song."""
    vc = ctx.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.send("▶ Resumed.")
    else:
        await ctx.send("❌ Nothing is paused.")


@bot.command(name="nowplaying", aliases=["np"])
async def now_playing(ctx):
    """Show the current song."""
    player = get_player(ctx.guild.id)
    if player.current:
        await ctx.send(f"🎵 Now playing: **{player.current.title}**")
    else:
        await ctx.send("❌ Nothing is playing.")


@bot.command(aliases=["queue", "q"])
async def plist(ctx):
    """Show the playlist."""
    player = get_player(ctx.guild.id)
    if not player.queue and not player.current:
        await ctx.send("❌ No tracks in the playlist.")
        return

    lines = []
    if player.current:
        lines.append(f"🎵 Now playing: **{player.current.title}**")
    if player.queue:
        lines.append("🎶 Up next:")
        shown = list(player.queue)[:MAX_QUEUE_DISPLAY]
        lines += [f"{i}. {t.title}" for i, t in enumerate(shown, start=1)]
        remaining = len(player.queue) - len(shown)
        if remaining > 0:
            lines.append(f"...and {remaining} more")

    await ctx.send("\n".join(lines)[:2000])


@bot.command()
async def clearlist(ctx):
    """Clear upcoming songs (keeps the current one playing)."""
    player = get_player(ctx.guild.id)
    if player.queue:
        player.queue.clear()
        await ctx.send("🗑 Playlist cleared.")
    else:
        await ctx.send("❌ No tracks in the playlist.")


@bot.command(name="disconnect", aliases=["leave", "dc"])
async def disconnect(ctx):
    """Leave the voice channel."""
    vc = ctx.voice_client
    if vc:
        get_player(ctx.guild.id).reset()
        await ctx.send("Disconnecting")
        await vc.disconnect(force=True)
    else:
        await ctx.send("I'm not connected to a voice channel.")


@bot.command(name="shutdown", aliases=["poweroff"])
@commands.is_owner()
async def shutdown(ctx):
    """Turn the bot off (owner only)."""
    await ctx.send("Shutting down")
    await bot.close()


if __name__ == "__main__":
    if not TOKEN:
        sys.exit("DISCORD_TOKEN is not set. Add it to your .env file.")
    bot.run(TOKEN, root_logger=True)
