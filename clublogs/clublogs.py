# clublogs/clublogs.py
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
from discord.ext import tasks

from brawlcommon.admin import bs_admin_check
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token

ACCENT  = discord.Color.from_rgb(66, 135, 245)
SUCCESS = discord.Color.green()
WARN    = discord.Color.orange()
ERROR   = discord.Color.red()

MAX_MEMBERS = 30

class ClubLogs(commands.Cog):
    """
    Constantly streams join/leave deltas per tracked club into that club's log channel (if set).
    Diffing logic is shared with ClubSync, but this cog is logging-only.
    """

    __version__ = "0.2.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xC10B10, force_registration=True)
        default_guild = {
            "enabled": True,
            "interval": 90,   # seconds
            "last_seen": {},  # tag -> list of member tags
        }
        self.config.register_guild(**default_guild)
        self._apis: Dict[int, BrawlStarsAPI] = {}
        self.loop.start()

    def cog_unload(self):
        self.loop.cancel()
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
    async def clublogs(self, ctx):
        """Configure the continuous club logs stream."""
        pass

    @clublogs.command(name="enable")
    @bs_admin_check()
    async def cl_enable(self, ctx):
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send(embed=discord.Embed(title="ClubLogs enabled", color=SUCCESS))

    @clublogs.command(name="disable")
    @bs_admin_check()
    async def cl_disable(self, ctx):
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send(embed=discord.Embed(title="ClubLogs disabled", color=WARN))

    @clublogs.command(name="interval")
    @bs_admin_check()
    async def cl_interval(self, ctx, seconds: int):
        seconds = max(60, min(600, seconds))
        await self.config.guild(ctx.guild).interval.set(seconds)
        await ctx.send(embed=discord.Embed(title="Log interval updated", description=f"{seconds}s", color=SUCCESS))
        if self.loop.is_running():
            self.loop.change_interval(seconds=seconds)

    # ---------------- Loop ----------------

    @tasks.loop(seconds=90)
    async def loop(self):
        for guild in list(self.bot.guilds):
            try:
                await self._tick(guild)
            except Exception:
                continue

    @loop.before_loop
    async def before(self):
        await self.bot.wait_until_ready()
        for g in self.bot.guilds:
            seconds = (await self.config.guild(g).interval())
            if seconds and seconds != 90:
                self.loop.change_interval(seconds=seconds)
                break

    async def _tick(self, guild: discord.Guild):
        if not guild:
            return
        if not (await self.config.guild(guild).enabled()):
            return

        clubs_cog = self.bot.get_cog("Clubs")
        if not clubs_cog:
            return
        tracked = await clubs_cog.config.guild(guild).clubs()
        if not tracked:
            return

        api = await self._api(guild)
        last_seen = await self.config.guild(guild).last_seen()  # {clubtag: [membertags]}
        updated_seen: Dict[str, List[str]] = {}

        for ctag, cfg in tracked.items():
            try:
                cmembers = await api.get_club_members(ctag)
            except Exception:
                continue
            items = cmembers.get("items") or []
            tags_now = [m.get("tag", "").replace("#", "") for m in items if m.get("tag")]
            updated_seen[ctag] = tags_now

            before = set(last_seen.get(ctag, []))
            after = set(tags_now)
            joined = list(after - before)
            left   = list(before - after)

            chan = guild.get_channel(cfg.get("log_channel_id") or 0)
            if not chan:
                continue

            for jtag in joined:
                try:
                    await chan.send(embed=discord.Embed(
                        title="Member Joined",
                        description=f"`#{jtag}` joined **{cfg.get('name','?')}**",
                        color=SUCCESS
                    ))
                except Exception:
                    pass

            for ltag in left:
                try:
                    await chan.send(embed=discord.Embed(
                        title="Member Left",
                        description=f"`#{ltag}` left **{cfg.get('name','?')}**",
                        color=ERROR
                    ))
                except Exception:
                    pass

        await self.config.guild(guild).last_seen.set(updated_seen)

async def setup(bot: Red):
    await bot.add_cog(ClubLogs(bot))
