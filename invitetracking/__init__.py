from .invitetracking import InviteTracking

async def setup(bot):
    cog = InviteTracking(bot)
    await bot.add_cog(cog)
