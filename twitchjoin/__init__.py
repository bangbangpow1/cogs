from .twitchjoin import TwitchJoin

async def setup(bot):
    # This function is what Red calls when you run [p]load twitchjoin
    await bot.add_cog(TwitchJoin(bot))