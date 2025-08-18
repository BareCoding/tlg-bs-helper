from redbot.core.bot import Red
from .mod import ModP


async def setup(bot: Red):
    cog = ModP(bot)
    bot.add_cog(cog)
    await cog.initialize()
