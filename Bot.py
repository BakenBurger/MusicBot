import discord
import os
from dotenv import load_dotenv
from discord import FFmpegPCMAudio
from discord.ext import commands
from yt_dlp import YoutubeDL
from urllib.parse import urlparse, parse_qs

# Set up intents
intents = discord.Intents.default()
intents.message_content = True  # Required for command processing
intents.voice_states = True    # Required for voice channel functionality

bot = commands.Bot(command_prefix='$',
                    intents=intents,
                    case_insensitive =True)
playlist = []
ffmpeg_options = {
        'before_options':
            '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',

        'options':
            '-vn -filter:a "volume=0.25"'
    }

@bot.event      #startup log
async def on_ready():
    channel = bot.get_channel(1537113205166309426)
    if channel:
         await channel.send("Bot worked")

@bot.command()
async def join(ctx):
    if not ctx.author.voice:
        await ctx.send("Please Join A Channel")
        return

    voice_channel = ctx.author.voice.channel

    if ctx.voice_client:
        if ctx.voice_client.channel == voice_channel:
            await ctx.send("Already in your channel!")
            return

        await ctx.voice_client.move_to(voice_channel)

    else:
        await voice_channel.connect()

    await ctx.send(f"Joining {voice_channel.name}")


@bot.command()
async def play(ctx, *, song):

    # User must be in a voice channel
    if not ctx.author.voice:
        await ctx.send("Please Join A Channel")
        return

    voice_channel = ctx.author.voice.channel

    # -----------------------------
    # CONNECT / MOVE BOT
    # -----------------------------

    voice_client = ctx.voice_client

    if voice_client is None:
        await ctx.send(f"Joining {voice_channel.name}")

        try:
            voice_client = await voice_channel.connect()
        except Exception as e:
            await ctx.send(f"❌ Could not connect to voice: {e}")
            return

    elif not voice_client.is_connected():
        try:
            await voice_client.disconnect(force=True)
        except:
            pass

        try:
            voice_client = await voice_channel.connect()
        except Exception as e:
            await ctx.send(f"❌ Could not reconnect to voice: {e}")
            return

    elif voice_client.channel != voice_channel:
        await ctx.send(f"Moving to {voice_channel.name}")

        try:
            await voice_client.move_to(voice_channel)
        except Exception as e:
            await ctx.send(f"❌ Could not move channel: {e}")
            return

    # -----------------------------
    # YOUTUBE MIX DETECTION
    # -----------------------------

    parsed_url = urlparse(song)
    query = parse_qs(parsed_url.query)

    playlist_id = query.get("list", [""])[0]

    # YouTube auto-generated mixes normally start with RD
    is_mix = playlist_id.startswith("RD")

    ydl_opts = {
        'format': 'bestaudio*/best',
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
        'noplaylist': is_mix,
        'cookiefile': r'C:\Sunway\PythonVS\Random Project\MusicBot\cookies.txt',
        'js_runtimes': {
        'node': {}
        }
    }


    try:

        # -----------------------------
        # GET SONG / PLAYLIST
        # -----------------------------

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(song, download=False)

        if 'entries' in info:

            for entry in info['entries']:

                if entry:
                    title = entry.get('title', 'Unknown Track')
                    url = entry.get('url')

                    if url:
                        playlist.append((title, url))

        else:

            title = info.get('title', 'Unknown Track')
            url = info.get('url')

            if url:
                playlist.append((title, url))

        # -----------------------------
        # PLAY SONG
        # -----------------------------

        if not voice_client.is_connected():
            await ctx.send("❌ Voice connection was lost.")
            return

        if not voice_client.is_playing() and playlist:

            first_title, first_url = playlist.pop(0)

            source = FFmpegPCMAudio(
                first_url,
                **ffmpeg_options,
                executable=r"C:\Sunway\PythonVS\ffmpeg-9.0.1-essentials_build\ffmpeg-9.0.1-essentials_build\bin\ffmpeg.exe"
            )

            voice_client.play(
                source,
                after=lambda e:
                    print(f"FFmpeg error: {e}") if e
                    else play_next(ctx)
            )

            await ctx.send(
                f"🎵 Now playing: **{first_title}**"
            )

        else:

            await ctx.send(
                f"➕ Added **{title}** to playlist."
            )

    except Exception as e:

        await ctx.send(f"❌ Error: {str(e)}")
        print(f"[ERROR] {str(e)}")


#Play next function
def play_next(ctx):
    if not playlist:
        print("Playlist finished")
        return

    if not ctx.voice_client or not ctx.voice_client.is_connected():
        print("Voice client disconnected")
        return

    title, url = playlist.pop(0)

    print(f"Playing next: {title}")

    source = FFmpegPCMAudio(
        url,
        **ffmpeg_options,
        executable=r"C:\Sunway\PythonVS\ffmpeg-9.0.1-essentials_build\ffmpeg-9.0.1-essentials_build\bin\ffmpeg.exe"
    )

    ctx.voice_client.play(
        source,
        after=lambda e: (
            print(f"FFmpeg error: {e}") if e
            else play_next(ctx)
        )
    )
#play next command
@bot.command(name="next", aliases=["skip"])
async def next_song(ctx):
    if not ctx.voice_client:
        await ctx.send("❌ Bot is not connected to voice.")
        return

    if not playlist:
        await ctx.send("❌ No next song in playlist.")
        return

    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()

    await ctx.send("⏭ Skipping to next song...")

@bot.command()  # Repeat print
async def plist(ctx):
    if playlist:
        playlist_message = "\n".join([f"{idx + 1}. {title}" for idx, (title, _) in enumerate(playlist)])
        await ctx.send(f"🎶 Current Playlist:\n{playlist_message}")
    else:
        await ctx.send("❌ No tracks in the playlist.")

@bot.command()  # Repeat print
async def clearlist(ctx):
    if playlist:
        playlist.clear()
        playlist_message = ("Cleared")
        await ctx.send(f"🎶 Current Playlist:\n{playlist_message}")
    else:
        await ctx.send("❌ No tracks in the playlist.")

#disconenct
@bot.command(name="disconnect", aliases=["leave channel", "dc"])
async def disconnect(ctx):

    if ctx.voice_client:
        await ctx.send("Disconnecting")
        await ctx.voice_client.disconnect(force=True)

    else:
        await ctx.send("I'm not connected to a voice channel.")
#shutdown bot
@bot.command(name="shutdown", aliases=["poweroff"])
async def shutdown(ctx):
    await ctx.send("Shutting Down")
    await bot.close()

    

# Load the environment variables from the .env file
load_dotenv()

# Get the token
TOKEN = os.getenv('DISCORD_TOKEN')

# Run the bot
bot.run(TOKEN)