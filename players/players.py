# players/players.py
from redbot.core import commands, Config
from redbot.core.bot import Red
import discord
from typing import List, Dict, Any
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import player_avatar_url, tag_pretty

class Players(commands.Cog):
    """Player verification, tags, stats, and leaderboards."""

    __author__  = "Threat Level Gaming"
    __version__ = "0.3.0"

    def __init__(self, bot: Red):
        self.bot = bot
        # 0xBEEFBEEF is valid hex
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

    # ---------- Tag management (separate command group) ----------
    @commands.group(name="tags", invoke_without_command=True)
    async def tags(self, ctx):
        """Save, view, reorder, remove your Brawl Stars tags."""
        e = discord.Embed(title="Tag Commands", color=discord.Color.blurple())
        e.description = (
            "`[p]tags save <tag>` – save a tag (max 3)\n"
            "`[p]tags list` – view saved tags\n"
            "`[p]tags setdefault <index>` – set default tag (1..3)\n"
            "`[p]tags move <from> <to>` – reorder tags\n"
            "`[p]tags remove <index>` – remove tag"
        )
        await ctx.send(embed=e)

    @tags.command(name="save")
    async def tags_save(self, ctx, tag: str):
        api = await self._api(ctx.guild)
        _ = await api.get_player(tag)  # validate
        norm = api.norm_tag(tag)
        async with self.config.user(ctx.author).tags() as tags:
            if norm in tags:
                e = discord.Embed(title="Already Saved", description=f"{tag_pretty(norm)} is already in your list.", color=discord.Color.orange())
                return await ctx.send(embed=e)
            if len(tags) >= 3:
                e = discord.Embed(title="Limit Reached", description="You already have 3 tags saved. Remove one first.", color=discord.Color.red())
                return await ctx.send(embed=e)
            tags.append(norm)
        e = discord.Embed(title="Tag Saved", description=f"Added **{tag_pretty(norm)}** to your tags.", color=discord.Color.green())
        await ctx.send(embed=e)

    @tags.command(name="list")
    async def tags_list(self, ctx):
        u = await self.config.user(ctx.author).all()
        tags = u["tags"]
        if not tags:
            e = discord.Embed(title="No Tags Saved", description="Use `[p]tags save <tag>` to add one.", color=discord.Color.red())
            return await ctx.send(embed=e)
        lines = []
        for i, t in enumerate(tags, start=1):
            star = " **(default)**" if (i - 1) == u["default_index"] else ""
            lines.append(f"**{i}.** {tag_pretty(t)}{star}")
        e = discord.Embed(title=f"{ctx.author.display_name}'s Tags", description="\n".join(lines), color=discord.Color.blurple())
        await ctx.send(embed=e)

    @tags.command(name="setdefault")
    async def tags_setdefault(self, ctx, index: int):
        i = index - 1
        tags = await self.config.user(ctx.author).tags()
        if not (0 <= i < len(tags)):
            e = discord.Embed(title="Invalid Index", description="Choose a valid tag index from `[p]tags list`.", color=discord.Color.red())
            return await ctx.send(embed=e)
        await self.config.user(ctx.author).default_index.set(i)
        e = discord.Embed(title="Default Updated", description=f"Default tag is now **{tag_pretty(tags[i])}**.", color=discord.Color.green())
        await ctx.send(embed=e)

    @tags.command(name="move")
    async def tags_move(self, ctx, index_from: int, index_to: int):
        f = index_from - 1
        t = index_to - 1
        async with self.config.user(ctx.author).all() as u:
            tags: List[str] = u["tags"]
            if not (0 <= f < len(tags)) or not (0 <= t < len(tags)):
                e = discord.Embed(title="Invalid Index", description="Use indices from `[p]tags list`.", color=discord.Color.red())
                return await ctx.send(embed=e)
            item = tags.pop(f)
            tags.insert(t, item)
            # normalize default index
            if u["default_index"] == f:
                u["default_index"] = t
            elif f < u["default_index"] <= t:
                u["default_index"] -= 1
            elif t <= u["default_index"] < f:
                u["default_index"] += 1
        e = discord.Embed(title="Tags Reordered", color=discord.Color.green())
        await ctx.send(embed=e)

    @tags.command(name="remove")
    async def tags_remove(self, ctx, index: int):
        i = index - 1
        async with self.config.user(ctx.author).all() as u:
            tags: List[str] = u["tags"]
            if not (0 <= i < len(tags)):
                e = discord.Embed(title="Invalid Index", description="Use indices from `[p]tags list`.", color=discord.Color.red())
                return await ctx.send(embed=e)
            removed = tags.pop(i)
            if u["default_index"] >= len(tags):
                u["default_index"] = 0
        e = discord.Embed(title="Tag Removed", description=f"Removed **{tag_pretty(removed)}**.", color=discord.Color.orange())
        await ctx.send(embed=e)

    # ---------- User-facing Brawl commands ----------
    @commands.group(name="bs", invoke_without_command=True)
    async def bs(self, ctx):
        """Brawl Stars profile commands."""
        e = discord.Embed(title="Brawl Stars", color=discord.Color.blurple())
        e.description = (
            "`[p]bs verify <tag>` – quick save/validate a tag\n"
            "`[p]bs me` – show your default tag stats\n"
            "`[p]bs player <tag>` – show stats for any tag\n"
            "`[p]bs leaderboard` – server trophies leaderboard"
        )
        await ctx.send(embed=e)

    @bs.command(name="verify")
    async def bs_verify(self, ctx, tag: str):
        api = await self._api(ctx.guild)
        pdata = await api.get_player(tag)
        norm = api.norm_tag(tag)
        async with self.config.user(ctx.author).tags() as tags:
            if norm not in tags:
                if len(tags) >= 3:
                    e = discord.Embed(title="Limit Reached", description="You already have 3 tags saved.", color=discord.Color.red())
                    return await ctx.send(embed=e)
                tags.append(norm)
        await self.config.user(ctx.author).ign_cache.set(pdata.get("name") or "")
        club = pdata.get("club") or {}
        await self.config.user(ctx.author).club_tag_cache.set((club.get("tag") or "").replace("#",""))
        e = discord.Embed(title="Verified", description=f"Saved **{tag_pretty(norm)}** to your profile.", color=discord.Color.green())
        await ctx.send(embed=e)

    @bs.command(name="me")
    async def bs_me(self, ctx):
        u = await self.config.user(ctx.author).all()
        if not u["tags"]:
            e = discord.Embed(title="No Tags", description="Use `[p]tags save <tag>` first.", color=discord.Color.red())
            return await ctx.send(embed=e)
        tag = u["tags"][u["default_index"]]
        await self._send_player_embed(ctx, tag)

    @bs.command(name="player")
    async def bs_player(self, ctx, tag: str):
        api = await self._api(ctx.guild)
        pdata = await api.get_player(tag)
        await self._send_player_embed_from_data(ctx, pdata)

    @bs.command(name="leaderboard")
    async def bs_leaderboard(self, ctx):
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
            e = discord.Embed(title="Leaderboard", description="No verified users yet.", color=discord.Color.blurple())
            return await ctx.send(embed=e)
        rows.sort(reverse=True, key=lambda r: r[0])
        top = rows[:20]
        desc = "\n".join([f"**{i+1}.** {r[1]} — {r[2]} ({r[3]}) • {r[0]:,} 🏆" for i, r in enumerate(top)])
        emb = discord.Embed(title=f"{ctx.guild.name} — Trophies Leaderboard", description=desc, color=discord.Color.gold())
        await ctx.send(embed=emb)

    # ---------- internals ----------
    async def _send_player_embed(self, ctx, tag_norm: str):
        api = await self._api(ctx.guild)
        pdata = await api.get_player(tag_norm)
        await self._send_player_embed_from_data(ctx, pdata)

    async def _send_player_embed_from_data(self, ctx, pdata: Dict[str, Any]):
        name    = pdata.get("name","Unknown")
        tag     = pdata.get("tag","")
        trophies= pdata.get("trophies",0)
        exp     = pdata.get("expLevel",0)
        icon_id = (pdata.get("icon") or {}).get("id",0)
        club    = pdata.get("club") or {}
        club_name = club.get("name", "—")
        club_tag  = club.get("tag", "—")

        e = discord.Embed(title=f"{name} ({tag})", description=f"Club: {club_name} {club_tag}", color=discord.Color.blurple())
        e.add_field(name="Trophies", value=f"{trophies:,}")
        e.add_field(name="EXP Level", value=str(exp))
        e.set_thumbnail(url=player_avatar_url(icon_id))
        await ctx.send(embed=e)

async def setup(bot: Red):
    await bot.add_cog(Players(bot))
