import discord
import asyncio
from redbot.core import commands, Config

class TwitchJoin(commands.Cog):
    """Automated Twitch integration for new members."""

    def __init__(self, bot):
        self.bot = bot
        # We create a small config to remember which Twitch name belongs to which User ID
        # This helps us delete the right stream when they leave.
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        self.config.register_member(twitch_name=None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        try:
            await member.send(f"Welcome to {member.guild.name}! Do you have a Twitch channel? (Yes/No)")

            def check(m):
                return m.author == member and isinstance(m.channel, discord.DMChannel)

            msg = await self.bot.wait_for("message", check=check, timeout=300)
            
            if msg.content.lower() in ["yes", "y", "yeah", "yep"]:
                await member.send("Awesome! What is your Twitch channel name?")
                
                channel_msg = await self.bot.wait_for("message", check=check, timeout=300)
                twitch_name = channel_msg.content.split('/')[-1].strip()

                # 1. Save the name locally so we can remove it later
                await self.config.member(member).twitch_name.set(twitch_name)

                # 2. Add to the Streams Cog automatically
                streams_cog = self.bot.get_cog("Streams")
                if streams_cog:
                    # We 'force' the stream into the Streams cog's list
                    # This mimics the [p]stream twitch <name> command
                    await streams_cog._add_stream(member.guild, "twitch", twitch_name)
                    await member.send(f"Success! I've added `{twitch_name}` to our automated alerts.")
                else:
                    await member.send("The Streams module is offline, but I've saved your info for later.")

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            print(f"Error in TwitchJoin: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Automatically removes their stream when they leave the server."""
        twitch_name = await self.config.member(member).twitch_name()
        
        if twitch_name:
            streams_cog = self.bot.get_cog("Streams")
            if streams_cog:
                # This mimics the [p]stream stop <name> command
                await streams_cog._delete_stream(member.guild, "twitch", twitch_name)
                # Clear our local record
                await self.config.member(member).clear()