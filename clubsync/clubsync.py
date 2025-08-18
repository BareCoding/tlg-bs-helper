# clubsync/clubsync.py
# ---- TLGBS bootstrap: make sibling "brawlcommon" importable on cold start ----
import sys, pathlib
_COGS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(_COGS_DIR) not in sys.path:
    sys.path.insert(0, str(_COGS_DIR))
# ------------------------------------------------------------------------------

from typing import Dict, Any, Optional, List
import asyncio
import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_list
from discord.ext import tasks

# from brawlcommon.admin import bs_admin_check
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.checks import bs_permission_check

ACCENT  = discord.Color.from_rgb(66, 135, 245)
SUCCESS = discord.Color.green()
WARN    = discord.Color.orange()
ERROR   = discord.Color.red()

MAX_MEMBERS = 30

class ClubSync(commands.Cog):
    """
    Background sync:
      - Watches tracked clubs for member joins/leaves (poll)
      - When a saved user joins their chosen club:
          * assigns club role
          * updates nickname to "IGN | CLUB" (CLUB without 'TLG')
      - Posts simple join/leave notices to the club's log channel if configured
    """

    __version__ = "0.4.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xC10B5F, force_registration=True)
        default_guild = {
            "enabled": True,
            "interval": 120,            # seconds
            "nick_format": "{IGN} | {CLUB}",  # CLUB = club name without 'TLG'
            "last_seen": {},            # tag -> list of member tags (for diffing)
        }
        self.config.register_guild(**default_guild)
        self._apis: Dict[int, BrawlStarsAPI] = {}
        self._locks: Dict[int, asyncio.Lock] = {}
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

    def _guild_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._locks:
            self._locks[guild_id] = asyncio.Lock()
        return self._locks[guild_id]

    # ---------------- Commands ----------------

    @commands.group()
    @commands.guild_only()
    @bs_permission_check()
    async def clubsync(self, ctx):
        """Configure and manage the club sync worker."""
        pass

    @clubsync.command(name="enable")
    # @bs_admin_check()
    @bs_permission_check()
    async def cs_enable(self, ctx):
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send(embed=discord.Embed(title="ClubSync enabled", color=SUCCESS))

    @clubsync.command(name="disable")
    # @bs_admin_check()
    @bs_permission_check()
    async def cs_disable(self, ctx):
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send(embed=discord.Embed(title="ClubSync disabled", color=WARN))

    @clubsync.command(name="interval")
    # @bs_admin_check()
    @bs_permission_check()
    async def cs_interval(self, ctx, seconds: int):
        seconds = max(60, min(900, seconds))
        await self.config.guild(ctx.guild).interval.set(seconds)
        await ctx.send(embed=discord.Embed(title="Poll interval updated", description=f"{seconds}s", color=SUCCESS))
        if self.loop.is_running():
            self.loop.change_interval(seconds=seconds)

    @clubsync.command(name="nickformat")
    # @bs_admin_check()
    @bs_permission_check()
    async def cs_nickformat(self, ctx, *, fmt: str):
        """
        Set nickname format. Replacements:
         {IGN}  - player name
         {CLUB} - club name with 'TLG' removed
        """
        await self.config.guild(ctx.guild).nick_format.set(fmt)
        await ctx.send(embed=discord.Embed(title="Nickname format updated", description=f"`{fmt}`", color=SUCCESS))

    # ---------------- Worker ----------------

    @tasks.loop(seconds=120)
    async def loop(self):
        for guild in list(self.bot.guilds):
            try:
                await self._tick(guild)
            except Exception:
                continue

    @loop.before_loop
    async def before(self):
        await self.bot.wait_until_ready()
        # pick up configured interval
        for g in self.bot.guilds:
            seconds = (await self.config.guild(g).interval())
            if seconds and seconds != 120:
                self.loop.change_interval(seconds=seconds)
                break

    async def _tick(self, guild: discord.Guild):
        if not guild:
            return
        if not (await self.config.guild(guild).enabled()):
            return

        lock = self._guild_lock(guild.id)
        if lock.locked():
            return
        async with lock:
            api = await self._api(guild)
            clubs_cog = self.bot.get_cog("Clubs")
            if not clubs_cog:
                return
            tracked = await clubs_cog.config.guild(guild).clubs()
            if not tracked:
                return

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

                # Compare
                before = set(last_seen.get(ctag, []))
                after = set(tags_now)
                joined = list(after - before)
                left   = list(before - after)

                # Notify channel
                chan = guild.get_channel(cfg.get("log_channel_id") or 0)

                # Role assignment and nickname updates for joiners
                if joined:
                    # Try to find users in the guild with this tag saved as default or any saved tag
                    bsinfo = self.bot.get_cog("BSInfo")
                    role = guild.get_role(cfg.get("role_id") or 0)
                    for jtag in joined:
                        member: Optional[discord.Member] = None
                        ign = None
                        # naive scan of members who have saved tags (bounded by guild size)
                        for m in guild.members:
                            if not bsinfo:
                                break
                            u = await bsinfo.config.user(m).all()
                            tags = u.get("tags", [])
                            if jtag in [t.replace("#", "").upper() for t in tags]:
                                member = m
                                ign = u.get("ign_cache") or m.display_name
                                break
                        # set roles and nickname
                        if member and role:
                            try:
                                await member.add_roles(role, reason="Joined club in-game")
                            except Exception:
                                pass
                        if member:
                            # Nickname: IGN | CLUB (without 'TLG')
                            club_name = cfg.get("name", "Club").replace("TLG", "").strip()
                            fmt = (await self.config.guild(guild).nick_format())
                            newnick = (fmt or "{IGN} | {CLUB}").format(IGN=ign or member.display_name, CLUB=club_name)
                            try:
                                await member.edit(nick=newnick, reason="Joined club in-game")
                            except Exception:
                                pass
                        if chan:
                            try:
                                await chan.send(embed=discord.Embed(
                                    title="Club Join",
                                    description=f"`#{jtag}` joined **{cfg.get('name','?')}**",
                                    color=SUCCESS
                                ))
                            except Exception:
                                pass

                if left and chan:
                    for ltag in left:
                        try:
                            await chan.send(embed=discord.Embed(
                                title="Club Leave",
                                description=f"`#{ltag}` left **{cfg.get('name','?')}**",
                                color=ERROR
                            ))
                        except Exception:
                            pass

            # Save the snapshot for next diff
            await self.config.guild(guild).last_seen.set(updated_seen)

async def setup(bot: Red):
    await bot.add_cog(ClubSync(bot))

