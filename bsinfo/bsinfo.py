# bsinfo/bsinfo.py
# ---- TLGBS bootstrap: make sibling "brawlcommon" importable on cold start ----
import sys, pathlib
_COGS_DIR = pathlib.Path(__file__).resolve().parents[1]  # .../cogs
if str(_COGS_DIR) not in sys.path:
    sys.path.insert(0, str(_COGS_DIR))
# ------------------------------------------------------------------------------

from redbot.core import commands, Config
from redbot.core.bot import Red
import discord
from typing import List, Dict, Any, Optional
from discord.ui import View, button, Button

from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import (
    tag_pretty,
    player_avatar_url,
    club_badge_url,
    brawler_icon_url,
    mode_icon_url,
    map_image_url,
    find_brawler_id_by_name,
)

ACCENT  = discord.Color.from_rgb(66,135,245)
SUCCESS = discord.Color.green()
WARN    = discord.Color.orange()
ERROR   = discord.Color.red()
GOLD    = discord.Color.gold()

# ---------- Simple paginator ----------
class EmbedPager(View):
    def __init__(self, pages: List[discord.Embed], author_id: int, timeout: int = 120):
        super().__init__(timeout=timeout)
        self.pages = pages or [discord.Embed(title="No pages", color=ERROR)]
        self.i = 0
        self.author_id = author_id

    async def on_timeout(self):
        for c in self.children:
            c.disabled = True

    async def _update(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.pages[self.i], view=self)

    @button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.defer()
        self.i = (self.i - 1) % len(self.pages)
        await self._update(interaction)

    @button(label="▶", style=discord.ButtonStyle.primary)
    async def nxt(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.defer()
        self.i = (self.i + 1) % len(self.pages)
        await self._update(interaction)

class BSInfo(commands.Cog):
    """
    Deep Brawl Stars lookups (players, clubs, brawlers, rankings, events)
    + per-user tag storage (max 3) used as defaults for lookups.
    Also exposes `!bs start` which DMs the application flow via Onboarding.
    """

    __version__ = "0.7.0"

    def __init__(self, bot: Red):
        self.bot = bot
        # Unique, valid hex identifier
        self.config = Config.get_conf(self, identifier=0xB51F0C, force_registration=True)
        default_user = {"tags": [], "default_index": 0, "ign_cache": "", "club_tag_cache": ""}
        self.config.register_user(**default_user)
        self._apis: Dict[int, BrawlStarsAPI] = {}

    async def cog_unload(self):
        for api in self._apis.values():
            await api.close()

    # ---------- API client ----------
    async def _api(self, guild: discord.Guild) -> BrawlStarsAPI:
        token = await get_brawl_api_token(self.bot)  # raises if not set
        cli = self._apis.get(guild.id)
        if not cli:
            cli = BrawlStarsAPI(token)
            self._apis[guild.id] = cli
        return cli

    # ---------- Small helpers ----------
    async def _get_default_tag(self, user: discord.User) -> Optional[str]:
        u = await self.config.user(user).all()
        if not u["tags"]:
            return None
        i = max(0, min(u["default_index"], len(u["tags"]) - 1))
        return u["tags"][i]

    async def _cache_player_bits(self, user: discord.User, pdata: Dict[str, Any]):
        await self.config.user(user).ign_cache.set(pdata.get("name") or "")
        club = pdata.get("club") or {}
        await self.config.user(user).club_tag_cache.set((club.get("tag") or "").replace("#",""))

    # ============================================================
    #                           COMMANDS
    # ============================================================

    @commands.group()
    async def bs(self, ctx):
        """Brawl Stars commands."""
        pass

    # ---------- Tag management: !bs tags ----------
    @bs.group(name="tags")
    async def bs_tags(self, ctx):
        """Manage your saved tags (max 3)."""
        pass

    @bs_tags.command(name="save")
    async def bs_tags_save(self, ctx, tag: str):
        """Save a tag after validating via the API."""
        if ctx.guild is None:
            return await ctx.send("This command can only be used in servers.")
        api = await self._api(ctx.guild)
        pdata = await api.get_player(tag)  # validate
        norm = api.norm_tag(tag)
        async with self.config.user(ctx.author).tags() as tags:
            if norm in tags:
                return await ctx.send(embed=discord.Embed(title="Tag already saved", description=f"{tag_pretty(norm)} is already in your list.", color=WARN))
            if len(tags) >= 3:
                return await ctx.send(embed=discord.Embed(title="Limit reached", description="You already have 3 tags saved.", color=ERROR))
            tags.append(norm)
        await self._cache_player_bits(ctx.author, pdata)
        await ctx.send(embed=discord.Embed(title="Tag saved", description=f"Added **{tag_pretty(norm)}**.", color=SUCCESS))

    @bs_tags.command(name="list")
    async def bs_tags_list(self, ctx):
        """Show your saved tags and the default one."""
        u = await self.config.user(ctx.author).all()
        tags = u["tags"]
        if not tags:
            return await ctx.send(embed=discord.Embed(title="No tags yet", description="Use `[p]bs tags save <tag>` to add one.", color=WARN))
        lines = []
        for i, t in enumerate(tags, start=1):
            star = " **(default)**" if (i - 1) == u["default_index"] else ""
            lines.append(f"**{i}.** {tag_pretty(t)}{star}")
        e = discord.Embed(title=f"{ctx.author.display_name}'s tags", description="\n".join(lines), color=ACCENT)
        await ctx.send(embed=e)

    @bs_tags.command(name="setdefault")
    async def bs_tags_setdefault(self, ctx, index: int):
        """Set which tag (1..3) is your default."""
        i = index - 1
        tags = await self.config.user(ctx.author).tags()
        if not (0 <= i < len(tags)):
            return await ctx.send(embed=discord.Embed(title="Invalid index", description="Choose an index from `[p]bs tags list`.", color=ERROR))
        await self.config.user(ctx.author).default_index.set(i)
        await ctx.send(embed=discord.Embed(title="Default updated", description=f"Default tag is now **{tag_pretty(tags[i])}**.", color=SUCCESS))

    @bs_tags.command(name="move")
    async def bs_tags_move(self, ctx, index_from: int, index_to: int):
        """Reorder your tags: move FROM to TO."""
        f = index_from - 1
        t = index_to - 1
        async with self.config.user(ctx.author).all() as u:
            tags: List[str] = u["tags"]
            if not (0 <= f < len(tags)) or not (0 <= t < len(tags)):
                return await ctx.send(embed=discord.Embed(title="Invalid index", description="Use indices from `[p]bs tags list`.", color=ERROR))
            item = tags.pop(f)
            tags.insert(t, item)
            # adjust default index
            if u["default_index"] == f:
                u["default_index"] = t
            elif f < u["default_index"] <= t:
                u["default_index"] -= 1
            elif t <= u["default_index"] < f:
                u["default_index"] += 1
        await ctx.send(embed=discord.Embed(title="Tags reordered", color=SUCCESS))

    @bs_tags.command(name="remove")
    async def bs_tags_remove(self, ctx, index: int):
        """Remove a saved tag by index (1..3)."""
        i = index - 1
        async with self.config.user(ctx.author).all() as u:
            tags: List[str] = u["tags"]
            if not (0 <= i < len(tags)):
                return await ctx.send(embed=discord.Embed(title="Invalid index", description="Use indices from `[p]bs tags list`.", color=ERROR))
            removed = tags.pop(i)
            if u["default_index"] >= len(tags):
                u["default_index"] = 0
        await ctx.send(embed=discord.Embed(title="Tag removed", description=f"Removed **{tag_pretty(removed)}**.", color=WARN))

    # ---------- Verify (guild-only) ----------
    @bs.command(name="verify")
    @commands.guild_only()
    async def bs_verify(self, ctx, tag: str):
        """Validate and save a tag (guild-only)."""
        await self.bs_tags_save(ctx, tag=tag)

    # ---------- Player (uses default tag if omitted) ----------
    @bs.command(name="player")
    async def bs_player(self, ctx, tag: Optional[str] = None):
        """Show a player's profile. If no tag is given, uses your default tag."""
        if ctx.guild is None and not tag:
            return await ctx.send("In DMs, please provide a tag: `bs player #TAG`.")
        api = await self._api(ctx.guild or self.bot.guilds[0])  # fallback guild for token/session
        use_tag = tag or await self._get_default_tag(ctx.author)
        if not use_tag:
            return await ctx.send(embed=discord.Embed(title="No default tag", description="Use `[p]bs tags save <tag>` first, or provide a tag.", color=ERROR))
        p = await api.get_player(use_tag)

        name = p.get("name","Unknown")
        tag_fmt = p.get("tag","")
        trophies = p.get("trophies",0)
        highest = p.get("highestTrophies",0)
        exp     = p.get("expLevel",0)
        icon_id = (p.get("icon") or {}).get("id",0)
        club    = p.get("club") or {}
        club_name = club.get("name","—")
        club_tag  = club.get("tag","—")
        brawlers  = p.get("brawlers") or []

        e1 = discord.Embed(title=f"{name} ({tag_fmt})", color=ACCENT, description=f"**Club:** {club_name} {club_tag}")
        e1.add_field(name="Trophies", value=f"{trophies:,}")
        e1.add_field(name="Highest", value=f"{highest:,}")
        e1.add_field(name="EXP Level", value=str(exp))
        e1.add_field(name="Brawlers Owned", value=str(len(brawlers)))
        e1.set_thumbnail(url=player_avatar_url(icon_id))

        lines = []
        for b in sorted(brawlers, key=lambda x: (-x.get("trophies",0), x.get("name",""))):
            lines.append(f"**{b.get('name')}** — {b.get('trophies',0):,} 🏆  | Pwr {b.get('power',0)} | R{b.get('rank',0)}")
        e2 = discord.Embed(title="Brawlers", color=ACCENT, description="\n".join(lines[:20]) or "—")

        pages = [e1, e2]
        view = EmbedPager(pages, author_id=ctx.author.id)
        await ctx.send(embed=e1, view=view)

    # ---------- Club overview ----------
    @bs.command(name="club")
    async def bs_club(self, ctx, club_tag: str):
        """Show club overview."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
        c = await api.get_club_by_tag(club_tag)

        name   = c.get("name","Club")
        tag    = c.get("tag","")
        desc   = c.get("description","")
        badge  = c.get("badgeId") or 0
        ttype  = (c.get("type") or "unknown").title()
        req    = c.get("requiredTrophies",0)
        count  = len(c.get("members") or [])
        trophies = c.get("trophies", 0)

        e = discord.Embed(title=f"{name} ({tag})", color=GOLD, description=desc or "—")
        e.add_field(name="Type", value=ttype)
        e.add_field(name="Req. Trophies", value=f"{req:,}")
        e.add_field(name="Members", value=f"{count}/50")
        e.add_field(name="Club Trophies", value=f"{trophies:,}")
        if badge:
            e.set_thumbnail(url=club_badge_url(badge))
        await ctx.send(embed=e)

    # ---------- Club roster (paginated) ----------
    @bs.command(name="clubmembers")
    async def bs_clubmembers(self, ctx, club_tag: str):
        """List all members of a club (paginated)."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
        m = await api.get_club_members(club_tag)
        items = m.get("items") or []

        pages: List[discord.Embed] = []
        chunk = 20
        for i in range(0, len(items), chunk):
            part = items[i:i+chunk]
            desc = "\n".join([f"**{it.get('name')}** ({it.get('tag')}) • {it.get('trophies',0):,} 🏆  • {it.get('role','member').title()}" for it in part]) or "—"
            e = discord.Embed(title=f"Members ({i+1}-{min(i+chunk, len(items))}/{len(items)})", description=desc, color=ACCENT)
            pages.append(e)

        if not pages:
            pages = [discord.Embed(title="No members found", color=ERROR)]

        view = EmbedPager(pages, author_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    # ---------- Brawlers catalog ----------
    @bs.command(name="brawlers")
    async def bs_brawlers(self, ctx):
        """List all brawlers (paginated)."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
        data = await api.get_brawlers()
        items = data.get("items") or []
        items.sort(key=lambda b: (b.get("rarity",{}).get("rank", 99), b.get("name","")))

        pages: List[discord.Embed] = []
        chunk = 12
        for i in range(0, len(items), chunk):
            part = items[i:i+chunk]
            lines = [f"**{b.get('name')}** — {b.get("rarity",{}).get("name","?")}" for b in part]
            thumb_id = part[0].get("id",0) if part else 0
            e = discord.Embed(title=f"Brawlers ({i+1}-{min(i+chunk,len(items))}/{len(items)})", description="\n".join(lines) or "—", color=ACCENT)
            if thumb_id:
                e.set_thumbnail(url=brawler_icon_url(thumb_id))
            pages.append(e)

        view = EmbedPager(pages, author_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    # ---------- Rankings ----------
    @bs.group(name="rankings")
    async def bs_rankings(self, ctx):
        """Global or country rankings."""
        pass

    @bs_rankings.command(name="players")
    async def bs_rankings_players(self, ctx, country: str = "global", limit: int = 25):
        """Top players (global or country code like 'AU', 'US')."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
        data = await api.get_rankings_players(country.lower(), limit)
        items = data.get("items") or []

        lines = []
        for i, it in enumerate(items, start=1):
            lines.append(f"**{i}.** {it.get('name')} ({it.get('tag')}) • {it.get('trophies',0):,} 🏆")
        e = discord.Embed(title=f"Top Players — {country.upper()}", description="\n".join(lines) or "—", color=GOLD)
        await ctx.send(embed=e)

    @bs_rankings.command(name="clubs")
    async def bs_rankings_clubs(self, ctx, country: str = "global", limit: int = 25):
        """Top clubs (global or country code)."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
        data = await api.get_rankings_clubs(country.lower(), limit)
        items = data.get("items") or []

        lines = []
        for i, it in enumerate(items, start=1):
            lines.append(f"**{i}.** {it.get('name')} ({it.get('tag')}) • {it.get('trophies',0):,} 🏆 • members {it.get('memberCount',0)}")
        e = discord.Embed(title=f"Top Clubs — {country.upper()}", description="\n".join(lines) or "—", color=GOLD)
        await ctx.send(embed=e)

    @bs_rankings.command(name="brawler")
    async def bs_rankings_brawler(self, ctx, id_or_name: str, country: str = "global", limit: int = 25):
        """Top players for a specific brawler."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
        all_b = await api.get_brawlers()
        bid: Optional[int] = None
        if id_or_name.isdigit():
            bid = int(id_or_name)
        else:
            bid = find_brawler_id_by_name(all_b, id_or_name)
        if bid is None:
            return await ctx.send(embed=discord.Embed(title="Brawler not found", color=ERROR))

        data = await api.get_rankings_brawler(country.lower(), bid, limit)
        items = data.get("items") or []

        lines = []
        for i, it in enumerate(items, start=1):
            player = it.get("player") or {}
            lines.append(f"**{i}.** {player.get('name')} ({player.get('tag')}) • {it.get('trophies',0):,} 🏆")
        e = discord.Embed(title=f"Top {id_or_name} — {country.upper()}", description="\n".join(lines) or "—", color=GOLD)
        e.set_thumbnail(url=brawler_icon_url(bid))
        await ctx.send(embed=e)

    # ---------- Events ----------
    @bs.command(name="events")
    async def bs_events(self, ctx):
        """Current event rotation (maps & modes)."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
        rot = await api.get_events_rotation()
        active = rot.get("active") or rot.get("events") or rot.get("items") or rot
        if isinstance(active, dict):
            active = active.get("events") or active.get("items") or []

        pages: List[discord.Embed] = []
        for ev in (active or []):
            mode = (ev.get("mode") or ev.get("event",{}).get("mode") or {}).get("name") if isinstance(ev.get("mode"), dict) else (ev.get("mode") or "Unknown")
            map_name = (ev.get("map") or ev.get("event",{}).get("map") or {}).get("name") if isinstance(ev.get("map"), dict) else (ev.get("map") or "Unknown")
            map_id = (ev.get("map") or {}).get("id") or (ev.get("event",{}).get("map") or {}).get("id") or 0
            e = discord.Embed(title=map_name, description=f"Mode: **{mode}**", color=ACCENT)
            if mode: e.set_thumbnail(url=mode_icon_url(str(mode)))
            if map_id: e.set_image(url=map_image_url(int(map_id)))
            pages.append(e)

        if not pages:
            pages = [discord.Embed(title="No active events reported.", color=WARN)]

        view = EmbedPager(pages, author_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    # ---------- Start application (server-only -> DMs) ----------
    @bs.command(name="start")
    @commands.guild_only()
    async def bs_start(self, ctx):
        """
        Start the application in DMs.
        """
        ob = self.bot.get_cog("Onboarding")
        if not ob or not hasattr(ob, "start_application_dm"):
            return await ctx.send(embed=discord.Embed(title="Onboarding not loaded", description="Ask an admin to load the Onboarding cog.", color=ERROR))

        try:
            dm = await ctx.author.create_dm()
            await dm.send(embed=discord.Embed(title="Club Application", description="Let's get you set up! Follow the prompts here.", color=ACCENT))
        except discord.Forbidden:
            return await ctx.send(embed=discord.Embed(title="I can't DM you", description="Enable DMs from server members and try again.", color=ERROR))

        await ctx.send(embed=discord.Embed(title="Check your DMs", description="I’ve sent you a message to continue your application.", color=SUCCESS))
        # hand off to onboarding in DM context
        await ob.start_application_dm(ctx.guild, ctx.author)  # type: ignore[attr-defined]

async def setup(bot: Red):
    await bot.add_cog(BSInfo(bot))
