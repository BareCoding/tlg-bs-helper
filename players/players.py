# players/players.py
from redbot.core import commands, Config
from redbot.core.bot import Red
import discord
from typing import List, Dict, Any
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import player_avatar_url, tag_pretty

ACCENT  = discord.Color.from_rgb(66,135,245)
SUCCESS = discord.Color.green()
WARN    = discord.Color.orange()
ERROR   = discord.Color.red()
GOLD    = discord.Color.gold()

class Players(commands.Cog):
    """Brawl Stars: tag management, player stats, and server leaderboards."""

    __author__  = "Threat Level Gaming"
    __version__ = "0.5.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xBEEFBEEF, force_registration=True)
        default_user  = {"tags": [], "default_index": 0, "ign_cache": "", "club_tag_cache": ""}
        default_guild = {"stats": {}}
        self.config.register_user(**default_user)
        self.config.register_guild(**default_guild)
        self._apis: Dict[int, BrawlStarsAPI] = {}

    async def cog_unload(self):
        for api in self._apis.values():
            await api.close()

    async def _api(self, guild: discord.Guild) -> BrawlStarsAPI:
        token = await get_brawl_api_token(self.bot)
        cli = self._apis.get(guild.id)
        if not cli:
            cli = BrawlStarsAPI(token)
            self._apis[guild.id] = cli
        return cli

    # -------- Tags: save/view/reorder/setdefault/remove --------

    @commands.group()
    async def tags(self, ctx):
        """Manage your saved Brawl Stars tags (max 3)."""
        pass

    @tags.command()
    async def save(self, ctx, tag: str):
        """Save a tag after validating via the API."""
        api = await self._api(ctx.guild)
        pdata = await api.get_player(tag)
        norm = api.norm_tag(tag)
        async with self.config.user(ctx.author).tags() as tags:
            if norm in tags:
                e = discord.Embed(title="Tag already saved", description=f"{tag_pretty(norm)} is already in your list.", color=WARN)
                return await ctx.send(embed=e)
            if len(tags) >= 3:
                e = discord.Embed(title="Limit reached", description="You already have 3 tags saved.", color=ERROR)
                return await ctx.send(embed=e)
            tags.append(norm)
        await self._cache_player_bits(ctx.author, pdata)
        e = discord.Embed(title="Tag saved", description=f"Added **{tag_pretty(norm)}**.", color=SUCCESS)
        await ctx.send(embed=e)

    @tags.command()
    async def list(self, ctx):
        """Show your saved tags and the default one."""
        u = await self.config.user(ctx.author).all()
        tags = u["tags"]
        if not tags:
            e = discord.Embed(title="No tags yet", description="Use `[p]tags save <tag>` to add one.", color=WARN)
            return await ctx.send(embed=e)
        lines = []
        for i, t in enumerate(tags, start=1):
            star = " **(default)**" if (i - 1) == u["default_index"] else ""
            lines.append(f"**{i}.** {tag_pretty(t)}{star}")
        e = discord.Embed(title=f"{ctx.author.display_name}'s tags", description="\n".join(lines), color=ACCENT)
        await ctx.send(embed=e)

    @tags.command()
    async def setdefault(self, ctx, index: int):
        """Set which tag (1..3) is your default."""
        i = index - 1
        tags = await self.config.user(ctx.author).tags()
        if not (0 <= i < len(tags)):
            e = discord.Embed(title="Invalid index", description="Choose an index from `[p]tags list`.", color=ERROR)
            return await ctx.send(embed=e)
        await self.config.user(ctx.author).default_index.set(i)
        e = discord.Embed(title="Default updated", description=f"Default tag is now **{tag_pretty(tags[i])}**.", color=SUCCESS)
        await ctx.send(embed=e)

    @tags.command()
    async def move(self, ctx, index_from: int, index_to: int):
        """Reorder your tags: move FROM to TO."""
        f = index_from - 1
        t = index_to - 1
        async with self.config.user(ctx.author).all() as u:
            tags: List[str] = u["tags"]
            if not (0 <= f < len(tags)) or not (0 <= t < len(tags)):
                e = discord.Embed(title="Invalid index", description="Use indices from `[p]tags list`.", color=ERROR)
                return await ctx.send(embed=e)
            item = tags.pop(f)
            tags.insert(t, item)
            if u["default_index"] == f:
                u["default_index"] = t
            elif f < u["default_index"] <= t:
                u["default_index"] -= 1
            elif t <= u["default_index"] < f:
                u["default_index"] += 1
        e = discord.Embed(title="Tags reordered", color=SUCCESS)
        await ctx.send(embed=e)

    @tags.command()
    async def remove(self, ctx, index: int):
        """Remove a saved tag by index (1..3)."""
        i = index - 1
        async with self.config.user(ctx.author).all() as u:
            tags: List[str] = u["tags"]
            if not (0 <= i < len(tags)):
                e = discord.Embed(title="Invalid index", description="Use indices from `[p]tags list`.", color=ERROR)
                return await ctx.send(embed=e)
            removed = tags.pop(i)
            if u["default_index"] >= len(tags):
                u["default_index"] = 0
        e = discord.Embed(title="Tag removed", description=f"Removed **{tag_pretty(removed)}**.", color=WARN)
        await ctx.send(embed=e)

    # -------- Player stats & leaderboard --------

    @commands.group()
    async def bs(self, ctx):
        """Brawl Stars lookups."""
        pass

    @bs.command()
    async def verify(self, ctx, tag: str):
        """Quickly validate and save a tag (same as tags save)."""
        await ctx.invoke(self.save, tag=tag)

    @bs.command()
    async def me(self, ctx):
        """Show stats for your default tag."""
        u = await self.config.user(ctx.author).all()
        if not u["tags"]:
            e = discord.Embed(title="No tags", description="Use `[p]tags save <tag>` first.", color=ERROR)
            return await ctx.send(embed=e)
        await self._send_player_embed(ctx, u["tags"][u["default_index"]])

    @bs.command()
    async def player(self, ctx, tag: str):
        """Show stats for a specific tag."""
        api = await self._api(ctx.guild)
        pdata = await api.get_player(tag)
        await self._send_player_embed_from_data(ctx, pdata)

    @bs.command()
    async def leaderboard(self, ctx):
        """Server trophies leaderboard for saved default tags."""
        api = await self._api(ctx.guild)
        rows = []
        for m in ctx.guild.members:
            u = await self.config.user(m).all()
            if not u["tags"]:
                continue
            try:
                pdata = await api.get_player(u["tags"][u["default_index"]])
            except Exception:
                continue
            rows.append((pdata.get("trophies", 0), m.display_name, pdata.get("name",""), pdata.get("tag","")))
        if not rows:
            e = discord.Embed(title="Leaderboard", description="No verified users yet.", color=ACCENT)
            return await ctx.send(embed=e)
        rows.sort(reverse=True, key=lambda r: r[0])
        top = rows[:20]
        desc = "\n".join([f"**{i+1}.** {r[1]} — {r[2]} ({r[3]}) • {r[0]:,} 🏆" for i, r in enumerate(top)])
        emb = discord.Embed(title=f"{ctx.guild.name} — Trophies Leaderboard", description=desc, color=GOLD)
        await ctx.send(embed=emb)

    # -------- helpers --------

    async def _cache_player_bits(self, user: discord.User, pdata: Dict[str, Any]):
        await self.config.user(user).ign_cache.set(pdata.get("name") or "")
        club = pdata.get("club") or {}
        await self.config.user(user).club_tag_cache.set((club.get("tag") or "").replace("#",""))

    async def _send_player_embed(self, ctx, tag_norm: str):
        api = await self._api(ctx.guild)
        pdata = await api.get_player(tag_norm)
        await self._send_player_embed_from_data(ctx, pdata)

    async def _send_player_embed_from_data(self, ctx, pdata: Dict[str, Any]):
        name    = pdata.get("name","Unknown")
        tag     = pdata.get("tag","")
        trophies= pdata.get("trophies",0)
        exp     = pdata.get("expLevel",0)
        h_troph = pdata.get("highestTrophies", 0)
        brawlers= len(pdata.get("brawlers") or [])
        icon_id = (pdata.get("icon") or {}).get("id",0)
        club    = pdata.get("club") or {}
        club_name = club.get("name", "—")
        club_tag  = club.get("tag", "—")

        e = discord.Embed(
            title=f"{name} ({tag})",
            description=f"**Club:** {club_name} {club_tag}",
            color=ACCENT
        )
        e.add_field(name="Trophies", value=f"{trophies:,}")
        e.add_field(name="Highest", value=f"{h_troph:,}")
        e.add_field(name="EXP Level", value=str(exp))
        e.add_field(name="Brawlers", value=str(brawlers))
        e.set_thumbnail(url=player_avatar_url(icon_id))
        e.set_footer(text=ctx.guild.name)
        await ctx.send(embed=e)

async def setup(bot: Red):
    await bot.add_cog(Players(bot))
