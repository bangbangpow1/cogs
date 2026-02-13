from .wordlepurger import WordlePurger

async def setup(bot):
    await bot.add_cog(WordlePurger(bot))