from __future__ import annotations
import discord
from redbot.core import commands, Config
from typing import Optional, List, Tuple
from .api import BrawlStarsAPI, BSAPIError, normalize_tag

CDN_BASE = "https://cdn.brawlify.com"

def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default

def brawler_icon_url(brawler: dict) -> str:
    """Builds a brawler icon URL using the brawler ID from the API."""
    brawler_id = brawler.get("id")
    if brawler_id:
        return f"{CDN_BASE}/brawlers/{brawler_id}.png"
    return f"{CDN_BASE}/brawlers/16000000.png"

class PlayerCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB51A11)
        self.config.register_user(tags=[])

    async def _client(self):
        keys = await self.bot.get_shared_api_tokens("brawlstars")
        if not keys or "api_key" not in keys:
            raise commands.UserFeedbackCheckFailure("API key not set. Use `[p]set api brawlstars api_key,YOURTOKEN`.")
        return BrawlStarsAPI(self.bot)

    async def _resolve_tag(self, ctx, tag: Optional[str], member: Optional[discord.Member]) -> Tuple[str, discord.Member]:
        if tag:
            return normalize_tag(tag), member or ctx.author
        target = member or ctx.author
        tags = await self.config.user(target).tags()
        if not tags:
            raise commands.UserFeedbackCheckFailure(f"No saved tags for {target.display_name}. Use `{ctx.clean_prefix}tag verify #YOURTAG`.")
        return tags[0], target

    @commands.group(name="tag", invoke_without_command=True)
    async def tag_group(self, ctx: commands.Context):
        await ctx.send_help()

    @tag_group.command(name="verify")
    async def tag_verify(self, ctx: commands.Context, tag: str):
        tag = normalize_tag(tag)
        client = await self._client()
        try:
            player = await client.get_player(tag)
        except BSAPIError as e:
            return await ctx.send(f"❌ Error: {e}")
        async with self.config.user(ctx.author).tags() as tags:
            if tag not in tags:
                tags.append(tag)
        await ctx.send(f"✅ Verified **{player.get('name')}** (#{tag}) and saved.")

    @tag_group.command(name="remove")
    async def tag_remove(self, ctx: commands.Context, tag: str):
        tag = normalize_tag(tag)
        async with self.config.user(ctx.author).tags() as tags:
            if tag in tags:
                tags.remove(tag)
                return await ctx.send(f"🗑️ Removed tag #{tag}.")
        await ctx.send("That tag isn’t saved.")

    @tag_group.command(name="list")
    async def tag_list(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        user = user or ctx.author
        tags = await self.config.user(user).tags()
        if not tags:
            return await ctx.send(f"No tags saved for {user.display_name}.")
        await ctx.send(f"**{user.display_name}'s Tags:**\n" + ", ".join(f"#{t}" for t in tags))

    @commands.command()
    async def profile(self, ctx, member: Optional[discord.Member] = None, tag: Optional[str] = None):
        try:
            use_tag, who = await self._resolve_tag(ctx, tag, member)
            client = await self._client()
            player = await client.get_player(use_tag)
        except Exception as e:
            return await ctx.send(f"⚠️ {e}")

        brawlers = player.get("brawlers", [])
        brawlers.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
        top_brawler = brawlers[0] if brawlers else None

        embed = discord.Embed(
            title=f"{player.get('name')} (#{use_tag})",
            description=f"🏅 Club: {player.get('club', {}).get('name', '—')}",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Trophies", value=player.get("trophies", "—"))
        embed.add_field(name="PB", value=player.get("highestTrophies", "—"))
        embed.add_field(name="EXP Level", value=player.get("expLevel", "—"))
        embed.add_field(name="3v3 Wins", value=player.get("3vs3Victories", 0))
        embed.add_field(name="Solo Wins", value=player.get("soloVictories", 0))
        embed.add_field(name="Duo Wins", value=player.get("duoVictories", 0))

        if top_brawler:
            embed.add_field(
                name="Top Brawler",
                value=f"**{top_brawler['name']}** — {top_brawler['trophies']} 🏆\n"
                      f"Power {top_brawler.get('power', '?')} | Rank {top_brawler.get('rank', '?')}",
                inline=False,
            )
            embed.set_thumbnail(url=brawler_icon_url(top_brawler))

        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @commands.command()
    async def brawlers(self, ctx, member: Optional[discord.Member] = None, tag: Optional[str] = None, limit: Optional[int] = 10):
        try:
            use_tag, who = await self._resolve_tag(ctx, tag, member)
            client = await self._client()
            player = await client.get_player(use_tag)
        except Exception as e:
            return await ctx.send(f"⚠️ {e}")

        brawlers = player.get("brawlers", [])
        brawlers.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
        top_brawlers = brawlers[:max(1, min(limit or 10, 25))]

        embed = discord.Embed(
            title=f"{player.get('name')}'s Top Brawlers",
            description="\n".join([
                f"**{b['name']}** — {b['trophies']} 🏆 | Power {b['power']} | Rank {b['rank']}"
                for b in top_brawlers
            ]),
            color=discord.Color.purple(),
        )

        if top_brawlers:
            embed.set_thumbnail(url=brawler_icon_url(top_brawlers[0]))

        embed.set_footer(text=f"#{use_tag}")
        await ctx.send(embed=embed)

    @commands.command()
    async def battlelog(self, ctx, member: Optional[discord.Member] = None, tag: Optional[str] = None, limit: Optional[int] = 5):
        try:
            use_tag, who = await self._resolve_tag(ctx, tag, member)
            client = await self._client()
            log = await client.get_player_battlelog(use_tag)
        except Exception as e:
            return await ctx.send(f"⚠️ {e}")

        entries = log.get("items", [])[:max(1, min(limit or 5, 25))]
        if not entries:
            return await ctx.send("No recent battles found.")

        lines = []
        for match in entries:
            evt = match.get("event", {})
            btl = match.get("battle", {})
            mode = evt.get("mode", "?").title()
            result = btl.get("result", "?").title()
            trophy_change = btl.get("trophyChange")
            brawler_name = next(
                (
                    (pl.get("brawler") or {}).get("name")
                    for team in btl.get("teams", [])
                    for pl in team
                    if normalize_tag(pl.get("tag", "")) == use_tag
                ),
                "Unknown"
            )
            tch = f" ({trophy_change:+})" if isinstance(trophy_change, int) else ""
            lines.append(f"• **{mode}** on *{evt.get('map', '—')}* — {result}{tch} as {brawler_name}")

        embed = discord.Embed(
            title=f"Battlelog — #{use_tag}",
            description="\n".join(lines),
            color=discord.Color.dark_green(),
        )
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)
