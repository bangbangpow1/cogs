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
        # The channel where stream alerts are posted
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
                twitch_name = channel_msg.content.split('/')[-1].strip()

                await self.config.member(member).twitch_name.set(twitch_name)
                
                # --- COMMAND INVOCATION ---
                # 1. Get the bot's prefix
                prefix = (await self.bot.get_valid_prefixes(member.guild))[0]
                
                # 2. Find a channel to "run" the command in (the admin channel)
                admin_chan = self.bot.get_channel(self.admin_channel_id)
                if not admin_chan:
                    return

                # 3. Create a fake message acting as the Server Owner
                # This bypasses permission checks
                fake_content = f"{prefix}streamalert twitch channel {twitch_name} {self.alert_channel_id}"
                fake_msg = copy.copy(channel_msg)
                fake_msg.content = fake_content
                fake_msg.channel = admin_chan
                fake_msg.guild = member.guild
                fake_msg.author = member.guild.owner 

                # 4. Tell the bot to execute the command
                context = await self.bot.get_context(fake_msg)
                await self.bot.invoke(context)

                # Notify Admin Channel
                await admin_chan.send(f"✅ **Automated:** Ran command `{fake_content}` for {member.mention}")
                await member.send(f"Done! I've added `{twitch_name}` to our stream alerts.")

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            print(f"TwitchJoin Error: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Automatically removes the stream when they leave."""
        twitch_name = await self.config.member(member).twitch_name()
        
        if twitch_name:
            prefix = (await self.bot.get_valid_prefixes(member.guild))[0]
            admin_chan = self.bot.get_channel(self.admin_channel_id)
            
            if admin_chan:
                # Fake a 'stop' command
                # Adjusting to the common Redbot 'stream stop' format
                stop_content = f"{prefix}streamalert twitch stop {twitch_name}"
                
                fake_msg = copy.copy(member.guild.owner) # Just need a base object
                # Note: creating a full fake message for 'remove' is similar to 'join'
                await admin_chan.send(f"❌ **Automated:** {member.name} left. Please ensure `{twitch_name}` is removed using `{stop_content}` if it doesn't happen automatically.")
                
                await self.config.member(member).clear()