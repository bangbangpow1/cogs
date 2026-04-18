import discord
import aiohttp
import asyncio
import logging
from typing import Optional, List, Dict
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger("red.detecttitle")

class DetectTitle(commands.Cog):
    """Monitor multiple Twitch channels for specific title keywords when they go live."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        # New structure: monitors is a dict where key is twitch_login
        default_guild = {
            "monitors": {}
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

    async def _delete_old_alert(self, guild_id: int, twitch_login: str, monitor_data: dict):
        """Deletes the last posted alert message for a specific monitor."""
        msg_id = monitor_data.get("last_message_id")
        if not msg_id:
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        channel = guild.get_channel(monitor_data["destination_channel"])
        if not channel:
            return

        try:
            msg = await channel.fetch_message(msg_id)
            await msg.delete()
        except discord.NotFound:
            pass
        except Exception as e:
            log.warning(f"Could not delete old alert for {twitch_login} in guild {guild_id}: {e}")
        finally:
            async with self.config.guild_from_id(guild_id).monitors() as monitors:
                if twitch_login in monitors:
                    monitors[twitch_login]["last_message_id"] = None

    async def _check_streams(self, guild_id: int, monitors: Dict[str, dict]):
        client_id, token = await self._get_twitch_auth()
        if not client_id or not token:
            return

        # Prepare batch request for efficiency (Twitch allows up to 100 logins)
        logins = [login for login, data in monitors.items() if data.get("enabled", True)]
        if not logins:
            return

        # Twitch API limits stream checks to batches of 100
        for i in range(0, len(logins), 100):
            batch = logins[i:i+100]
            query_string = "&user_login=".join(batch)
            url = f"https://api.twitch.tv/helix/streams?user_login={query_string}"
            headers = {"Client-ID": client_id, "Authorization": f"Bearer {token}"}
            
            session = await self._get_session()
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        continue
                    
                    data = await resp.json()
                    active_streams = {s["user_login"].lower(): s for s in data.get("data", [])}
                    
                    for login in batch:
                        monitor_data = monitors[login]
                        stream = active_streams.get(login)
                        
                        if not stream:
                            # Stream is offline
                            if monitor_data.get("last_stream_id") is not None:
                                await self._delete_old_alert(guild_id, login, monitor_data)
                                async with self.config.guild_from_id(guild_id).monitors() as m:
                                    if login in m:
                                        m[login]["last_stream_id"] = None
                            continue

                        # Stream is online
                        stream_id = stream["id"]
                        if stream_id == monitor_data.get("last_stream_id"):
                            continue

                        title = stream["title"].lower()
                        keywords = [k.lower() for k in monitor_data.get("keywords", [])]
                        
                        match = not keywords or any(kw in title for kw in keywords)
                        
                        if match:
                            await self._delete_old_alert(guild_id, login, monitor_data)
                            msg = await self._post_alert(guild_id, login, monitor_data, stream)
                            async with self.config.guild_from_id(guild_id).monitors() as m:
                                if login in m:
                                    m[login]["last_stream_id"] = stream_id
                                    if msg:
                                        m[login]["last_message_id"] = msg.id
            except Exception as e:
                log.error(f"Error checking batch for guild {guild_id}: {e}")

    async def _post_alert(self, guild_id: int, twitch_login: str, monitor_data: dict, stream: dict):
        guild = self.bot.get_guild(guild_id)
        if not guild: return None
        
        channel = guild.get_channel(monitor_data["destination_channel"])
        if not channel: return None

        thumbnail = stream["thumbnail_url"].replace("{width}", "1280").replace("{height}", "720")
        stream_url = f"https://www.twitch.tv/{stream['user_login']}"

        embed = discord.Embed(
            title=f"{stream['user_name']} is now live!",
            description=f"**{stream['title']}**\n\nhey come check out this channel\n{stream_url}",
            color=0x6441A5
        )
        embed.set_image(url=thumbnail)
        embed.set_footer(text="Twitch Stream Alert")
        
        try:
            return await channel.send(embed=embed)
        except:
            return None

    async def _monitor_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                all_guilds = await self.config.all_guilds()
                for guild_id, settings in all_guilds.items():
                    monitors = settings.get("monitors", {})
                    if monitors:
                        await self._check_streams(guild_id, monitors)
            except Exception as e:
                log.exception(f"Error in monitor loop: {e}")
            await asyncio.sleep(60)

    @commands.group(name="detecttitle", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def detecttitle(self, ctx):
        """Manage multiple Twitch title-detection monitors."""
        await ctx.send_help()

    @detecttitle.command(name="setup")
    async def setup(self, ctx, twitch_channel: str, destination_channel: discord.TextChannel, *, keyword: str):
        """Add or update a Twitch channel monitor.
        
        Example: `[p]detecttitle setup MyChannel #alerts "Playing Game"`
        """
        login = twitch_channel.split('/')[-1].lower()
        async with self.config.guild(ctx.guild).monitors() as monitors:
            monitors[login] = {
                "destination_channel": destination_channel.id,
                "keywords": [keyword],
                "enabled": True,
                "last_stream_id": None,
                "last_message_id": None
            }
        
        await ctx.send(f"✅ **Monitor set for `{login}`!**\n"
                       f"• **Destination:** {destination_channel.mention}\n"
                       f"• **Keyword:** \"{keyword}\"")
        
        tokens = await self.bot.get_shared_api_tokens("twitch")
        if not tokens.get("client_id") or not tokens.get("client_secret"):
            await ctx.send(f"⚠️ **Note:** Twitch API tokens are not set! Use `{ctx.clean_prefix}detecttitle creds`.")

    @detecttitle.command(name="remove")
    async def remove_monitor(self, ctx, twitch_channel: str):
        """Stop monitoring a specific Twitch channel."""
        login = twitch_channel.split('/')[-1].lower()
        async with self.config.guild(ctx.guild).monitors() as monitors:
            if login in monitors:
                del monitors[login]
                await ctx.send(f"✅ Stopped monitoring `{login}`.")
            else:
                await ctx.send(f"❌ I am not monitoring `{login}`.")

    @detecttitle.command(name="list")
    async def list_monitors(self, ctx):
        """List all active Twitch monitors in this server."""
        monitors = await self.config.guild(ctx.guild).monitors()
        if not monitors:
            return await ctx.send("No channels are currently being monitored.")

        embed = discord.Embed(title="Active Twitch Monitors", color=0x6441A5)
        for login, data in monitors.items():
            dest = ctx.guild.get_channel(data["destination_channel"])
            dest_name = dest.mention if dest else "Unknown Channel"
            kws = ", ".join([f"`{k}`" for k in data["keywords"]]) or "Any stream"
            status = "🟢 Enabled" if data.get("enabled", True) else "🔴 Disabled"
            
            embed.add_field(
                name=f"{login} ({status})",
                value=f"**Dest:** {dest_name}\n**Keywords:** {kws}",
                inline=False
            )
        await ctx.send(embed=embed)

    @detecttitle.command(name="addkeyword")
    async def add_keyword(self, ctx, twitch_channel: str, *, keyword: str):
        """Add a keyword to an existing monitor."""
        login = twitch_channel.split('/')[-1].lower()
        async with self.config.guild(ctx.guild).monitors() as monitors:
            if login not in monitors:
                return await ctx.send(f"❌ I am not monitoring `{login}`. Use `setup` first.")
            if keyword not in monitors[login]["keywords"]:
                monitors[login]["keywords"].append(keyword)
                await ctx.send(f"✅ Added keyword `{keyword}` to `{login}`.")
            else:
                await ctx.send(f"❌ That keyword is already set for `{login}`.")

    @detecttitle.command(name="clearkeywords")
    async def clear_keywords(self, ctx, twitch_channel: str):
        """Remove all keywords for a monitor (will alert for any stream)."""
        login = twitch_channel.split('/')[-1].lower()
        async with self.config.guild(ctx.guild).monitors() as monitors:
            if login in monitors:
                monitors[login]["keywords"] = []
                await ctx.send(f"✅ Keywords cleared for `{login}`. Alerting for all streams.")
            else:
                await ctx.send(f"❌ I am not monitoring `{login}`.")

    @detecttitle.command(name="toggle")
    async def toggle_monitor(self, ctx, twitch_channel: str):
        """Enable or disable a specific monitor."""
        login = twitch_channel.split('/')[-1].lower()
        async with self.config.guild(ctx.guild).monitors() as monitors:
            if login in monitors:
                current = monitors[login].get("enabled", True)
                monitors[login]["enabled"] = not current
                status = "Enabled" if not current else "Disabled"
                await ctx.send(f"✅ Monitor for `{login}` is now **{status}**.")
            else:
                await ctx.send(f"❌ I am not monitoring `{login}`.")

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
