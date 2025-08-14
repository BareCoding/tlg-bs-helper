# bsPlayers/__init__.py
from .playerCommands import PlayerCommands
from .adminCommands import AdminCommands

async def setup(bot):
    await bot.add_cog(PlayerCommands(bot))
    await bot.add_cog(AdminCommands(bot))
