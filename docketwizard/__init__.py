from .docketwizard import DocketWizard

async def setup(bot):
    cog = DocketWizard(bot)
    await bot.add_cog(cog)
