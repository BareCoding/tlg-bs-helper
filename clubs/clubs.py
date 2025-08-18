# clubs/clubs.py
# ---- TLGBS bootstrap: make sibling "brawlcommon" importable on cold start ----
import sys, pathlib
_COGS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(_COGS_DIR) not in sys.path:
    sys.path.insert(0, str(_COGS_DIR))
# ------------------------------------------------------------------------------

from typing import Dict, Any, Optional, List
import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

# from brawlcommon.admin import bs_admin_check
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import club_badge_url
from brawlcommon.checks import bs_permission_check

ACCENT  = discord.Color.from_rgb(66, 135, 245)
SUCCESS = discord.Color.green()
WARN    = discord.Color.orange()
ERROR   = discord.Color.red()

class Clubs(commands.Cog):
    """
    Track TLGBS clubs (add/remove/list and per-club settings).
    Stores:
      guild.clubs = {
        "<TAG_NOHASH>": {
           "name": str,
           "badge_id": int,
           "role_id": Optional[int],            # Discord role to assign for this club
           "log_channel_id": Optional[int],     # channel for applications/logs
           "leadership_role_id": Optional[int], # role to ping on apps
           "required_trophies": int,            # cached from API (not authoritative)
        }, ...
      }
    """

    __version__ = "0.3.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xC1A8B5, force_registration=True)
        self.config.register_guild(clubs={})
        self._apis: Dict[int, BrawlStarsAPI] = {}

    def cog_unload(self):
        for api in self._apis.values():
            self.bot.loop.create_task(api.close())

    async def _api(self, guild: discord.Guild) -> BrawlStarsAPI:
        token = await get_brawl_api_token(self.bot)
        cli = self._apis.get(guild.id)
        if not cli:
            cli = BrawlStarsAPI(token)
            self._apis[guild.id] = cli
        return cli

    # ---------------- Commands ----------------

    @commands.group()
    @commands.guild_only()
    @bs_permission_check()
    async def clubs(self, ctx):
        """Manage and view tracked clubs."""
        pass

    # ------- Admin: add/remove/config -------

    @clubs.command(name="add")
    # @bs_admin_check()
    @bs_permission_check()
    async def clubs_add(self, ctx, club_tag: str):
        """Add a club by tag (pulls data from the API)."""
        api = await self._api(ctx.guild)
        tag = api.norm_tag(club_tag)
        data = await api.get_club_by_tag(tag)
        name = data.get("name", f"#{tag}")
        badge = data.get("badgeId") or 0
        req = int(data.get("requiredTrophies", 0))

        async with self.config.guild(ctx.guild).clubs() as clubs:
            if tag in clubs:
                return await ctx.send(embed=discord.Embed(
                    title="Already tracked", description=f"Club **{name}** `#{tag}` is already tracked.", color=WARN
                ))
            clubs[tag] = {
                "name": name,
                "badge_id": badge,
                "role_id": None,
                "log_channel_id": None,
                "leadership_role_id": None,
                "required_trophies": req,
            }

        e = discord.Embed(title="Club added", description=f"**{name}** `#{tag}`", color=SUCCESS)
        if badge:
            e.set_thumbnail(url=club_badge_url(badge))
        e.add_field(name="Required Trophies", value=str(req))
        await ctx.send(embed=e)

    @clubs.command(name="remove")
    # @bs_admin_check()
    @bs_permission_check()
    async def clubs_remove(self, ctx, club_tag: str):
        """Remove a club from tracking."""
        api = await self._api(ctx.guild)
        tag = api.norm_tag(club_tag)
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if tag not in clubs:
                return await ctx.send(embed=discord.Embed(
                    title="Not tracked", description=f"`#{tag}` isn’t tracked.", color=ERROR
                ))
            cfg = clubs.pop(tag)
        await ctx.send(embed=discord.Embed(
            title="Club removed", description=f"Removed **{cfg.get('name','?')}** `#{tag}`.", color=WARN
        ))

    @clubs.command(name="setrole")
    # @bs_admin_check()
    @bs_permission_check()
    async def clubs_setrole(self, ctx, club_tag: str, role: discord.Role):
        """Set the Discord role to assign when a member joins this club."""
        api = await self._api(ctx.guild)
        tag = api.norm_tag(club_tag)
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if tag not in clubs:
                return await ctx.send(embed=discord.Embed(title="Not tracked", description=f"`#{tag}` isn’t tracked.", color=ERROR))
            clubs[tag]["role_id"] = role.id
            name = clubs[tag].get("name", f"#{tag}")
        await ctx.send(embed=discord.Embed(
            title="Club role set", description=f"{role.mention} will be assigned for **{name}** `#{tag}`.", color=SUCCESS
        ))

    @clubs.command(name="setlog")
    # @bs_admin_check()
    async def clubs_setlog(self, ctx, club_tag: str, channel: discord.TextChannel):
        """Set the log/applications channel for this club."""
        api = await self._api(ctx.guild)
        tag = api.norm_tag(club_tag)
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if tag not in clubs:
                return await ctx.send(embed=discord.Embed(title="Not tracked", description=f"`#{tag}` isn’t tracked.", color=ERROR))
            clubs[tag]["log_channel_id"] = channel.id
            name = clubs[tag].get("name", f"#{tag}")
        await ctx.send(embed=discord.Embed(
            title="Club log channel set", description=f"Logs for **{name}** `#{tag}` → {channel.mention}", color=SUCCESS
        ))

    @clubs.command(name="setlead")
    # @bs_admin_check()
    @bs_permission_check()
    async def clubs_setlead(self, ctx, club_tag: str, role: discord.Role):
        """Set the leadership role to ping for this club."""
        api = await self._api(ctx.guild)
        tag = api.norm_tag(club_tag)
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if tag not in clubs:
                return await ctx.send(embed=discord.Embed(title="Not tracked", description=f"`#{tag}` isn’t tracked.", color=ERROR))
            clubs[tag]["leadership_role_id"] = role.id
            name = clubs[tag].get("name", f"#{tag}")
        await ctx.send(embed=discord.Embed(
            title="Leadership role set", description=f"{role.mention} will be pinged for **{name}** `#{tag}`.", color=SUCCESS
        ))

    # ------- Viewers -------

    @clubs.command(name="list")
    @bs_permission_check()
    async def clubs_list(self, ctx):
        """List all tracked clubs."""
        clubs = await self.config.guild(ctx.guild).clubs()
        if not clubs:
            return await ctx.send(embed=discord.Embed(title="No clubs tracked", color=WARN))
        lines = []
        for tag, cfg in clubs.items():
            name = cfg.get("name", f"#{tag}")
            req = cfg.get("required_trophies", 0)
            role_id = cfg.get("role_id")
            role_txt = f"<@&{role_id}>" if role_id else "—"
            lines.append(f"**{name}** `#{tag}` • Req **{req:,}** • Role {role_txt}")
        await ctx.send(embed=discord.Embed(title="Tracked Clubs", description="\n".join(lines), color=ACCENT))

    @clubs.command(name="refreshcache")
    # @bs_admin_check()
    @commands.is_owner()
    async def clubs_refreshcache(self, ctx):
        """Refresh cached name/badge/req for all tracked clubs from API."""
        api = await self._api(ctx.guild)
        updated = 0
        async with self.config.guild(ctx.guild).clubs() as clubs:
            for tag, cfg in list(clubs.items()):
                try:
                    c = await api.get_club_by_tag(tag)
                except Exception:
                    continue
                cfg["name"] = c.get("name", cfg.get("name", f"#{tag}"))
                cfg["badge_id"] = c.get("badgeId") or cfg.get("badge_id", 0)
                cfg["required_trophies"] = int(c.get("requiredTrophies", cfg.get("required_trophies", 0)))
                updated += 1
        await ctx.send(embed=discord.Embed(
            title="Cache refreshed", description=f"Updated {updated} clubs from API.", color=SUCCESS
        ))

async def setup(bot: Red):
    await bot.add_cog(Clubs(bot))
