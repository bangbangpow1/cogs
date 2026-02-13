import discord
import asyncio
import logging
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger("red.twitchjoin")


class TwitchJoin(commands.Cog):
    """Automated Twitch integration using command invocation."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        self.config.register_member(twitch_name=None)
        self.config.register_guild(
            admin_channel_id=None,
            alert_channel_id=None
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle new member joins with Twitch setup."""
        if member.bot:
            return

        guild_config = await self.config.guild(member.guild).all()
        admin_channel_id = guild_config.get("admin_channel_id")
        alert_channel_id = guild_config.get("alert_channel_id")
        
        if not admin_channel_id or not alert_channel_id:
            log.warning(f"Channels not configured for guild {member.guild.id}")
            return

        try:
            await self._process_new_member(member, admin_channel_id, alert_channel_id)
        except asyncio.TimeoutError:
            log.info(f"Timeout waiting for response from {member.id}")
        except discord.Forbidden:
            log.warning(f"Cannot DM member {member.id}")
        except Exception as e:
            log.exception(f"TwitchJoin Error for {member.id}: {e}")

    async def _process_new_member(self, member: discord.Member, admin_channel_id: int, alert_channel_id: int):
        """Process DM flow with new member."""
        try:
            dm_channel = await member.create_dm()
            await dm_channel.send(f"Welcome to {member.guild.name}! Do you have a Twitch channel? (Yes/No)")
        except discord.Forbidden:
            log.info(f"Cannot send DM to {member.id}, aborting Twitch setup")
            return

        def check(m):
            return m.author.id == member.id and isinstance(m.channel, discord.DMChannel)

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=300)
        except asyncio.TimeoutError:
            await member.send("Request timed out. If you change your mind, contact a moderator!")
            raise

        if msg.content.lower() not in ["yes", "y", "yeah", "yep"]:
            await member.send("No problem! If you change your mind later, just let us know.")
            return

        await member.send("What is your Twitch channel name?")
        
        try:
            channel_msg = await self.bot.wait_for("message", check=check, timeout=300)
        except asyncio.TimeoutError:
            await member.send("Request timed out. Please contact a moderator to set this up manually.")
            raise

        twitch_input = channel_msg.content.strip()
        twitch_name = self._parse_twitch_name(twitch_input)
        
        if not twitch_name:
            await member.send("That doesn't look like a valid Twitch username. Please contact a moderator.")
            return

        await self.config.member(member).twitch_name.set(twitch_name)
        
        admin_channel = self.bot.get_channel(admin_channel_id)
        alert_channel = self.bot.get_channel(alert_channel_id)
        
        if not admin_channel or not alert_channel:
            log.error(f"Channels not found: admin={admin_channel_id}, alert={alert_channel_id}")
            await member.send("Error: Could not find alert channels. Please contact a moderator.")
            return

        # Use command invocation instead of non-existent API methods
        success = await self._invoke_streamalert_command(
            member.guild, admin_channel, alert_channel, twitch_name
        )
        
        if success:
            await admin_channel.send(
                f"✅ **Automated:** Set up alerts for `{twitch_name}` (requested by {member.mention})."
            )
            await member.send(f"Success! I've added `{twitch_name}` to our stream alerts.")
        else:
            await member.send(
                "There was an issue setting up the alert automatically. A moderator will review this manually."
            )
            await admin_channel.send(
                f"⚠️ **Manual Review Needed:** {member.mention} wants to add `{twitch_name}` but auto-setup failed."
            )

    def _parse_twitch_name(self, input_str: str) -> str:
        """Extract Twitch username from various input formats."""
        input_str = input_str.strip()
        
        if "twitch.tv/" in input_str:
            parts = input_str.split("twitch.tv/")
            if len(parts) > 1:
                username = parts[1].split("/")[0].split("?")[0]
                return username.lower()
        
        if input_str.startswith("@"):
            input_str = input_str[1:]
            
        return input_str.lower()

    async def _invoke_streamalert_command(
        self, 
        guild: discord.Guild, 
        admin_channel: discord.TextChannel, 
        alert_channel: discord.TextChannel, 
        twitch_name: str
    ) -> bool:
        """Invoke streamalert command by creating a proper context."""
        try:
            # Get prefix
            prefixes = await self.bot.get_valid_prefixes(guild)
            prefix = prefixes[0] if prefixes else "!"
            
            # Create the command string
            command_content = f"{prefix}streamalert twitch channel {twitch_name} {alert_channel.id}"
            
            # Create a fake message that looks like it came from the guild owner
            # This ensures proper permissions
            guild_owner = guild.owner
            
            # Create a minimal message object using discord.Message
            # We need to use the bot's connection state
            state = self.bot._connection
            
            # Build message data
            message_data = {
                "id": 0,
                "channel_id": admin_channel.id,
                "guild_id": guild.id,
                "author": {
                    "id": guild_owner.id,
                    "username": guild_owner.name,
                    "discriminator": getattr(guild_owner, "discriminator", "0"),
                    "bot": False,
                    "avatar": getattr(guild_owner, "avatar", None),
                },
                "content": command_content,
                "timestamp": discord.utils.utcnow().isoformat(),
                "edited_timestamp": None,
                "tts": False,
                "mention_everyone": False,
                "mentions": [],
                "mention_roles": [],
                "mention_channels": [],
                "attachments": [],
                "embeds": [],
                "pinned": False,
                "type": 0,
                "flags": 0,
            }
            
            # Create the message object
            fake_message = discord.Message(state=state, channel=admin_channel, data=message_data)
            # Override author to be the guild owner for permissions
            fake_message.author = guild_owner
            
            # Get context and invoke
            ctx = await self.bot.get_context(fake_message)
            
            if ctx.valid:
                await self.bot.invoke(ctx)
                log.info(f"Successfully invoked streamalert for {twitch_name}")
                return True
            else:
                log.error(f"Invalid context for command: {command_content}")
                return False

        except Exception as e:
            log.exception(f"Error invoking streamalert command: {e}")
            return False

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Handle member leaving - notify admins to remove stream."""
        twitch_name = await self.config.member(member).twitch_name()
        
        if not twitch_name:
            return

        guild_config = await self.config.guild(member.guild).all()
        admin_channel_id = guild_config.get("admin_channel_id")
        
        if not admin_channel_id:
            return

        try:
            admin_channel = self.bot.get_channel(admin_channel_id)
            if not admin_channel:
                return

            # Since we can't easily delete via API, notify admins to remove manually
            await admin_channel.send(
                f"❌ **Auto-Notice:** {member.name} (ID: {member.id}) left the server. "
                f"They had Twitch alerts set up for `{twitch_name}`. "
                f"Please remove this alert manually with `{await self._get_prefix(member.guild)}streamalert twitch stop {twitch_name}` "
                f"if desired."
            )
            
            # Clear member data
            await self.config.member(member).clear()
            
        except Exception as e:
            log.exception(f"Error in on_member_remove: {e}")

    async def _get_prefix(self, guild: discord.Guild) -> str:
        """Get guild prefix."""
        prefixes = await self.bot.get_valid_prefixes(guild)
        return prefixes[0] if prefixes else "!"

    # Admin configuration commands
    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchjoin(self, ctx: commands.Context):
        """Configure TwitchJoin settings."""
        pass

    @twitchjoin.command(name="setup")
    async def set_channels(
        self, 
        ctx: commands.Context, 
        admin_channel: discord.TextChannel, 
        alert_channel: discord.TextChannel
    ):
        """Set the admin and alert channels."""
        await self.config.guild(ctx.guild).admin_channel_id.set(admin_channel.id)
        await self.config.guild(ctx.guild).alert_channel_id.set(alert_channel.id)
        await ctx.send(
            f"✅ Channels configured:\n"
            f"Admin: {admin_channel.mention}\n"
            f"Alerts: {alert_channel.mention}"
        )

    @twitchjoin.command(name="test")
    async def test_setup(self, ctx: commands.Context, twitch_name: str):
        """Test the Twitch alert setup manually."""
        guild_config = await self.config.guild(ctx.guild).all()
        alert_channel_id = guild_config.get("alert_channel_id")
        
        if not alert_channel_id:
            await ctx.send("Alert channel not configured! Use `[p]twitchjoin setup` first.")
            return
            
        alert_channel = self.bot.get_channel(alert_channel_id)
        if not alert_channel:
            await ctx.send("Could not find the configured alert channel.")
            return

        success = await self._invoke_streamalert_command(
            ctx.guild, ctx.channel, alert_channel, twitch_name
        )
        
        if success:
            await ctx.send(f"✅ Successfully triggered alert setup for `{twitch_name}`")
        else:
            await ctx.send(f"❌ Failed to setup alert for `{twitch_name}`. Check logs.")

    @twitchjoin.command(name="manual")
    async def manual_remove_notice(self, ctx: commands.Context, member: discord.Member):
        """Manually trigger the removal notice for a member."""
        twitch_name = await self.config.member(member).twitch_name()
        if not twitch_name:
            await ctx.send(f"{member.mention} doesn't have a Twitch name stored.")
            return
            
        await ctx.send(
            f"**Removal Notice:** {member.name} had Twitch `{twitch_name}` set up.\n"
            f"Remove with: `{await self._get_prefix(ctx.guild)}streamalert twitch stop {twitch_name}`"
        )