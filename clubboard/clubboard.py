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
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone

from brawlcommon.admin import bs_admin_check
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import club_badge_url

ACCENT  = discord.Color.from_rgb(66, 135, 245)
SUCCESS = discord.Color.from_rgb(46, 204, 113)
WARN    = discord.Color.from_rgb(241, 196, 15)
ERROR   = discord.Color.from_rgb(231, 76, 60)

MAX_MEMBERS = 30
STYLE_CHOICES = {"compact", "cards"}

def _progress_bar(current: int, total: int, width: int = 12) -> str:
    if total <= 0:
        return "░" * width
    frac = max(0.0, min(1.0, current / total))
    filled = int(round(frac * width))
    return "█" * filled + "░" * (width - filled)

def _status_emoji(current: int) -> str:
    return "🟢" if current < MAX_MEMBERS else "🔴"

def _club_line(name: str, ctag: str, members: int, req: int, club_troph: int, ctype: str) -> str:
    bar = _progress_bar(members, MAX_MEMBERS, width=10)
    return f"{_status_emoji(members)} **{name}** `#{ctag}`\n {bar}  **{members}/{MAX_MEMBERS}**  • Req **{req:,}**  • Club **{club_troph:,}**  • {ctype}"

def _split_rows(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    open_rows = [r for r in rows if r["members"] < MAX_MEMBERS]
    full_rows = [r for r in rows if r["members"] >= MAX_MEMBERS]
    open_rows.sort(key=lambda r: (r["members"], -r["req"]))
    full_rows.sort(key=lambda r: (-r["members"], -r["req"]))
    return open_rows, full_rows

class ClubBoard(commands.Cog):
    """Live board of all tracked clubs, updated every 5 minutes."""

    __version__ = "0.3.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xCB0A4D, force_registration=True)
        default_guild = {"channel_id": None, "message_id": None, "style": "compact", "title": None}
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
    @bs_admin_check()
    async def setchannel(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).message_id.set(None)
        await ctx.send(embed=discord.Embed(
            title="Channel set", description=f"Board will be posted in {channel.mention}.", color=SUCCESS
        ))

    @clubboard.command(name="style")
    @bs_admin_check()
    async def style(self, ctx, style: str):
        style = style.lower()
        if style not in STYLE_CHOICES:
            return await ctx.send(embed=discord.Embed(
                title="Invalid style", description="Choose either `compact` or `cards`.", color=ERROR
            ))
        await self.config.guild(ctx.guild).style.set(style)
        await ctx.send(embed=discord.Embed(
            title="Style updated", description=f"Board style set to **{style}**.", color=SUCCESS
        ))
        await self._render(ctx.guild, force_new=False)

    @clubboard.command(name="title")
    @bs_admin_check()
    async def title(self, ctx, *, title: Optional[str] = None):
        await self.config.guild(ctx.guild).title.set(title)
        await ctx.send(embed=discord.Embed(
            title="Title updated", description=f"Board title set to: **{title or 'default'}**.", color=SUCCESS
        ))
        await self._render(ctx.guild, force_new=False)

    @clubboard.command(name="refresh")
    @bs_admin_check()
    async def refresh(self, ctx):
        await self._render(ctx.guild, force_new=False)
        await ctx.tick()

    @clubboard.command(name="start")
    @bs_admin_check()
    async def start(self, ctx):
        if not self.loop.is_running():
            self.loop.start()
        await self._render(ctx.guild, force_new=False)
        await ctx.tick()

    @clubboard.command(name="stop")
    @bs_admin_check()
    async def stop(self, ctx):
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
                await self.config.guild(guild).message_id.set(None)
                await channel.send(embed=discord.Embed(
                    title="No clubs configured", description="Use `[p]clubs add #TAG` to add clubs.", color=WARN
                ))
                return

            api = await self._api(guild)
            rows: List[Dict[str, Any]] = []
            for ctag, cfg in tracked.items():
                try:
                    cinfo = await api.get_club_by_tag(ctag)
                except Exception:
                    continue
                rows.append({
                    "ctag": ctag,
                    "name": cinfo.get("name") or cfg.get("name") or f"#{ctag}",
                    "members": len(cinfo.get("members") or []),
                    "req": int(cinfo.get("requiredTrophies", cfg.get("required_trophies", 0))),
                    "ctype": (cinfo.get("type") or "unknown").title(),
                    "troph": cinfo.get("trophies", 0),
                    "badge": cinfo.get("badgeId") or 0,
                })

            open_rows, full_rows = _split_rows(rows)
            style = conf.get("style") or "compact"
            title = conf.get("title") or f"{guild.name} — Club Overview"
            color = SUCCESS if open_rows else ERROR

            emb = discord.Embed(title=title, color=color)
            now = datetime.now(timezone.utc).strftime("%H:%M UTC")
            emb.set_footer(text=f"Updated {now} • {('Open: ' + str(len(open_rows))) if open_rows else 'No open clubs'} | Full: {len(full_rows)}")

            best = (open_rows or rows)
            if best and best[0].get("badge"):
                emb.set_thumbnail(url=club_badge_url(best[0]["badge"]))

            if style == "cards" and len(rows) <= 24:
                if open_rows:
                    emb.add_field(name="🟢 Open Clubs", value="\u200b", inline=False)
                    for r in open_rows:
                        bar = _progress_bar(r["members"], MAX_MEMBERS, width=10)
                        value = f"{bar} **{r['members']}/{MAX_MEMBERS}**\nReq **{r['req']:,}** • Club **{r['troph']:,}** • {r['ctype']}\n`#{r['ctag']}`"
                        emb.add_field(name=f"{r['name']}", value=value, inline=True)
                if full_rows:
                    emb.add_field(name="\u200b", value="\u200b", inline=False)
                    emb.add_field(name="🔴 Full Clubs", value="\u200b", inline=False)
                    for r in full_rows:
                        bar = _progress_bar(r["members"], MAX_MEMBERS, width=10)
                        value = f"{bar} **{r['members']}/{MAX_MEMBERS}**\nReq **{r['req']:,}** • Club **{r['troph']:,}** • {r['ctype']}\n`#{r['ctag']}`"
                        emb.add_field(name=f"{r['name']}", value=value, inline=True)
                if len(emb.fields) > 25:
                    style = "compact"

            if style == "compact":
                sections: List[str] = []
                if open_rows:
                    lines = [_club_line(r["name"], r["ctag"], r["members"], r["req"], r["troph"], r["ctype"]) for r in open_rows]
                    sections.append("**🟢 Open Clubs**\n" + "\n".join(lines))
                if full_rows:
                    lines = [_club_line(r["name"], r["ctag"], r["members"], r["req"], r["troph"], r["ctype"]) for r in full_rows]
                    sections.append("**🔴 Full Clubs**\n" + "\n".join(lines))
                emb.description = "\n\n".join(sections)[:4000] or "—"

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
