# cogs/brawlcommon/checks.py
from __future__ import annotations

from redbot.core import commands
import discord


def bs_permission_check():
    """
    Decorator: gate a command using BSAdmin's per-guild ACLs.
    Resolution order (first match wins):
      1) command-level  (e.g., "bs player")
      2) group-level    (e.g., "bs")
      3) cog-level      (e.g., "BSInfo")
      4) fallback: server Admins/Owner
    """
    async def predicate(ctx: commands.Context) -> bool:
        # DM? deny (we only manage per-guild ACLs)
        if ctx.guild is None:
            return False

        # Owner bypass
        if await ctx.bot.is_owner(ctx.author):
            return True

        # Server Admins always allowed
        if ctx.author.guild_permissions.administrator:
            return True

        # If BSAdmin isn't loaded, default to Admin-only (denied here)
        admin_cog = ctx.bot.get_cog("BSAdmin")
        if admin_cog is None:
            return False

        # Gather keys
        qualified = (ctx.command.qualified_name or "").lower() if ctx.command else ""
        group = (ctx.command.full_parent_name or "").lower() if ctx.command else ""
        cog_name = getattr(ctx.cog, "__cog_name__", None)

        return await admin_cog.is_allowed(
            guild=ctx.guild,
            member=ctx.author,
            cog_name=cog_name,
            qualified_name=qualified or None,
            group_name=group or None,
        )

    return commands.check(predicate)
