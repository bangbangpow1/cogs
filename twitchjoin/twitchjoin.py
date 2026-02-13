import discord
import asyncio
import logging
from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Optional

log = logging.getLogger("red.twitchjoin")


class TwitchJoin(commands.Cog):
    """Automated Twitch integration for new members."""
    
    __version__ = "1.1.0"
    __author__ = "Your Name"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, 
            identifier=9876543210, 
            force_registration=True
        )
        
        # Default config schema
        default_guild = {
            "admin_channel": None,
            "alert_channel": None,
            "enabled": True,
            "timeout_minutes": 5
        }
        default_member = {
            "twitch_name": None,
            "alerts_enabled": False
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

    async def red_delete_data_for_user(self, *, requester: str, user_id: int):
        """Handle data deletion requests."""
        # Delete member data across all guilds
        all_members = await self.config.all_members()
        for guild_id, members in all_members.items():
            if user_id in members:
                await self.config.member_from_ids(guild_id, user_id).clear()

    async def get_streams_cog(self) -> Optional[commands.Cog]:
        """Safely get the Streams cog if loaded."""
        return self.bot.get_cog("Streams")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle new member joins."""
        if member.bot:
            return

        guild_config = await self.config.guild(member.guild).all()
        
        # Check if feature is enabled
        if not guild_config["enabled"]:
            return
            
        # Check required channels are configured
        if not guild_config["admin_channel"] or not guild_config["alert_channel"]:
            log.warning(f"Channels not configured for guild {member.guild.id}")
            return

        try:
            await self._process_new_member(member, guild_config)
        except asyncio.TimeoutError:
            log.info(f"Timeout waiting for response from {member.id}")
        except discord.Forbidden:
            log.warning(f"Cannot DM member {member.id}")
        except Exception as e:
            log.exception(f"Error processing member {member.id}: {e}")

    async def _process_new_member(self, member: discord.Member, guild_config: dict):
        """Process DM flow with new member."""
        timeout = guild_config["timeout_minutes"] * 60
        
        # Initial welcome message
        try:
            await member.send(
                f"Welcome to **{member.guild.name}**! 🎉\n\n"
                f"Do you have a Twitch channel you'd like to set up alerts for? "
                f"(Yes/No)\n*This request will timeout in {guild_config['timeout_minutes']} minutes.*"
            )
        except discord.Forbidden:
            log.info(f"Cannot send DM to {member.id}, skipping Twitch setup")
            return

        def check(m):
            return (
                m.author == member 
                and isinstance(m.channel, discord.DMChannel)
                and m.content
            )

        # Wait for Yes/No response
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            await member.send("Request timed out. If you change your mind, contact a moderator!")
            raise

        if msg.content.lower() not in ["yes", "y", "yeah", "yep"]:
            await member.send("No problem! If you change your mind later, just let us know.")
            return

        # Ask for Twitch name
        await member.send(
            "Great! What's your Twitch channel name?\n"
            "You can provide just the username or the full URL (twitch.tv/username)."
        )

        try:
            channel_msg = await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            await member.send("Request timed out. Please contact a moderator to set this up manually.")
            raise

        # Parse Twitch name
        twitch_input = channel_msg.content.strip()
        twitch_name = self._parse_twitch_name(twitch_input)
        
        if not twitch_name:
            await member.send("That doesn't look like a valid Twitch username. Please contact a moderator for help.")
            return

        # Save to config
        await self.config.member(member).twitch_name.set(twitch_name)
        await self.config.member(member).alerts_enabled.set(True)

        # Setup stream alert via Streams cog
        success = await self._setup_stream_alert(member, twitch_name, guild_config)
        
        if success:
            await member.send(
                f"✅ Success! I've set up stream alerts for `{twitch_name}`. "
                f"You'll receive notifications in the designated channel when you go live!"
            )
        else:
            await member.send(
                f"⚠️ I saved your Twitch name (`{twitch_name}`), but couldn't automatically "
                f"set up alerts. A moderator will review this manually."
            )

    def _parse_twitch_name(self, input_str: str) -> Optional[str]:
        """Extract Twitch username from various input formats."""
        input_str = input_str.strip().lower()
        
        # Handle full URLs
        if "twitch.tv/" in input_str:
            parts = input_str.split("twitch.tv/")
            if len(parts) > 1:
                username = parts[1].split("/")[0].split("?")[0]
                return username if username else None
        
        # Handle @username
        if input_str.startswith("@"):
            input_str = input_str[1:]
            
        # Basic validation - Twitch usernames are 4-25 chars, alphanumeric + underscore
        cleaned = ''.join(c for c in input_str if c.isalnum() or c == '_')
        
        if 4 <= len(cleaned) <= 25 and cleaned[0].isalnum():
            return cleaned
        return None

    async def _setup_stream_alert(
        self, 
        member: discord.Member, 
        twitch_name: str, 
        guild_config: dict
    ) -> bool:
        """Setup stream alert using Streams cog API."""
        streams_cog = await self.get_streams_cog()
        if not streams_cog:
            log.error("Streams cog not loaded")
            return False

        try:
            # Get channel objects
            admin_channel = self.bot.get_channel(guild_config["admin_channel"])
            alert_channel = self.bot.get_channel(guild_config["alert_channel"])
            
            if not admin_channel or not alert_channel:
                log.error("Configured channels not found")
                return False

            # Method 1: Try to use Streams cog's internal API if available
            # This is cleaner than faking messages
            if hasattr(streams_cog, "add_stream_alert"):
                await streams_cog.add_stream_alert(
                    channel=alert_channel,
                    stream_name=twitch_name,
                    platform="twitch"
                )
            else:
                # Method 2: Use command invocation with proper context
                # This is more reliable than fake messages in modern Redbot
                ctx = await self._create_context(
                    channel=admin_channel,
                    content=f"streamalert twitch channel {twitch_name} {alert_channel.id}"
                )
                if ctx:
                    await self.bot.invoke(ctx)

            # Send confirmation to admin channel
            await admin_channel.send(
                f"✅ **Auto-Setup:** {member.mention} joined and set up alerts for "
                f"`{twitch_name}` → {alert_channel.mention}"
            )
            
            return True
            
        except Exception as e:
            log.exception(f"Failed to setup stream alert: {e}")
            return False

    async def _create_context(self, channel: discord.TextChannel, content: str):
        """Create a proper command context for invocation."""
        try:
            # Get prefix
            prefixes = await self.bot.get_valid_prefixes(channel.guild)
            prefix = prefixes[0] if prefixes else "!"
            
            # Create a minimal message-like object for context creation
            # Note: This is still somewhat hacky but more robust than full message fakery
            class FakeMessage:
                def __init__(self, bot, channel, content):
                    self._bot = bot
                    self.channel = channel
                    self.content = content
                    self.guild = channel.guild
                    self.author = channel.guild.owner  # Run as owner
                    self.id = 0
                    self.created_at = discord.utils.utcnow()
                    
                async def delete(self):
                    pass
            
            fake_msg = FakeMessage(self.bot, channel, prefix + content)
            return await self.bot.get_context(fake_msg)
            
        except Exception as e:
            log.error(f"Failed to create context: {e}")
            return None

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Cleanup when member leaves."""
        member_data = await self.config.member(member).all()
        
        if not member_data.get("twitch_name") or not member_data.get("alerts_enabled"):
            return

        twitch_name = member_data["twitch_name"]
        guild_config = await self.config.guild(member.guild).all()
        
        if not guild_config["admin_channel"]:
            return

        try:
            admin_channel = self.bot.get_channel(guild_config["admin_channel"])
            if not admin_channel:
                return

            # Try to remove the stream alert
            streams_cog = await self.get_streams_cog()
            if streams_cog and hasattr(streams_cog, "remove_stream_alert"):
                await streams_cog.remove_stream_alert(
                    channel=self.bot.get_channel(guild_config["alert_channel"]),
                    stream_name=twitch_name,
                    platform="twitch"
                )

            await admin_channel.send(
                f"❌ **Auto-Cleanup:** {member.name} left. "
                f"Removed Twitch alerts for `{twitch_name}`."
            )
            
            # Clear member data
            await self.config.member(member).clear()
            
        except Exception as e:
            log.exception(f"Error during member removal cleanup: {e}")

    # Admin Commands
    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchjoin(self, ctx: commands.Context):
        """Configure TwitchJoin settings."""
        pass

    @twitchjoin.command(name="channel")
    async def set_channels(
        self, 
        ctx: commands.Context, 
        admin_channel: discord.TextChannel, 
        alert_channel: discord.TextChannel
    ):
        """Set the admin and alert channels."""
        await self.config.guild(ctx.guild).admin_channel.set(admin_channel.id)
        await self.config.guild(ctx.guild).alert_channel.set(alert_channel.id)
        await ctx.send(
            f"✅ Channels configured:\n"
            f"Admin: {admin_channel.mention}\n"
            f"Alerts: {alert_channel.mention}"
        )

    @twitchjoin.command(name="toggle")
    async def toggle_enabled(self, ctx: commands.Context, enabled: bool = None):
        """Toggle automatic Twitch setup for new members."""
        if enabled is None:
            current = await self.config.guild(ctx.guild).enabled()
            enabled = not current
            
        await self.config.guild(ctx.guild).enabled.set(enabled)
        status = "enabled" if enabled else "disabled"
        await ctx.send(f"Auto-Twitch setup is now **{status}**.")

    @twitchjoin.command(name="timeout")
    async def set_timeout(self, ctx: commands.Context, minutes: int):
        """Set the DM timeout duration (1-30 minutes)."""
        if not 1 <= minutes <= 30:
            await ctx.send("Timeout must be between 1 and 30 minutes.")
            return
            
        await self.config.guild(ctx.guild).timeout_minutes.set(minutes)
        await ctx.send(f"DM timeout set to **{minutes}** minutes.")

    @twitchjoin.command(name="status")
    async def show_status(self, ctx: commands.Context):
        """Show current configuration status."""
        config = await self.config.guild(ctx.guild).all()
        
        admin_ch = self.bot.get_channel(config["admin_channel"])
        alert_ch = self.bot.get_channel(config["alert_channel"])
        
        embed = discord.Embed(
            title="TwitchJoin Configuration",
            color=await ctx.embed_color()
        )
        embed.add_field(name="Enabled", value="Yes" if config["enabled"] else "No", inline=True)
        embed.add_field(name="Timeout", value=f"{config['timeout_minutes']} min", inline=True)
        embed.add_field(
            name="Admin Channel", 
            value=admin_ch.mention if admin_ch else "Not set", 
            inline=False
        )
        embed.add_field(
            name="Alert Channel", 
            value=alert_ch.mention if alert_ch else "Not set", 
            inline=False
        )
        
        # Count members with Twitch setup
        all_members = await self.config.all_members(ctx.guild)
        count = sum(1 for m in all_members.values() if m.get("twitch_name"))
        embed.add_field(name="Members Setup", value=str(count), inline=True)
        
        await ctx.send(embed=embed)

    @twitchjoin.command(name="manual")
    async def manual_setup(
        self, 
        ctx: commands.Context, 
        member: discord.Member, 
        twitch_name: str
    ):
        """Manually set up Twitch alerts for a member."""
        # Validate twitch name
        parsed = self._parse_twitch_name(twitch_name)
        if not parsed:
            await ctx.send("Invalid Twitch username format.")
            return
            
        await self.config.member(member).twitch_name.set(parsed)
        await self.config.member(member).alerts_enabled.set(True)
        
        guild_config = await self.config.guild(ctx.guild).all()
        success = await self._setup_stream_alert(member, parsed, guild_config)
        
        if success:
            await ctx.send(f"✅ Set up alerts for {member.mention} (`{parsed}`)")
        else:
            await ctx.send(f"⚠️ Saved data but alert setup failed for `{parsed}`")

    @twitchjoin.command(name="cleanup")
    async def cleanup_data(self, ctx: commands.Context, member: discord.Member = None):
        """Clear Twitch data for a member (or all members if none specified)."""
        if member:
            await self.config.member(member).clear()
            await ctx.send(f"Cleared data for {member.mention}")
        else:
            await self.config.clear_all_members(ctx.guild)
            await ctx.send("Cleared all member Twitch data for this server.")