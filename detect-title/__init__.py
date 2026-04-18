from .detect_title import DetectTitle

async def setup(bot):
    cog = DetectTitle(bot)
    await bot.add_cog(cog)
