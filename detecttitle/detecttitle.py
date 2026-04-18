import discord
import aiohttp
import asyncio
import logging
from typing import Optional, List
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger("red.detecttitle")

class DetectTitle(commands.Cog):
    """Monitor a Twitch channel for specific title keywords when they go live."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        default_guild = {
            "twitch_channel": None,
            "keywords": [],
            "destination_channel": None,
            "enabled": False,
            "last_stream_id": None,
            "last_message_id": None
        }
        self.config.register_guild(**default_guild)
        self._session: Optional[aiohttp.ClientSession] = None
        self._monitor_task = self.bot.loop.create_task(self._monitor_loop())

    def cog_unload(self):
        if self._monitor_task:
            self._monitor_task.cancel()
        if self._session:
            asyncio.create_task(self._session.close())

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _get_twitch_auth(self):
        tokens = await self.bot.get_shared_api_tokens("twitch")
        client_id = tokens.get("client_id")
        client_secret = tokens.get("client_secret")
        if not client_id or not client_secret:
            return None, None

        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials"
        }
        session = await self._get_session()
        try:
            async with session.post(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return client_id, data["access_token"]
                else:
                    log.error(f"Failed to get Twitch token: {resp.status} - {await resp.text()}")
        except Exception as e:
            log.error(f"Exception while getting Twitch token: {e}")
        return None, None

    async def _delete_old_alert(self, guild_id: int, settings: dict):
        """Deletes the last posted alert message if it exists."""
        if not settings["last_message_id"]:
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        channel = guild.get_channel(settings["destination_channel"])
        if not channel:
            return

        try:
            msg = await channel.fetch_message(settings["last_message_id"])
            await msg.delete()
        except discord.NotFound:
            pass # Already deleted
        except Exception as e:
            log.warning(f"Could not delete old alert in guild {guild_id}: {e}")
        finally:
            await self.config.guild_from_id(guild_id).last_message_id.set(None)

    async def _check_stream(self, guild_id: int, settings: dict):
        client_id, token = await self._get_twitch_auth()
        if not client_id or not token:
            return

        twitch_channel = settings["twitch_channel"]
        url = f"https://api.twitch.tv/helix/streams?user_login={twitch_channel}"
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {token}"
        }
        
        session = await self._get_session()
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    log.error(f"Failed to check Twitch stream: {resp.status} - {await resp.text()}")
                    return
                
                data = await resp.json()
                streams = data.get("data", [])
                
                if not streams:
                    # Stream is offline
                    if settings["last_stream_id"] is not None:
                        await self._delete_old_alert(guild_id, settings)
                        await self.config.guild_from_id(guild_id).last_stream_id.set(None)
                    return

                stream = streams[0]
                stream_id = stream["id"]
                last_id = settings["last_stream_id"]

                # If we've already alerted for this specific stream ID, skip
                if stream_id == last_id:
                    return

                title = stream["title"].lower()
                keywords = [k.lower() for k in settings["keywords"]]
                
                match = False
                if not keywords:
                    match = True
                else:
                    for kw in keywords:
                        if kw in title:
                            match = True
                            break
                
                if match:
                    # Clean up any existing alert before posting a new one (e.g. title changed)
                    await self._delete_old_alert(guild_id, settings)
                    await self._post_alert(guild_id, settings, stream)
                    await self.config.guild_from_id(guild_id).last_stream_id.set(stream_id)
        except Exception as e:
            log.error(f"Exception while checking stream for guild {guild_id}: {e}")

    async def _post_alert(self, guild_id: int, settings: dict, stream: dict):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        
        channel_id = settings["destination_channel"]
        channel = guild.get_channel(channel_id)
        if not channel:
            return

        twitch_name = stream["user_name"]
        stream_title = stream["title"]
        thumbnail = stream["thumbnail_url"].replace("{width}", "1280").replace("{height}", "720")
        stream_url = f"https://www.twitch.tv/{stream['user_login']}"

        embed = discord.Embed(
            title=f"{twitch_name} is now live!",
            description=f"**{stream_title}**\n\nhey come check out this channel\n{stream_url}",
            color=0x6441A5
        )
        embed.set_image(url=thumbnail)
        embed.set_footer(text="Twitch Stream Alert")
        
        try:
            msg = await channel.send(embed=embed)
            await self.config.guild_from_id(guild_id).last_message_id.set(msg.id)
        except discord.Forbidden:
            log.warning(f"Failed to send alert in {channel.name} ({guild_id}): Permission denied")
        except Exception as e:
            log.error(f"Error posting alert in guild {guild_id}: {e}")

    async def _monitor_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                all_guilds = await self.config.all_guilds()
                for guild_id, settings in all_guilds.items():
                    if not settings["enabled"] or not settings["twitch_channel"] or not settings["destination_channel"]:
                        continue
                    await self._check_stream(guild_id, settings)
            except Exception as e:
                log.exception(f"Error in monitor loop: {e}")
            
            await asyncio.sleep(60)

    @commands.group(name="detecttitle", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def detecttitle(self, ctx):
        """Settings for DetectTitle Twitch alerts."""
        await ctx.send_help()

    @detecttitle.command(name="setup")
    async def setup(self, ctx, twitch_channel: str, destination_channel: discord.TextChannel, *, keyword: str):
        """Setup the entire monitoring process.
        
        Example: `[p]detecttitle setup MyChannel #alerts "Playing Game"`
        """
        # Remove URL prefix if provided
        channel_name = twitch_channel.split('/')[-1].lower()
        
        await self.config.guild(ctx.guild).twitch_channel.set(channel_name)
        await self.config.guild(ctx.guild).destination_channel.set(destination_channel.id)
        await self.config.guild(ctx.guild).keywords.set([keyword])
        await self.config.guild(ctx.guild).enabled.set(True)
        # Reset last_stream_id to allow immediate detection if they are already live
        await self.config.guild(ctx.guild).last_stream_id.set(None)
        
        await ctx.send(f"✅ **Monitoring setup complete!**\n"
                       f"• **Twitch:** {channel_name}\n"
                       f"• **Destination:** {destination_channel.mention}\n"
                       f"• **Keyword:** \"{keyword}\"\n"
                       f"• **Status:** Enabled")
        
        # Check if API tokens are set
        tokens = await self.bot.get_shared_api_tokens("twitch")
        if not tokens.get("client_id") or not tokens.get("client_secret"):
            await ctx.send("⚠️ **Note:** Twitch API tokens are not set! Use `[p]detecttitle creds` for instructions.")

    @detecttitle.command(name="channel")
    async def set_channel(self, ctx, channel: str):
        """Set the Twitch channel to monitor."""
        channel_name = channel.split('/')[-1].lower()
        await self.config.guild(ctx.guild).twitch_channel.set(channel_name)
        await ctx.send(f"✅ Monitoring Twitch channel: `{channel_name}`")

    @detecttitle.command(name="destination")
    async def set_destination(self, ctx, channel: discord.TextChannel):
        """Set the Discord channel for alerts."""
        await self.config.guild(ctx.guild).destination_channel.set(channel.id)
        await ctx.send(f"✅ Alerts will be sent to {channel.mention}")

    @detecttitle.command(name="keyword")
    async def add_keyword(self, ctx, *, keyword: str):
        """Add a keyword to look for in the title."""
        async with self.config.guild(ctx.guild).keywords() as keywords:
            if keyword not in keywords:
                keywords.append(keyword)
                await ctx.send(f"✅ Added keyword: `{keyword}`")
            else:
                await ctx.send("❌ That keyword is already in the list.")

    @detecttitle.command(name="clear")
    async def clear_keywords(self, ctx):
        """Clear all keywords. Monitoring will alert for ANY live stream."""
        await self.config.guild(ctx.guild).keywords.set([])
        await ctx.send("✅ All keywords cleared. Monitoring will now alert for ANY live stream.")

    @detecttitle.command(name="toggle")
    async def toggle(self, ctx):
        """Enable or disable monitoring."""
        enabled = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not enabled)
        status = "Enabled" if not enabled else "Disabled"
        await ctx.send(f"✅ Monitoring is now **{status}**.")

    @detecttitle.command(name="settings")
    async def show_settings(self, ctx):
        """Show current configuration."""
        data = await self.config.guild(ctx.guild).all()
        twitch = data['twitch_channel'] or "Not set"
        dest = ctx.guild.get_channel(data['destination_channel'])
        dest_mention = dest.mention if dest else "Not set"
        keywords = ", ".join([f"`{k}`" for k in data['keywords']]) if data['keywords'] else "Any stream (No keywords)"
        enabled = "Yes" if data['enabled'] else "No"
        
        embed = discord.Embed(title="DetectTitle Settings", color=0x6441A5)
        embed.add_field(name="Twitch Channel", value=twitch, inline=True)
        embed.add_field(name="Destination", value=dest_mention, inline=True)
        embed.add_field(name="Enabled", value=enabled, inline=True)
        embed.add_field(name="Keywords", value=keywords, inline=False)
        await ctx.send(embed=embed)

    @detecttitle.command(name="creds")
    async def show_creds_info(self, ctx):
        """Instructions on how to set Twitch API credentials."""
        prefix = ctx.clean_prefix
        msg = (
            "To use this cog, you need a Twitch Client ID and Client Secret.\n\n"
            "1. Go to the [Twitch Dev Console](https://dev.twitch.tv/console/apps) and create an app.\n"
            "2. Set Redirect URI to `http://localhost`.\n"
            "3. Copy your **Client ID** and generate a **Client Secret**.\n"
            "4. Run the following command in Discord (replace placeholders):\n"
            f"```{prefix}set api twitch client_id <YOUR_CLIENT_ID> client_secret <YOUR_CLIENT_SECRET>```"
        )
        await ctx.send(msg)
