# bsPlayers/adminCommands.py
from redbot.core import commands
from .api import BrawlStarsAPI, BSAPIError, normalize_tag, get_token, set_token

class AdminCommands(commands.Cog):
    """Admin commands for Brawl Stars"""

    def __init__(self, bot):
        self.bot = bot

    async def _client(self):
        token = await get_token()
        if not token:
            raise commands.UserFeedbackCheckFailure("No API token set. Use [p]acheckapi or set token.")
        return BrawlStarsAPI(token)

    @commands.group(name="atag")
    @commands.admin_or_permissions(manage_guild=True)
    async def atag_group(self, ctx):
        """Admin tag management"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @atag_group.command(name="verify")
    async def atag_verify(self, ctx, user: commands.UserConverter, tag: str):
        tag = normalize_tag(tag)
        client = await self._client()
        try:
            await client.get_player(tag)
        except BSAPIError as e:
            return await ctx.send(f"❌ Invalid tag: {e}")
        async with self.bot.get_cog("PlayerCommands").config.user(user).tags() as tags:
            if tag not in tags:
                tags.append(tag)
        await ctx.send(f"✅ Tag #{tag} saved for {user.display_name}")

    @atag_group.command(name="remove")
    async def atag_remove(self, ctx, user: commands.UserConverter, tag: str):
        tag = normalize_tag(tag)
        async with self.bot.get_cog("PlayerCommands").config.user(user).tags() as tags:
            if tag in tags:
                tags.remove(tag)
                await ctx.send(f"🗑️ Removed #{tag} for {user.display_name}")
            else:
                await ctx.send("That tag isn't saved.")

    @commands.command(name="acheckapi")
    @commands.admin_or_permissions(manage_guild=True)
    async def acheckapi(self, ctx, token: str = None):
        """Check if API token is working (and optionally set a new one)"""
        if token:
            await set_token(token)
        tok = await get_token()
        if not tok:
            return await ctx.send("No token set.")
        client = BrawlStarsAPI(tok)
        try:
            # trivial check: get a global brawlers list
            await client.list_brawlers()
        except BSAPIError as e:
            return await ctx.send(f"❌ API not working: {e}")
        await ctx.send("✅ API is working.")
