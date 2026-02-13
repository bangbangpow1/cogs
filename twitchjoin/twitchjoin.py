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
        
        # Default settings
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
        """Triggered when a new member joins the server."""
        if member.bot:
            return

        # Check if the guild has been configured first
        guild_data = await self.config.guild(member.guild).all()
        if not guild_data["admin_channel_id"] or not guild_data["alert_channel_id"]:
            log.warning(f"TwitchJoin is not configured for guild: {member.guild.name}")
            return

        try:
            # Send DM to user
            await member.send(f"Welcome to {member.guild.name}! Do you have a Twitch channel? (Yes/No)")

            def check(m):
                return m.author.id == member.id and isinstance(m.channel, discord.DMChannel)

            # Wait for Yes/No
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            
            if msg.content.lower() in ["yes", "y", "yeah", "yep"]:
                await member.send("What is your Twitch channel name?")
                
                # Wait for Twitch Name
                name_msg = await self.bot.wait_for("message", check=check, timeout=300)
                twitch_name = name_msg.content.split('/')[-1].strip().lower()

                # Save the name for removal logic later
                await self.config.member(member).twitch_name.set(twitch_name)

                # Process the automated command
                await self._automate_stream_add(member, twitch_name, guild_data)

        except asyncio.TimeoutError:
            log.info(f"User {member.name} timed out during TwitchJoin DMs.")
        except discord.Forbidden:
            log.info(f"Could not DM user {member.name} (DMs likely closed).")
        except Exception as e:
            log.error(f"Error in TwitchJoin on_member_join: {e}")

    async def _automate_stream_add(self, member: discord.Member, twitch_name: str, guild_data: dict):
        """Fakes a command from the owner to trigger the Streams cog."""
        admin_chan = self.bot.get_channel(guild_data["admin_channel_id"])
        if not admin_chan:
            return

        prefix = (await self.bot.get_valid_prefixes(member.guild))[0]
        # Format: [p]streamalert twitch channel <name> <id>
        cmd_text = f"{prefix}streamalert twitch channel {twitch_name} {guild_data['alert_channel_id']}"

        # Build fake message
        fake_msg = copy.copy(member.guild.owner.top_role.members[0].mention) # Placeholder
        # Actually creating a message object from scratch
        fake_msg = discord.Message(
            state=self.bot._connection,
            channel=admin_chan,
            data={
                'content': cmd_text,
                'author': {'id': member.guild.owner_id},
                'id': 1
            }
        )
        fake_msg.author = member.guild.owner
        fake_msg.guild = member.guild

        # Execute
        ctx = await self.bot.get_context(fake_msg)
        if ctx.valid:
            await self.bot.invoke(ctx)
            await admin_chan.send(f"✅ **Automated:** Setting up Twitch alerts for `{twitch_name}` for new member {member.mention}.")
            await member.send(f"Success! I've added `{twitch_name}` to our stream alerts.")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Removes the stream notice when a member leaves."""
        twitch_name = await self.config.member(member).twitch_name()
        if not twitch_name:
            return

        admin_id = await self.config.guild(member.guild).admin_channel_id()
        admin_chan = self.bot.get_channel(admin_id)

        if admin_chan:
            prefix = (await self.bot.get_valid_prefixes(member.guild))[0]
            await admin_chan.send(
                f"❌ **Auto-Notice:** {member.name} left. If you want to stop their alerts, "
                f"use `{prefix}streamalert twitch stop {twitch_name}`."
            )
            # Clear data
            await self.config.member(member).clear()

    # --- ADMIN SETTINGS ---
    @commands.group(name="twitchjoin")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchjoin(self, ctx: commands.Context):
        """Settings for automated Twitch joining."""
        pass

    @twitchjoin.command(name="setup")
    async def setup_channels(self, ctx, admin_channel: discord.TextChannel, alert_channel: discord.TextChannel):
        """Set the channels for admin logs and stream alerts."""
        await self.config.guild(ctx.guild).admin_channel_id.set(admin_channel.id)
        await self.config.guild(ctx.guild).alert_channel_id.set(alert_channel.id)
        await ctx.send(f"✅ **Configured!**\nAdmin logs: {admin_channel.mention}\nAlerts post to: {alert_channel.mention}")

    @twitchjoin.command(name="test")
    async def test_invoke(self, ctx, twitch_name: str):
        """Manually test if the bot can trigger the Streams cog."""
        guild_data = await self.config.guild(ctx.guild).all()
        if not guild_data["alert_channel_id"]:
            return await ctx.send("Please run setup first!")
        
        await self._automate_stream_add(ctx.author, twitch_name, guild_data)
        await ctx.send(f"Test command sent for `{twitch_name}`.")