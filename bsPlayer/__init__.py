from .playerCommands import PlayerCommands

async def setup(bot):
    await bot.add_cog(PlayerCommands(bot))
