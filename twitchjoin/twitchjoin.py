import discord
import asyncio
import copy
import logging
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger("red.twitchjoin")

class TwitchJoin(commands.Cog):
    """Automated Twitch integration via Join DMs and Reaction Signups."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        default_guild = {
            "admin_channel_id": None,
            "alert_channel_id": None,
            "signup_message_id": None
        }
        default_member = {
            "twitch_name": None
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

    async def _invoke_stream_toggle(self, member, twitch_name: str, guild_data: dict, is_join: bool):
        """Creates a fake context to run the streamalert toggle command."""
        admin_chan = self.bot.get_channel(guild_data["admin_channel_id"])
        if not admin_chan:
            return

        prefix = (await self.bot.get_valid_prefixes(member.guild))[0]
        cmd_text = f"{prefix}streamalert twitch channel {twitch_name} {guild_data['alert_channel_id']}"

        data = {
            "id": discord.utils.time_snowflake(discord.utils.utcnow()),
            "content": cmd_text,
            "author": {
                "id": member.guild.owner_id,
                "username": member.guild.owner.name,
                "discriminator": str(member.guild.owner.discriminator),
                "avatar": str(member.guild.owner.avatar),
                "bot": False,
            },
            "channel_id": admin_chan.id,
            "guild_id": member.guild.id,
            "type": 0,
            "timestamp": discord.utils.utcnow().isoformat(),
        }

        fake_msg = discord.Message(state=self.bot._connection, channel=admin_chan, data=data)
        fake_msg.author = member.guild.owner 
        ctx = await self.bot.get_context(fake_msg)
        
        if ctx.valid:
            await self.bot.invoke(ctx)
            log_msg = "Adding" if is_join else "Removing"
            emoji = "✅" if is_join else "❌"
            await admin_chan.send(f"{emoji} **Automated:** {log_msg} Twitch `{twitch_name}` for {member.mention}")

    async def _ask_twitch_name(self, member, guild):
        """Internal helper to handle the DM conversation."""
        try:
            await member.send("What is your twitch channel? (name only)")

            def check(m):
                return m.author.id == member.id and isinstance(m.channel, discord.DMChannel)

            name_msg = await self.bot.wait_for("message", check=check, timeout=300)
            twitch_name = name_msg.content.split('/')[-1].strip().lower()

            await self.config.member(member).twitch_name.set(twitch_name)
            guild_data = await self.config.guild(guild).all()
            
            await self._invoke_stream_toggle(member, twitch_name, guild_data, is_join=True)
            await member.send(f"Success! I've added `{twitch_name}` to our stream alerts.")
        except asyncio.TimeoutError:
            pass
        except discord.Forbidden:
            log.info(f"Could not DM {member.name}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
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
                await self._ask_twitch_name(member, member.guild)
        except (asyncio.TimeoutError, discord.Forbidden):
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.bot.user.id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        signup_id = await self.config.guild(guild).signup_message_id()
        if payload.message_id != signup_id:
            return
        if str(payload.emoji) == "✅":
            member = guild.get_member(payload.user_id)
            if member:
                try:
                    channel = self.bot.get_channel(payload.channel_id)
                    message = await channel.fetch_message(payload.message_id)
                    await message.remove_reaction(payload.emoji, member)
                except: pass
                await self._ask_twitch_name(member, guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        twitch_name = await self.config.member(member).twitch_name()
        if twitch_name:
            guild_data = await self.config.guild(member.guild).all()
            if guild_data["admin_channel_id"]:
                await self._invoke_stream_toggle(member, twitch_name, guild_data, is_join=False)
            await self.config.member(member).clear()

    @commands.group(name="twitchjoin")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchjoin(self, ctx):
        """Settings for TwitchJoin automation."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @twitchjoin.command(name="help")
    async def twitchjoin_help(self, ctx):
        """Visual guide on how to setup this cog."""
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="TwitchJoin Setup Guide",
            description="Follow these steps to enable automated Twitch alerts for your members.",
            color=0x6441a5 # Twitch Purple
        )
        embed.add_field(
            name="Step 1: Channel Configuration",
            value=f"Run `{prefix}twitchjoin setup <#admin_log_channel> <#shoutout_channel>`\n"
                  "This tells the bot where to log actions and where the alerts should eventually post.",
            inline=False
        )
        embed.add_field(
            name="Step 2: Create Signup Message",
            value=f"Run `{prefix}twitchjoin postmsg` in your server.\n"
                  "The bot will post the recruitment message with a ✅ reaction. Existing members can click this to sign up.",
            inline=False
        )
        embed.add_field(
            name="How it Works",
            value="• **New Members:** Get a DM automatically when they join.\n"
                  "• **Existing Members:** Click the ✅ reaction in your shoutout channel.\n"
                  "• **Automation:** The bot fakes a `streamalert` command to the Streams cog for you.\n"
                  "• **Cleanup:** When a member leaves the server, their alert is automatically removed.",
            inline=False
        )
        await ctx.send(embed=embed)

    @twitchjoin.command(name="setup")
    async def setup_channels(self, ctx, admin_channel: discord.TextChannel, alert_channel: discord.TextChannel):
        """Set the admin log and shoutout channels."""
        await self.config.guild(ctx.guild).admin_channel_id.set(admin_channel.id)
        await self.config.guild(ctx.guild).alert_channel_id.set(alert_channel.id)
        await ctx.send(f"✅ **Channels configured!**\nAdmin logs: {admin_channel.mention}\nShoutouts: {alert_channel.mention}")

    @twitchjoin.command(name="postmsg")
    async def postmsg(self, ctx):
        """Post the recruitment message in the shoutout channel."""
        alert_id = await self.config.guild(ctx.guild).alert_channel_id()
        alert_chan = self.bot.get_channel(alert_id)
        if not alert_chan:
            return await ctx.send(f"Please run `{ctx.clean_prefix}twitchjoin setup` first!")

        text = ("If you would like your channel to be featured here every time you go live on twitch automatically, "
                "press the button below. Your twitch will get added to a list, and a card will be placed in here "
                "whenever you go live!")
        msg = await alert_chan.send(text)
        await msg.add_reaction("✅")
        await self.config.guild(ctx.guild).signup_message_id.set(msg.id)
        await ctx.send(f"Signup message posted in {alert_chan.mention}!")