import discord
import asyncio
import logging
import copy
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
        if member.bot:
            return

        guild_config = await self.config.guild(member.guild).all()
        admin_id = guild_config.get("admin_channel_id")
        alert_id = guild_config.get("alert_channel_id")
        
        if not admin_id or not alert_id:
            return

        # Start the DM process
        try:
            await self._process_new_member(member, admin_id, alert_id)
        except Exception as e:
            log.error(f"Error in on_member_join: {e}")

    async def _process_new_member(self, member, admin_id, alert_id):
        def check(m):
            return m.author.id == member.id and isinstance(m.channel, discord.DMChannel)

        try:
            await member.send(f"Welcome to {member.guild.name}! Do you have a Twitch channel? (Yes/No)")
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            
            if msg.content.lower() in ["yes", "y", "yeah", "yep"]:
                await member.send("What is your Twitch channel name?")
                c_msg = await self.bot.wait_for("message", check=check, timeout=300)
                twitch_name = c_msg.content.split('/')[-1].strip().lower()

                await self.config.member(member).twitch_name.set(twitch_name)
                
                # Fetch channels to ensure they aren't None
                admin_chan = self.bot.get_channel(admin_id) or await self.bot.fetch_channel(admin_id)
                
                # Run the command
                success = await self._invoke_streamalert_command(member.guild, admin_chan, alert_id, twitch_name)
                
                if success:
                    await admin_chan.send(f"✅ **Automated:** Added `{twitch_name}` for {member.mention}")
                    await member.send(f"Success! `{twitch_name}` added to alerts.")
        except asyncio.TimeoutError:
            pass

    async def _invoke_streamalert_command(self, guild, admin_chan, alert_id, twitch_name):
        try:
            prefix = (await self.bot.get_valid_prefixes(guild))[0]
            command_content = f"{prefix}streamalert twitch channel {twitch_name} {alert_id}"
            
            # Create a robust fake message
            fake_msg = copy.copy(await admin_chan.history(limit=1).flatten())[0] # Grab a real msg template
            fake_msg.content = command_content
            fake_msg.author = guild.owner
            fake_msg.channel = admin_chan
            
            ctx = await self.bot.get_context(fake_msg)
            if ctx.valid:
                await self.bot.invoke(ctx)
                return True
            return False
        except Exception as e:
            log.error(f"Invoke Error: {e}")
            return False

    # --- ADMIN COMMANDS ---
    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchjoin(self, ctx):
        """Settings for TwitchJoin"""
        pass

    @twitchjoin.command()
    async def setup(self, ctx, admin_channel: discord.TextChannel, alert_channel: discord.TextChannel):
        """Set your channels."""
        await self.config.guild(ctx.guild).admin_channel_id.set(admin_channel.id)
        await self.config.guild(ctx.guild).alert_channel_id.set(alert_channel.id)
        await ctx.send("✅ Configured!")