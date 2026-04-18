import discord
import aiohttp
import asyncio
import logging
from typing import Optional, List, Dict
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger("red.detecttitle")

class DetectTitle(commands.Cog):
    """Monitor multiple Twitch channels for title keywords with auto-roles."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
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

    async def _handle_role(self, guild: discord.Guild, monitor_data: dict, add: bool):
        """Helper to add or remove the live role from the associated user."""
        user_id = monitor_data.get("user_id")
        role_id = monitor_data.get("role_id")
        if not user_id or not role_id:
            return

        member = guild.get_member(user_id)
        role = guild.get_role(role_id)
        if not member or not role:
            return

        try:
            if add:
                if role not in member.roles:
                    await member.add_roles(role, reason="Stream live (keyword match)")
            else:
                if role in member.roles:
                    await member.remove_roles(role, reason="Stream offline or title mismatch")
        except discord.Forbidden:
            log.warning(f"Failed to manage role for {member.name} in {guild.name}: Missing Permissions")
        except Exception as e:
            log.error(f"Error managing role for {member.name}: {e}")

    async def _delete_old_alert_only(self, guild: discord.Guild, twitch_login: str, monitor_data: dict):
        """Performs Discord cleanup (role/message) WITHOUT touching config."""
        # Handle Role Removal
        await self._handle_role(guild, monitor_data, add=False)

        # Handle Message Deletion
        msg_id = monitor_data.get("last_message_id")
        if msg_id:
            channel = guild.get_channel(monitor_data["destination_channel"])
            if channel:
                try:
                    msg = await channel.fetch_message(msg_id)
                    await msg.delete()
                except discord.NotFound:
                    pass
                except Exception as e:
                    log.warning(f"Could not delete alert for {twitch_login}: {e}")

    async def _check_streams(self, guild_id: int, monitors: Dict[str, dict]):
        client_id, token = await self._get_twitch_auth()
        if not client_id or not token: return

        logins = [login for login, data in monitors.items() if data.get("enabled", True)]
        if not logins: return

        guild = self.bot.get_guild(guild_id)
        if not guild: return

        # We will collect updates and save them all at once at the end
        updates = {}

        for i in range(0, len(logins), 100):
            batch = logins[i:i+100]
            query_string = "&user_login=".join(batch)
            url = f"https://api.twitch.tv/helix/streams?user_login={query_string}"
            headers = {"Client-ID": client_id, "Authorization": f"Bearer {token}"}
            
            session = await self._get_session()
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200: continue
                    
                    data = await resp.json()
                    active_streams = {s["user_login"].lower(): s for s in data.get("data", [])}
                    
                    for login in batch:
                        monitor_data = monitors[login]
                        stream = active_streams.get(login)
                        last_id = monitor_data.get("last_stream_id")

                        if not stream:
                            if last_id is not None:
                                await self._delete_old_alert_only(guild, login, monitor_data)
                                updates[login] = {"last_stream_id": None, "last_message_id": None}
                            continue

                        stream_id = stream["id"]
                        title = stream["title"].lower()
                        keywords = [k.lower() for k in monitor_data.get("keywords", [])]
                        match = not keywords or any(kw in title for kw in keywords)

                        if match:
                            if stream_id != last_id:
                                await self._delete_old_alert_only(guild, login, monitor_data)
                                await self._handle_role(guild, monitor_data, add=True)
                                msg = await self._post_alert(guild, monitor_data, stream)
                                updates[login] = {
                                    "last_stream_id": stream_id,
                                    "last_message_id": msg.id if msg else None
                                }
                        else:
                            if last_id is not None:
                                await self._delete_old_alert_only(guild, login, monitor_data)
                                updates[login] = {"last_stream_id": None, "last_message_id": None}
            except Exception as e:
                log.error(f"Error checking batch for guild {guild_id}: {e}")

        if updates:
            async with self.config.guild(guild).monitors() as m:
                for login, data in updates.items():
                    if login in m:
                        m[login].update(data)

    async def _post_alert(self, guild: discord.Guild, monitor_data: dict, stream: dict):
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
                    if monitors: await self._check_streams(guild_id, monitors)
            except Exception as e:
                log.exception(f"Error in monitor loop: {e}")
            await asyncio.sleep(60)

    @commands.group(name="detecttitle", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def detecttitle(self, ctx):
        """Manage Twitch monitors with auto-roles."""
        await ctx.send_help()

    @detecttitle.command(name="setup")
    async def setup(self, ctx, twitch_channel: str, destination_channel: discord.TextChannel, keyword: str, member: discord.Member, role: discord.Role):
        """Add a monitor with user and role association.
        
        Example: `[p]detecttitle setup MyChannel #alerts "Playing Game" @Gabu @LiveRole`
        """
        login = twitch_channel.split('/')[-1].lower()
        async with self.config.guild(ctx.guild).monitors() as monitors:
            monitors[login] = {
                "destination_channel": destination_channel.id,
                "keywords": [keyword],
                "enabled": True,
                "user_id": member.id,
                "role_id": role.id,
                "last_stream_id": None,
                "last_message_id": None
            }
        
        await ctx.send(f"✅ **Monitor set for `{login}`!**\n"
                       f"• **Destination:** {destination_channel.mention}\n"
                       f"• **User:** {member.mention}\n"
                       f"• **Role:** {role.name}\n"
                       f"• **Keyword:** \"{keyword}\"")

    @detecttitle.command(name="remove")
    async def remove_monitor(self, ctx, twitch_channel: str):
        """Stop monitoring a specific Twitch channel."""
        login = twitch_channel.split('/')[-1].lower()
        async with self.config.guild(ctx.guild).monitors() as monitors:
            if login in monitors:
                # Cleanup Discord side only
                await self._delete_old_alert_only(ctx.guild, login, monitors[login])
                # Delete from config
                del monitors[login]
                await ctx.send(f"✅ Stopped monitoring `{login}`.")
            else:
                await ctx.send(f"❌ I am not monitoring `{login}`.")

    @detecttitle.command(name="clearall")
    async def clear_all(self, ctx):
        """Wipe ALL monitors for this server."""
        await self.config.guild(ctx.guild).monitors.set({})
        await ctx.send("✅ All monitors have been wiped for this server.")

    @detecttitle.command(name="list")
    async def list_monitors(self, ctx):
        """List all active Twitch monitors."""
        monitors = await self.config.guild(ctx.guild).monitors()
        if not monitors: return await ctx.send("No monitors configured.")

        embed = discord.Embed(title="Active Twitch Monitors", color=0x6441A5)
        for login, data in monitors.items():
            user = ctx.guild.get_member(data.get("user_id"))
            status = "🟢 Enabled" if data.get("enabled", True) else "🔴 Disabled"
            
            val = f"**User:** {user.mention if user else 'Not found'}\n" \
                  f"**Keywords:** {', '.join(data['keywords']) or 'Any'}"
            embed.add_field(name=f"{login} ({status})", value=val, inline=False)
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
