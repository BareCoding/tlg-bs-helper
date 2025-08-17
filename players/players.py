# players/players.py
from redbot.core import commands, Config
from redbot.core.bot import Red
import discord
from typing import List, Dict, Any
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import player_avatar_url

class Players(commands.Cog):
    """Player verification, tags, stats, and leaderboards."""

    __author__ = "Threat Level Gaming"
    __version__ = "0.2.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xBEEFBEEF, force_registration=True)
        default_user = {"tags": [], "default_index": 0, "ign_cache": "", "club_tag_cache": ""}
        default_guild = {"stats": {}}  # optional global snapshots by tag if you want to expand
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

    # ---- User commands (prefixed "bs") ----
    @commands.group(name="bs", invoke_without_command=True)
    async def bs(self, ctx):
        """Brawl Stars profile commands."""
        await ctx.send_help()

    @bs.command(name="verify")
    async def bs_verify(self, ctx, tag: str):
        """Verify and save a tag (adds to your list, max 3)."""
        api = await self._api(ctx.guild)
        pdata = await api.get_player(tag)
        norm = api.norm_tag(tag)
        async with self.config.user(ctx.author).tags() as tags:
            if norm not in tags:
                if len(tags) >= 3:
                    return await ctx.send("You already have 3 tags saved. Remove one first with `[p]bs deltag <index>`.")
                tags.append(norm)
        await self.config.user(ctx.author).ign_cache.set(pdata.get("name") or "")
        club = pdata.get("club") or {}
        await self.config.user(ctx.author).club_tag_cache.set((club.get("tag") or "").replace("#",""))
        await ctx.tick()

    @bs.command(name="addtag")
    async def bs_addtag(self, ctx, tag: str):
        """Add a tag (max 3)."""
        api = await self._api(ctx.guild)
        _ = await api.get_player(tag)  # validate
        norm = api.norm_tag(tag)
        async with self.config.user(ctx.author).tags() as tags:
            if norm in tags:
                return await ctx.send("That tag is already saved.")
            if len(tags) >= 3:
                return await ctx.send("You already have 3 tags saved.")
            tags.append(norm)
        await ctx.tick()

    @bs.command(name="deltag")
    async def bs_deltag(self, ctx, index: int):
        """Remove a tag by index (1..3)."""
        i = index - 1
        async with self.config.user(ctx.author).all() as u:
            tags: List[str] = u["tags"]
            if not (0 <= i < len(tags)):
                return await ctx.send("Invalid index.")
            tags.pop(i)
            if u["default_index"] >= len(tags):
                u["default_index"] = 0
        await ctx.tick()

    @bs.command(name="setdefault")
    async def bs_setdefault(self, ctx, index: int):
        """Set your default tag index (1..3)."""
        i = index - 1
        tags = await self.config.user(ctx.author).tags()
        if not (0 <= i < len(tags)):
            return await ctx.send("Invalid index.")
        await self.config.user(ctx.author).default_index.set(i)
        await ctx.tick()

    @bs.command(name="mytags")
    async def bs_mytags(self, ctx):
        """Show your tags."""
        u = await self.config.user(ctx.author).all()
        tags = u["tags"]
        if not tags:
            return await ctx.send("No tags saved. Use `[p]bs verify <tag>`.")
        msg = []
        for i, t in enumerate(tags, start=1):
            star = " **(default)**" if (i - 1) == u["default_index"] else ""
            msg.append(f"{i}. #{t}{star}")
        await ctx.send("\n".join(msg))

    @bs.command(name="me")
    async def bs_me(self, ctx):
        """Show stats for your default tag."""
        u = await self.config.user(ctx.author).all()
        if not u["tags"]:
            return await ctx.send("No tags saved. Use `[p]bs verify <tag>` first.")
        tag = u["tags"][u["default_index"]]
        await self._send_player_embed(ctx, tag)

    @bs.command(name="player")
    async def bs_player(self, ctx, tag: str):
        """Show stats for a specific tag."""
        api = await self._api(ctx.guild)
        pdata = await api.get_player(tag)
        await self._send_player_embed_from_data(ctx, pdata)

    @bs.command(name="leaderboard")
    async def bs_leaderboard(self, ctx):
        """Server leaderboard among users with saved tags (by trophies)."""
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
            return await ctx.send("No verified users yet.")
        rows.sort(reverse=True, key=lambda r: r[0])
        top = rows[:20]
        desc = "\n".join([f"**{i+1}.** {r[1]} — {r[2]} ({r[3]}) • {r[0]:,} 🏆" for i, r in enumerate(top)])
        emb = discord.Embed(title=f"{ctx.guild.name} — Trophies Leaderboard", description=desc, color=discord.Color.gold())
        await ctx.send(embed=emb)

    async def _send_player_embed(self, ctx, tag_norm: str):
        api = await self._api(ctx.guild)
        pdata = await api.get_player(tag_norm)
        await self._send_player_embed_from_data(ctx, pdata)

    async def _send_player_embed_from_data(self, ctx, pdata: Dict[str, Any]):
        name = pdata.get("name","Unknown")
        tag = pdata.get("tag","")
        trophies = pdata.get("trophies",0)
        exp = pdata.get("expLevel",0)
        icon_id = (pdata.get("icon") or {}).get("id",0)
        club = pdata.get("club") or {}
        club_name = club.get("name", "—")
        club_tag = club.get("tag", "—")

        e = discord.Embed(title=f"{name} ({tag})", description=f"Club: {club_name} {club_tag}", color=discord.Color.blurple())
        e.add_field(name="Trophies", value=f"{trophies:,}")
        e.add_field(name="EXP Level", value=str(exp))
        e.set_thumbnail(url=player_avatar_url(icon_id))
        await ctx.send(embed=e)

async def setup(bot: Red):
    await bot.add_cog(Players(bot))
