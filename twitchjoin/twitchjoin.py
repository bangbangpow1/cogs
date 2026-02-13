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
        """Triggered when a member joins; DMs them to ask for Twitch."""
        if member.bot:
            return

        guild_data = await self.config.guild(member.guild).all()
        if not guild_data["admin_channel_id"] or not guild_data["alert_channel_id"]:
            return

        try:
            await member.send(f"Welcome to {member.guild.name}! Do you have a Twitch channel? (Yes/No)")

            def check(m):
                return m.author.id == member.id and isinstance(m.channel, discord.DMChannel)

            msg = await self.bot.wait_for("message", check=check, timeout=300)
            
            if msg.content.lower() in ["yes", "y", "yeah", "yep"]:
                await member.send("What is your Twitch channel name?")
                name_msg = await self.bot.wait_for("message", check=check, timeout=300)
                twitch_name = name_msg.content.split('/')[-1].strip().lower()

                # Save the name so we can invoke the toggle again when they leave
                await self.config.member(member).twitch_name.set(twitch_name)

                # Invoke the command to ADD
                await self._invoke_stream_toggle(member, twitch_name, guild_data, is_join=True)

        except asyncio.TimeoutError:
            pass
        except discord.Forbidden:
            log.info(f"Could not DM user {member.name} - DMs closed.")
        except Exception as e:
            log.error(f"Error in on_member_join: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Triggered when a member leaves; Invokes the toggle command to REMOVE."""
        twitch_name = await self.config.member(member).twitch_name()
        if not twitch_name:
            return

        guild_data = await self.config.guild(member.guild).all()
        if not guild_data["admin_channel_id"] or not guild_data["alert_channel_id"]:
            return

        # Invoke the exact same command to TOGGLE it off
        await self._invoke_stream_toggle(member, twitch_name, guild_data, is_join=False)
        
        # Clear data after removal
        await self.config.member(member).clear()

    async def _invoke_stream_toggle(self, member: discord.Member, twitch_name: str, guild_data: dict, is_join: bool):
        """Creates a fake context to run the streamalert toggle command."""
        admin_chan = self.bot.get_channel(guild_data["admin_channel_id"])
        if not admin_chan:
            return

        prefix = (await self.bot.get_valid_prefixes(member.guild))[0]
        
        # EXACT command format for both add and remove as requested
        cmd_text = f"{prefix}streamalert twitch channel {twitch_name} {guild_data['alert_channel_id']}"

        # Construct the message data
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
        fake_msg.author = member.guild.owner 

        ctx = await self.bot.get_context(fake_msg)
        
        if ctx.valid:
            await self.bot.invoke(ctx)
            
            # Different log message for the admin channel to keep track
            log_msg = "Adding" if is_join else "Removing"
            emoji = "✅" if is_join else "❌"
            await admin_chan.send(f"{emoji} **Automated:** {log_msg} Twitch alerts for `{twitch_name}` (Command: `{cmd_text}`)")
            
            if is_join:
                try:
                    await member.send(f"Success! I've added `{twitch_name}` to our stream alerts.")
                except discord.Forbidden:
                    pass

    # --- ADMIN SETTINGS ---

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
        """Manually trigger the toggle command."""
        guild_data = await self.config.guild(ctx.guild).all()
        await self._invoke_stream_toggle(ctx.author, twitch_name, guild_data, is_join=True)
        await ctx.send(f"Toggle command for `{twitch_name}` sent to admin channel.")