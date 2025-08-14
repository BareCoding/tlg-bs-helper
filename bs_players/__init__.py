from .players import BSPlayers

async def setup(bot):
    await bot.add_cog(BSPlayers(bot))
