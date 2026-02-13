import discord
import asyncio
import copy
from redbot.core import commands, Config

class TwitchJoin(commands.Cog):
    """Automated Twitch integration using command invocation."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        self.config.register_member(twitch_name=None)
        self.admin_channel_id = 1175349790989635678
        self.alert_channel_id = 1174951271938142258

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
                await member.send("What is your Twitch channel name?")
                
                channel_msg = await self.bot.wait_for("message", check=check, timeout=300)
                # This handles links vs plain names
                twitch_name = channel_msg.content.split('/')[-1].strip()

                await self.config.member(member).twitch_name.set(twitch_name)
                
                prefix = (await self.bot.get_valid_prefixes(member.guild))[0]
                admin_chan = self.bot.get_channel(self.admin_channel_id)
                
                if not admin_chan:
                    return

                # Create fake context to run the ADD command
                fake_content = f"{prefix}streamalert twitch channel {twitch_name} {self.alert_channel_id}"
                fake_msg = copy.copy(channel_msg)
                fake_msg.content = fake_content
                fake_msg.channel = admin_chan
                fake_msg.guild = member.guild
                fake_msg.author = member.guild.owner 

                context = await self.bot.get_context(fake_msg)
                await self.bot.invoke(context)

                await admin_chan.send(f"✅ **Automated:** Added `{twitch_name}` for {member.mention}")
                await member.send(f"Done! I've added `{twitch_name}` to our stream alerts.")

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            print(f"TwitchJoin Join Error: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Automatically removes the stream when they leave."""
        twitch_name = await self.config.member(member).twitch_name()
        
        if twitch_name:
            prefix = (await self.bot.get_valid_prefixes(member.guild))[0]
            admin_chan = self.bot.get_channel(self.admin_channel_id)
            
            if admin_chan:
                # The command to remove a stream is usually 'stop'
                stop_content = f"{prefix}streamalert twitch stop {twitch_name}"
                
                # Create a fake message to trigger the removal
                fake_msg = discord.Message(
                    state=self.bot._connection,
                    channel=admin_chan,
                    data={'content': stop_content, 'author': {'id': member.guild.owner_id}, 'id': 1}
                )
                fake_msg.guild = member.guild

                context = await self.bot.get_context(fake_msg)
                await self.bot.invoke(context)
                
                await admin_chan.send(f"❌ **Automated:** Removed `{twitch_name}` because {member.name} left.")
                await self.config.member(member).clear()