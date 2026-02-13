from redbot.core import commands
import discord

class WordlePurger(commands.Cog):
    """Delete messages from a specific user."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_messages=True)
    async def deletewordle(self, ctx, amount: int = 100):
        """
        Deletes messages from the Wordle bot (ID: 1211781489931452447).
        Usage: [p]deletewordle 50 (scans last 50 messages)
        """
        target_id = 1211781489931452447
        
        # This check tells the purge function which messages to delete
        def is_target(m):
            return m.author.id == target_id

        # Delete the command message itself first
        await ctx.message.delete()

        # Perform the purge
        deleted = await ctx.channel.purge(limit=amount, check=is_target)

        # Send a temporary confirmation message
        await ctx.send(f"Done! Deleted {len(deleted)} messages from the target user.", delete_after=5)