from __future__ import annotations
from typing import List, Optional, Tuple
import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

from .api import BrawlStarsAPI, BSAPIError, normalize_tag

CDN = "https://cdn.brawlify.com"


# ---------- CDN Helpers ----------

def brawler_image_url(brawler_id: int, borderless: bool = True) -> str:
    style = "borderless" if borderless else "borders"
    return f"{CDN}/brawlers/{style}/{brawler_id}.png"

def profile_icon_url(icon_id: int) -> str:
    return f"{CDN}/profile-icons/{icon_id}.png"

# ---------- Utilities ----------

async def _resolve_tag(
    ctx: commands.Context,
    explicit_tag: Optional[str],
    member: Optional[discord.Member],
    user_conf,
) -> Tuple[str, discord.Member]:
    if explicit_tag:
        return normalize_tag(explicit_tag), (member or ctx.author)

    target = member or ctx.author
    tags: List[str] = await user_conf(target).tags()
    if not tags:
        raise commands.UserFeedbackCheckFailure(
            f"No saved tags for **{target.display_name}**.\n"
            f"Use `{ctx.clean_prefix}tag verify #YOURTAG` or provide a #tag."
        )
    return tags[0], target


# ---------- Cog ----------

class PlayerCommands(commands.Cog):
    """Player commands for Brawl Stars: tag management, profile, brawlers, battlelog."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB51A11, force_registration=True)
        self.config.register_user(tags=[])

    async def _client(self) -> BrawlStarsAPI:
        return BrawlStarsAPI(self.bot)

    # ---------- Tag Commands ----------

    @commands.group(name="tag", invoke_without_command=True)
    async def tag_group(self, ctx: commands.Context):
        """Manage your saved Brawl Stars tags."""
        await ctx.send_help()

    @tag_group.command(name="verify")
    async def tag_verify(self, ctx: commands.Context, tag: str):
        tag = normalize_tag(tag)
        client = await self._client()
        try:
            pdata = await client.get_player(tag)
        except BSAPIError as e:
            return await ctx.send(f"❌ Invalid tag or API error: {e}")
        finally:
            await client.close()

        async with self.config.user(ctx.author).tags() as tags:
            if tag not in tags:
                tags.append(tag)

        await ctx.send(
            embed=discord.Embed(
                title="✅ Tag Verified",
                description=(
                    f"**{pdata.get('name','?')}** (#{tag})\n"
                    f"Trophies: **{pdata.get('trophies', 0)}**"
                ),
                color=discord.Color.green()
            ).set_footer(text=f"Saved for {ctx.author.display_name}")
        )

    @tag_group.command(name="remove")
    async def tag_remove(self, ctx: commands.Context, tag: str):
        tag = normalize_tag(tag)
        async with self.config.user(ctx.author).tags() as tags:
            if tag in tags:
                tags.remove(tag)
                return await ctx.send(embed=discord.Embed(
                    description=f"🗑️ Removed tag `#{tag}`.",
                    color=discord.Color.orange()
                ))
        await ctx.send("That tag isn’t saved on your account.")

    @tag_group.command(name="list")
    async def tag_list(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        user = user or ctx.author
        tags = await self.config.user(user).tags()
        if not tags:
            return await ctx.send(f"No tags saved for **{user.display_name}**.")
        await ctx.send(
            embed=discord.Embed(
                title=f"{user.display_name}'s Tags",
                description="\n".join(f"• `#{t}`" for t in tags),
                color=discord.Color.blue()
            )
        )

    # ---------- Profile Command ----------

    @commands.command(name="profile")
    async def profile(self, ctx, member: Optional[discord.Member] = None, tag: Optional[str] = None):
        try:
            use_tag, who = await _resolve_tag(ctx, tag, member, self.config.user)
        except commands.UserFeedbackCheckFailure as e:
            return await ctx.send(str(e))

        client = await self._client()
        try:
            p = await client.get_player(use_tag)
        except BSAPIError as e:
            return await ctx.send(f"API error: {e}")
        finally:
            await client.close()

        icon_url = profile_icon_url(p.get("icon", {}).get("id", 28000000))
        name = p.get("name", "?")
        emb = discord.Embed(
            title=f"{name} (#{use_tag})",
            description=f"🏅 **Club:** {p.get('club', {}).get('name', '—')}",
            color=discord.Color.gold()
        )
        emb.add_field(name="Trophies", value=p.get("trophies", 0))
        emb.add_field(name="PB", value=p.get("highestTrophies", 0))
        emb.add_field(name="EXP Level", value=p.get("expLevel", "?"))
        emb.add_field(name="3v3 Wins", value=p.get("3vs3Victories", 0))
        emb.add_field(name="Solo Wins", value=p.get("soloVictories", 0))
        emb.add_field(name="Duo Wins", value=p.get("duoVictories", 0))

        top_b = sorted(p.get("brawlers", []), key=lambda b: b.get("trophies", 0), reverse=True)[0]
        emb.add_field(
            name="Top Brawler",
            value=f"**{top_b['name']}** — {top_b['trophies']} 🏆\n"
                  f"Power {top_b['power']} | Rank {top_b['rank']}",
            inline=False
        )
        emb.set_thumbnail(url=brawler_image_url(top_b["id"]))
        emb.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=emb)

    # ---------- Brawlers Command ----------

    @commands.command(name="brawlers")
    async def brawlers(self, ctx, member: Optional[discord.Member] = None, tag: Optional[str] = None):
        try:
            use_tag, who = await _resolve_tag(ctx, tag, member, self.config.user)
        except commands.UserFeedbackCheckFailure as e:
            return await ctx.send(str(e))

        client = await self._client()
        try:
            p = await client.get_player(use_tag)
        except BSAPIError as e:
            return await ctx.send(f"API error: {e}")
        finally:
            await client.close()

        blist = sorted(p.get("brawlers", []), key=lambda b: b.get("trophies", 0), reverse=True)
        if not blist:
            return await ctx.send("No brawlers found.")

        desc = "\n".join(
            f"**{b['name']}** — {b['trophies']} 🏆 | Power {b['power']} | Rank {b['rank']}"
            for b in blist[:10]
        )

        emb = discord.Embed(
            title=f"{p.get('name')}’s Top Brawlers",
            description=desc,
            color=discord.Color.purple()
        )
        emb.set_footer(text=f"#{use_tag}")
        emb.set_thumbnail(url=brawler_image_url(blist[0]["id"]))
        await ctx.send(embed=emb)

    # ---------- Battlelog Command ----------

    @commands.command(name="battlelog")
    async def battlelog(self, ctx, member: Optional[discord.Member] = None, tag: Optional[str] = None):
        try:
            use_tag, who = await _resolve_tag(ctx, tag, member, self.config.user)
        except commands.UserFeedbackCheckFailure as e:
            return await ctx.send(str(e))

        client = await self._client()
        try:
            log = await client.get_player_battlelog(use_tag)
        except BSAPIError as e:
            return await ctx.send(f"API error: {e}")
        finally:
            await client.close()

        battles = log.get("items", [])[:5]
        if not battles:
            return await ctx.send("No recent battles.")

        lines = []
        for b in battles:
            battle = b.get("battle", {})
            result = battle.get("result", "N/A")
            mode = (b.get("event") or {}).get("mode", "Unknown").title()
            mapname = (b.get("event") or {}).get("map", "Unknown")
            trophy_change = battle.get("trophyChange", 0)
            lines.append(
                f"**{mode}** on *{mapname}* — {result.title()} ({trophy_change:+})"
            )

        emb = discord.Embed(
            title=f"Recent Battles — #{use_tag}",
            description="\n".join(lines),
            color=discord.Color.blurple()
        )
        emb.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=emb)
