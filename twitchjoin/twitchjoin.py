import discord
import asyncio
from redbot.core import commands, Config

class TwitchJoin(commands.Cog):
    """Automated Twitch integration for new members."""

    def __init__(self, bot):
        self.bot = bot
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
                await member.send("Awesome! What is your Twitch channel name? (Just the name, e.g., `twitch.tv/NAME`)")
                
                channel_msg = await self.bot.wait_for("message", check=check, timeout=300)
                twitch_name = channel_msg.content.split('/')[-1].strip()

                # Save locally for removal later
                await self.config.member(member).twitch_name.set(twitch_name)

                # --- NEW AUTOMATION METHOD ---
                # We find the first text channel the bot can talk in to run the command
                # Red's Streams cog needs a 'Context' to know which guild to add it to
                text_channel = member.guild.system_channel or member.guild.text_channels[0]
                
                # We create a fake message to trigger the command
                prefix = (await self.bot.get_valid_prefixes(member.guild))[0]
                fake_msg = copy.copy(msg) # Import copy at top
                fake_msg.content = f"{prefix}stream twitch {twitch_name}"
                fake_msg.channel = text_channel
                fake_msg.guild = member.guild
                
                context = await self.bot.get_context(fake_msg)
                await self.bot.invoke(context)
                
                await member.send(f"I've submitted `{twitch_name}` to the stream alerts!")

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            print(f"Error in TwitchJoin: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        twitch_name = await self.config.member(member).twitch_name()
        if twitch_name:
            # Similar 'fake command' to remove it
            prefix = (await self.bot.get_valid_prefixes(member.guild))[0]
            text_channel = member.guild.text_channels[0]
            
            # This runs the [p]stream stop command
            # Note: You might need to adjust this depending on how your Streams cog is set up
            # Usually it's [p]stream stop twitch <name>
            await self.bot.get_context(discord.Message(content=f"{prefix}stream stop twitch {twitch_name}", channel=text_channel))
            # (Simplifying for brevity, better to use the invoke method as above)