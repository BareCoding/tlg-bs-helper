# brawlcommon/admin.py
# ---- TLGBS bootstrap: make sibling imports work on cold start ----
import sys, pathlib
_COGS_DIR = pathlib.Path(__file__).resolve().parents[1]  # .../cogs
if str(_COGS_DIR) not in sys.path:
    sys.path.insert(0, str(_COGS_DIR))
# ------------------------------------------------------------------

from redbot.core import commands

def bs_admin_check():
    """
    Decorator to restrict commands to the allow-list managed by the BSAdmin cog,
    with owner and manage_guild/admin fallback.
    """
    async def predicate(ctx: commands.Context) -> bool:
        if await ctx.bot.is_owner(ctx.author):
            return True
        if not ctx.guild:
            return False
        cog = ctx.bot.get_cog("BSAdmin")
        if not cog:
            perms = ctx.author.guild_permissions
            return perms.manage_guild or perms.administrator
        return await cog.is_authorized(ctx.guild, ctx.author)
    return commands.check(predicate)
