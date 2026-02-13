import discord
import asyncio
import copy
import logging
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger("red.twitchjoin")

class TwitchJoin(commands.Cog):
    """Automated Twitch integration for new server members."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        default_guild = {
            "admin_channel_id": None,
            "alert_channel_id": None
        }
        default_member = {
            "twitch_name": None
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        guild_data = await self.config.guild(member.guild).all()
        if not guild_data["admin_channel_id"] or not guild_data["alert_channel_id"]:
            return

        try:
            # Step 1: DM the user
            await member.send(f"Welcome to {member.guild.name}! Do you have a Twitch channel? (Yes/No)")

            def check(m):
                return m.author.id == member.id and isinstance(m.channel, discord.DMChannel)

            msg = await self.bot.wait_for("message", check=check, timeout=300)
            
            if msg.content.lower() in ["yes", "y", "yeah", "yep"]:
                await member.send("What is your Twitch channel name?")
                name_msg = await self.bot.wait_for("message", check=check, timeout=300)
                twitch_name = name_msg.content.split('/')[-1].strip().lower()

                # Save for later
                await self.config.member(member).twitch_name.set(twitch_name)

                # Step 2: Run the automated command
                await self._automate_stream_add(member, twitch_name, guild_data)

        except asyncio.TimeoutError:
            pass
        except discord.Forbidden:
            log.info(f"Could not DM user {member.name} - DMs closed.")
        except Exception as e:
            log.error(f"Error in on_member_join: {e}")

    async def _automate_stream_add(self, member: discord.Member, twitch_name: str, guild_data: dict):
        """Builds a context to trigger the Streams cog."""
        admin_chan = self.bot.get_channel(guild_data["admin_channel_id"])
        if not admin_chan:
            return

        prefix = (await self.bot.get_valid_prefixes(member.guild))[0]
        # Using the exact command you specified
        cmd_text = f"{prefix}streamalert twitch channel {twitch_name} {guild_data['alert_channel_id']}"

        # Build the fake message data
        data = {
            "id": discord.utils.time_snowflake(discord.utils.utcnow()),
            "content": cmd_text,
            "author": {
                "id": member.guild.owner_id,
                "username": member.guild.owner.name,
                "discriminator": member.guild.owner.discriminator,
                "avatar": member.guild.owner.avatar,
                "bot": False,
            },
            "channel_id": admin_chan.id,
            "guild_id": member.guild.id,
            "attachments": [],
            "embeds": [],
            "mentions": [],
            "mention_roles": [],
            "pinned": False,
            "mention_everyone": False,
            "tts": False,
            "type": 0,
            "timestamp": discord.utils.utcnow().isoformat(),
            "edited_timestamp": None,
        }

        fake_msg = discord.Message(state=self.bot._connection, channel=admin_chan, data=data)
        
        # Crucial: Ensure the author is a Member object, not just a User object
        fake_msg.author = member.guild.owner 

        ctx = await self.bot.get_context(fake_msg)
        
        if ctx.valid:
            await self.bot.invoke(ctx)
            await admin_chan.send(f"🤖 **Auto-Invoking:** `{cmd_text}`")
            await member.send(f"Success! I've sent the request to add `{twitch_name}` to alerts.")
        else:
            log.error(f"Could not create a valid context for command: {cmd_text}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Notifies admins to remove stream alerts when someone leaves."""
        twitch_name = await self.config.member(member).twitch_name()
        if not twitch_name:
            return

        admin_id = await self.config.guild(member.guild).admin_channel_id()
        admin_chan = self.bot.get_channel(admin_id)

        if admin_chan:
            prefix = (await self.bot.get_valid_prefixes(member.guild))[0]
            await admin_chan.send(
                f"❌ **Member Left:** {member.name} has left the server.\n"
                f"To stop their Twitch alerts, use: `{prefix}streamalert twitch stop {twitch_name}`"
            )
            await self.config.member(member).clear()

    # --- ADMIN COMMANDS ---

    @commands.group(name="twitchjoin")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchjoin(self, ctx: commands.Context):
        """Manage TwitchJoin automation settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @twitchjoin.command(name="setup")
    async def setup_channels(self, ctx, admin_channel: discord.TextChannel, alert_channel: discord.TextChannel):
        """Configure the admin log and the alert destination channel."""
        await self.config.guild(ctx.guild).admin_channel_id.set(admin_channel.id)
        await self.config.guild(ctx.guild).alert_channel_id.set(alert_channel.id)
        await ctx.send(
            f"✅ **Configuration Saved**\n"
            f"Admin Logs: {admin_channel.mention}\n"
            f"Stream Alerts: {alert_channel.mention}"
        )

    @twitchjoin.command(name="test")
    async def test_setup(self, ctx, twitch_name: str):
        """Test the automation by trying to add a twitch channel now."""
        guild_data = await self.config.guild(ctx.guild).all()
        if not guild_data["admin_channel_id"]:
            return await ctx.send("Please run `[p]twitchjoin setup` first!")
        
        await self._automate_stream_add(ctx.author, twitch_name, guild_data)
        await ctx.send(f"Verification sent to admin channel for `{twitch_name}`.")