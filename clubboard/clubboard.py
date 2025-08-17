# clubboard/clubboard.py
# ---- TLGBS bootstrap: make sibling "brawlcommon" importable on cold start ----
import sys, pathlib
_COGS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(_COGS_DIR) not in sys.path:
    sys.path.insert(0, str(_COGS_DIR))
# ------------------------------------------------------------------------------

from redbot.core import commands, Config
from redbot.core.bot import Red
import discord
from discord.ext import tasks
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import club_badge_url

ACCENT  = discord.Color.from_rgb(66, 135, 245)
SUCCESS = discord.Color.green()
WARN    = discord.Color.orange()
ERROR   = discord.Color.red()

MAX_MEMBERS = 30

class ClubBoard(commands.Cog):
    """Live board of all tracked clubs, updated every 5 minutes."""

    __version__ = "0.2.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xCB0A4D, force_registration=True)
        default_guild = {
            "channel_id": None,
            "message_id": None,
        }
        self.config.register_guild(**default_guild)
        self._apis: Dict[int, BrawlStarsAPI] = {}
        self._lock: Dict[int, bool] = {}
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

    @commands.group()
    @commands.guild_only()
    async def clubboard(self, ctx):
        """Configure and manage the live club board."""
        pass

    @clubboard.command(name="setchannel")
    @commands.has_guild_permissions(manage_guild=True)
    async def clubboard_setchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel where the board lives (one message edited every 5 minutes)."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).message_id.set(None)
        await ctx.send(embed=discord.Embed(title="Channel set", description=f"Board will be posted in {channel.mention}.", color=SUCCESS))

    @clubboard.command(name="refresh")
    @commands.has_guild_permissions(manage_guild=True)
    async def clubboard_refresh(self, ctx):
        """Force an immediate refresh."""
        await self._render(ctx.guild, force_new=False)
        await ctx.tick()

    @clubboard.command(name="start")
    @commands.has_guild_permissions(manage_guild=True)
    async def clubboard_start(self, ctx):
        """Ensure the loop is running and a board is present."""
        if not self.loop.is_running():
            self.loop.start()
        await self._render(ctx.guild, force_new=False)
        await ctx.tick()

    @clubboard.command(name="stop")
    @commands.has_guild_permissions(manage_guild=True)
    async def clubboard_stop(self, ctx):
        """Stop the auto-update loop (board message remains)."""
        if self.loop.is_running():
            self.loop.cancel()
        await ctx.tick()

    @tasks.loop(minutes=5)
    async def loop(self):
        for guild in list(self.bot.guilds):
            try:
                await self._render(guild, force_new=False)
            except Exception:
                continue

    @loop.before_loop
    async def before(self):
        await self.bot.wait_until_ready()

    async def _render(self, guild: discord.Guild, force_new: bool):
        if not guild:
            return
        if self._lock.get(guild.id):
            return
        self._lock[guild.id] = True
        try:
            conf = await self.config.guild(guild).all()
            channel = guild.get_channel(conf.get("channel_id") or 0)
            if not channel:
                return

            clubs_cog = self.bot.get_cog("Clubs")
            tracked = await clubs_cog.config.guild(guild).clubs() if clubs_cog else {}
            if not tracked:
                await channel.send(embed=discord.Embed(title="No clubs configured", description="Use `[p]clubs add #TAG` to add clubs.", color=WARN))
                return

            api = await self._api(guild)

            # Build lines, live fetch
            rows = []
            for ctag, cfg in tracked.items():
                try:
                    cinfo = await api.get_club_by_tag(ctag)
                except Exception:
                    continue
                members = len(cinfo.get("members") or [])
                full = members >= MAX_MEMBERS
                req = int(cinfo.get("requiredTrophies", cfg.get("required_trophies", 0)))
                name = cinfo.get("name") or cfg.get("name") or f"#{ctag}"
                ctype = (cinfo.get("type") or "unknown").title()
                ctroph = cinfo.get("trophies", 0)
                badge = cinfo.get("badgeId") or 0
                status = "FULL" if full else "Open"
                rows.append({
                    "ctag": ctag,
                    "name": name,
                    "members": members,
                    "req": req,
                    "ctype": ctype,
                    "troph": ctroph,
                    "badge": badge,
                    "status": status,
                })

            # Sort: lowest members first
            rows.sort(key=lambda r: (r["members"], -r["req"]))

            # Compose embed
            now = datetime.now(timezone.utc).strftime("%H:%M UTC")
            desc_lines = []
            for r in rows:
                line = (
                    f"**{r['name']}**  `#{r['ctag']}` — "
                    f"**{r['members']}/{MAX_MEMBERS}** members • req **{r['req']:,}** • "
                    f"club **{r['troph']:,}** • {r['ctype']} • "
                    f"{'🔴 FULL' if r['status']=='FULL' else '🟢 OPEN'}"
                )
                desc_lines.append(line)

            emb = discord.Embed(
                title=f"{guild.name} — Club Overview",
                description="\n".join(desc_lines)[:4000] or "—",
                color=ACCENT
            )
            emb.set_footer(text=f"Updated {now}")

            # Show a badge if there’s a clear first candidate
            if rows and rows[0]["badge"]:
                emb.set_thumbnail(url=club_badge_url(rows[0]["badge"]))

            # Post or edit
            msg_id = conf.get("message_id")
            msg: Optional[discord.Message] = None
            if msg_id:
                try:
                    msg = await channel.fetch_message(msg_id)
                except Exception:
                    msg = None
            if not msg or force_new:
                msg = await channel.send(embed=emb)
                await self.config.guild(guild).message_id.set(msg.id)
            else:
                await msg.edit(embed=emb)
        finally:
            self._lock[guild.id] = False


async def setup(bot: Red):
    await bot.add_cog(ClubBoard(bot))
