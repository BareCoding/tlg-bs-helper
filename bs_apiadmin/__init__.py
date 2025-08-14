from .apiadmin import BSAPIAdmin
async def setup(bot):
    await bot.add_cog(BSAPIAdmin(bot))
