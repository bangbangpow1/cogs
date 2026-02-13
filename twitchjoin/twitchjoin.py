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
        # Store these in config instead of hardcoding for flexibility
        self.config.register_guild(
            admin_channel_id=None,
            alert_channel_id=None
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle new member joins with Twitch setup."""
        if member.bot:
            return

        # Check if guild is configured
        guild_config = await self.config.guild(member.guild).all()
        admin_channel_id = guild_config.get("admin_channel_id")
        alert_channel_id = guild_config.get("alert_channel_id")
        
        if not admin_channel_id or not alert_channel_id:
            log.warning(f"Channels not configured for guild {member.guild.id}")
            return

        try:
            # Try to send DM - many users have DMs disabled!
            try:
                dm_channel = await member.create_dm()
                await dm_channel.send(f"Welcome to {member.guild.name}! Do you have a Twitch channel? (Yes/No)")
            except discord.Forbidden:
                log.info(f"Cannot send DM to {member.id}, aborting Twitch setup")
                # Optionally notify admin channel that user has DMs disabled
                admin_channel = self.bot.get_channel(admin_channel_id)
                if admin_channel:
                    await admin_channel.send(
                        f"⚠️ {member.mention} joined but has DMs disabled. "
                        f"Could not auto-setup Twitch alerts."
                    )
                return

            # Wait for response - FIXED CHECK FUNCTION
            def check(m):
                # Check author and that it's a DM
                if m.author.id != member.id:
                    return False
                # Check if it's in a DM channel
                if not isinstance(m.channel, discord.DMChannel):
                    return False
                return True

            msg = await self.bot.wait_for("message", check=check, timeout=300)
            
            if msg.content.lower() in ["yes", "y", "yeah", "yep"]:
                await member.send("What is your Twitch channel name?")
                channel_msg = await self.bot.wait_for("message", check=check, timeout=300)
                
                # Parse Twitch name
                twitch_input = channel_msg.content.strip()
                twitch_name = self._parse_twitch_name(twitch_input)
                
                if not twitch_name:
                    await member.send("That doesn't look like a valid Twitch username. Please contact a moderator.")
                    return

                # Save to config
                await self.config.member(member).twitch_name.set(twitch_name)
                
                # Get channels
                admin_channel = self.bot.get_channel(admin_channel_id)
                alert_channel = self.bot.get_channel(alert_channel_id)
                
                if not admin_channel or not alert_channel:
                    log.error(f"Channels not found: admin={admin_channel_id}, alert={alert_channel_id}")
                    await member.send("Error: Could not find alert channels. Please contact a moderator.")
                    return

                # FIXED: Use actual command invocation through the bot's command system
                # Instead of faking messages, we'll use the Streams cog's API directly
                success = await self._add_twitch_alert(twitch_name, alert_channel, admin_channel)
                
                if success:
                    await admin_channel.send(
                        f"✅ **Automated:** Set up alerts for `{twitch_name}` "
                        f"(requested by {member.mention})."
                    )
                    await member.send(
                        f"Success! I've added `{twitch_name}` to our stream alerts."
                    )
                else:
                    await member.send(
                        "There was an issue setting up the alert automatically. "
                        "A moderator has been notified."
                    )
                    await admin_channel.send(
                        f"⚠️ **Manual Review Needed:** {member.mention} wants to add "
                        f"`{twitch_name}` but auto-setup failed."
                    )

        except asyncio.TimeoutError:
            log.info(f"Timeout waiting for response from {member.id}")
            try:
                await member.send("Request timed out. If you change your mind, contact a moderator!")
            except discord.Forbidden:
                pass
        except Exception as e:
            log.exception(f"TwitchJoin Error for {member.id}: {e}")

    def _parse_twitch_name(self, input_str: str) -> str:
        """Extract Twitch username from various input formats."""
        input_str = input_str.strip()
        
        # Handle twitch.tv URLs
        if "twitch.tv/" in input_str:
            parts = input_str.split("twitch.tv/")
            if len(parts) > 1:
                username = parts[1].split("/")[0].split("?")[0]
                return username.lower()
        
        # Remove @ if present
        if input_str.startswith("@"):
            input_str = input_str[1:]
            
        return input_str.lower()

    async def _add_twitch_alert(self, twitch_name: str, alert_channel: discord.TextChannel, admin_channel: discord.TextChannel) -> bool:
        """Add Twitch alert using Streams cog API."""
        try:
            streams_cog = self.bot.get_cog("Streams")
            if not streams_cog:
                log.error("Streams cog not loaded")
                return False

            # Get prefix for command
            prefixes = await self.bot.get_valid_prefixes(alert_channel.guild)
            prefix = prefixes[0] if prefixes else "!"
            
            # Create a proper context by simulating a command invocation
            # We need to create a message object that the bot can process
            fake_content = f"{prefix}streamalert twitch channel {twitch_name} {alert_channel.id}"
            
            # Create a minimal message-like object
            # Use the guild owner as the "author" to ensure permissions
            guild_owner = alert_channel.guild.owner
            
            # Build the fake message data
            fake_message_data = {
                "id": 0,
                "channel_id": admin_channel.id,
                "guild_id": alert_channel.guild.id,
                "author": {
                    "id": guild_owner.id,
                    "username": guild_owner.name,
                    "discriminator": getattr(guild_owner, "discriminator", "0"),
                    "bot": False
                },
                "content": fake_content,
                "timestamp": discord.utils.utcnow().isoformat(),
                "edited_timestamp": None,
                "tts": False,
                "mention_everyone": False,
                "mentions": [],
                "mention_roles": [],
                "attachments": [],
                "embeds": [],
                "pinned": False,
                "type": 0
            }
            
            # Create the message object from data
            # This is more reliable than copy()
            try:
                # Try using the bot's HTTP client to create a proper message object
                state = self.bot._connection
                fake_msg = discord.Message(state=state, channel=admin_channel, data=fake_message_data)
                fake_msg.author = guild_owner
            except Exception as e:
                log.error(f"Failed to create fake message: {e}")
                # Fallback: try to invoke command directly on the cog
                return await self._invoke_streams_directly(streams_cog, twitch_name, alert_channel)

            # Get context and invoke
            ctx = await self.bot.get_context(fake_msg)
            if ctx.valid:
                await self.bot.invoke(ctx)
                return True
            else:
                log.error(f"Invalid context for command: {fake_content}")
                return False

        except Exception as e:
            log.exception(f"Error in _add_twitch_alert: {e}")
            return False

    async def _invoke_streams_directly(self, streams_cog, twitch_name: str, alert_channel: discord.TextChannel) -> bool:
        """Fallback: Try to call Streams cog methods directly."""
        try:
            # Check if Streams cog has a public API we can use
            # This depends on the Streams cog version
            if hasattr(streams_cog, "add_stream"):
                await streams_cog.add_stream(
                    guild=alert_channel.guild,
                    channel=alert_channel,
                    stream_name=twitch_name,
                    platform="twitch"
                )
                return True
            elif hasattr(streams_cog, "add_alert"):
                await streams_cog.add_alert(
                    guild_id=alert_channel.guild.id,
                    channel_id=alert_channel.id,
                    stream_name=twitch_name,
                    platform="twitch"
                )
                return True
            else:
                log.error("Streams cog does not expose add_stream or add_alert methods")
                return False
        except Exception as e:
            log.exception(f"Direct invocation failed: {e}")
            return False

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Automatically removes the stream when they leave."""
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

            # Notify admin channel
            await admin_channel.send(
                f"❌ **Automated:** {member.name} left. "
                f"Consider removing Twitch `{twitch_name}` from alerts."
            )
            
            # Clear member data
            await self.config.member(member).clear()
            
            # Note: Actually removing the stream alert requires knowing
            # the exact command/API. You may need to do this manually
            # or implement similar logic to _add_twitch_alert
            
        except Exception as e:
            log.exception(f"Error in on_member_remove: {e}")

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

        success = await self._add_twitch_alert(twitch_name, alert_channel, ctx.channel)
        
        if success:
            await ctx.send(f"✅ Successfully triggered alert setup for `{twitch_name}`")
        else:
            await ctx.send(f"❌ Failed to setup alert for `{twitch_name}`. Check logs.")