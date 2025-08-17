from redbot.core import commands
from redbot.core.bot import Red
import discord
from typing import List, Dict, Any, Optional
from discord.ui import View, button, Button

from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
# import only what we use here to avoid failures on stale helpers
from brawlcommon.utils import (
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
    """Deep Brawl Stars lookups (players, clubs, brawlers, rankings, events)."""

    def __init__(self, bot: Red):
        self.bot = bot
        self._apis: Dict[int, BrawlStarsAPI] = {}

    async def cog_unload(self):
        for api in self._apis.values():
            await api.close()

    async def _api(self, guild: discord.Guild) -> BrawlStarsAPI:
        token = await get_brawl_api_token(self.bot)  # raises nice error if not set
        cli = self._apis.get(guild.id)
        if not cli:
            cli = BrawlStarsAPI(token)
            self._apis[guild.id] = cli
        return cli

    @commands.group()
    async def bs(self, ctx):
        """Brawl Stars API commands."""
        pass

    # ----- Player -----
    @bs.command()
    async def player(self, ctx, tag: str):
        """Show a player's profile (multi-page)."""
        api = await self._api(ctx.guild)
        p = await api.get_player(tag)

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
        e1.set_footer(text=ctx.guild.name)

        lines = []
        for b in sorted(brawlers, key=lambda x: (-x.get("trophies",0), x.get("name",""))):
            lines.append(f"**{b.get('name')}** — {b.get('trophies',0):,} 🏆  | Pwr {b.get('power',0)} | R{b.get('rank',0)}")
        e2 = discord.Embed(title="Brawlers", color=ACCENT, description="\n".join(lines[:20]) or "—")

        pages = [e1, e2]
        view = EmbedPager(pages, author_id=ctx.author.id)
        await ctx.send(embed=e1, view=view)

    # ----- Club overview -----
    @bs.command()
    async def club(self, ctx, club_tag: str):
        """Show club overview."""
        api = await self._api(ctx.guild)
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

    # ----- Club roster (paginated) -----
    @bs.command()
    async def clubmembers(self, ctx, club_tag: str):
        """List all members of a club (paginated)."""
        api = await self._api(ctx.guild)
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

    # ----- Brawlers catalog -----
    @bs.command()
    async def brawlers(self, ctx):
        """List all brawlers (paginated)."""
        api = await self._api(ctx.guild)
        data = await api.get_brawlers()
        items = data.get("items") or []
        items.sort(key=lambda b: (b.get("rarity",{}).get("rank", 99), b.get("name","")))

        pages: List[discord.Embed] = []
        chunk = 12
        for i in range(0, len(items), chunk):
            part = items[i:i+chunk]
            lines = [f"**{b.get('name')}** — {b.get('rarity',{}).get('name','?')}" for b in part]
            thumb_id = part[0].get("id",0) if part else 0
            e = discord.Embed(title=f"Brawlers ({i+1}-{min(i+chunk,len(items))}/{len(items)})", description="\n".join(lines) or "—", color=ACCENT)
            if thumb_id:
                e.set_thumbnail(url=brawler_icon_url(thumb_id))
            pages.append(e)

        view = EmbedPager(pages, author_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    # ----- Rankings -----
    @bs.group()
    async def rankings(self, ctx):
        """Global or country rankings."""
        pass

    @rankings.command(name="players")
    async def rankings_players(self, ctx, country: str = "global", limit: int = 25):
        """Top players (global or country code like 'AU', 'US')."""
        api = await self._api(ctx.guild)
        data = await api.get_rankings_players(country.lower(), limit)
        items = data.get("items") or []

        lines = []
        for i, it in enumerate(items, start=1):
            lines.append(f"**{i}.** {it.get('name')} ({it.get('tag')}) • {it.get('trophies',0):,} 🏆")
        e = discord.Embed(title=f"Top Players — {country.upper()}", description="\n".join(lines) or "—", color=GOLD)
        await ctx.send(embed=e)

    @rankings.command(name="clubs")
    async def rankings_clubs(self, ctx, country: str = "global", limit: int = 25):
        """Top clubs (global or country code)."""
        api = await self._api(ctx.guild)
        data = await api.get_rankings_clubs(country.lower(), limit)
        items = data.get("items") or []

        lines = []
        for i, it in enumerate(items, start=1):
            lines.append(f"**{i}.** {it.get('name')} ({it.get('tag')}) • {it.get('trophies',0):,} 🏆 • members {it.get('memberCount',0)}")
        e = discord.Embed(title=f"Top Clubs — {country.upper()}", description="\n".join(lines) or "—", color=GOLD)
        await ctx.send(embed=e)

    @rankings.command(name="brawler")
    async def rankings_brawler(self, ctx, id_or_name: str, country: str = "global", limit: int = 25):
        """Top players for a specific brawler."""
        api = await self._api(ctx.guild)
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

    # ----- Events / rotation -----
    @bs.command()
    async def events(self, ctx):
        """Current event rotation (maps & modes)."""
        api = await self._api(ctx.guild)
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

async def setup(bot: Red):
    await bot.add_cog(BSInfo(bot))
