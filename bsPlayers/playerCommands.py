from __future__ import annotations
import json
from typing import List, Optional, Tuple

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

from .api import (
    BrawlStarsAPI,
    BSAPIError,
    normalize_tag,
)

# ---------- helpers ----------

async def _resolve_tag(
    ctx: commands.Context,
    explicit_tag: Optional[str],
    member: Optional[discord.Member],
    user_conf,
) -> Tuple[str, discord.Member]:
    """
    Priority: explicit tag > mentioned member's first tag > author's first tag.
    Raises UserFeedbackCheckFailure if no tag can be resolved.
    """
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


def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


# ---------- cog ----------

class PlayerCommands(commands.Cog):
    """Player commands for Brawl Stars: tag management, profile, brawlers, battlelog."""

    __author__ = "Pat"
    __version__ = "1.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB51A11, force_registration=True)
        self.config.register_user(tags=[])

    async def _client(self) -> BrawlStarsAPI:
        return BrawlStarsAPI(self.bot)

    # ---------- !tag group ----------

    @commands.group(name="tag", invoke_without_command=True)
    @commands.guild_only()
    async def tag_group(self, ctx: commands.Context):
        """Manage your saved Brawl Stars tags."""
        await ctx.send_help()

    @tag_group.command(name="verify")
    async def tag_verify(self, ctx: commands.Context, tag: str):
        """Verify a tag against the API and save it to your account."""
        tag = normalize_tag(tag)
        client = await self._client()
        try:
            pdata = await client.get_player(tag)  # validates tag
        except BSAPIError as e:
            return await ctx.send(f"❌ Invalid tag or API error: {e}")
        finally:
            await client.close()

        async with self.config.user(ctx.author).tags() as tags:
            if tag not in tags:
                tags.append(tag)

        emb = discord.Embed(
            title=f"✅ Verified {pdata.get('name','?')} (#{tag})",
            description=f"Trophies: **{pdata.get('trophies', 0)}**",
            color=discord.Color.green(),
        )
        await ctx.send(embed=emb)

    @tag_group.command(name="remove")
    async def tag_remove(self, ctx: commands.Context, tag: str):
        """Remove a saved tag from your account."""
        tag = normalize_tag(tag)
        async with self.config.user(ctx.author).tags() as tags:
            if tag in tags:
                tags.remove(tag)
                emb = discord.Embed(
                    title="🗑️ Tag Removed",
                    description=f"Removed tag #{tag}.",
                    color=discord.Color.red(),
                )
                return await ctx.send(embed=emb)
        await ctx.send("That tag isn’t saved on your account.")

    @tag_group.command(name="list")
    async def tag_list(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        """List saved tags for you or the mentioned user."""
        user = user or ctx.author
        tags: List[str] = await self.config.user(user).tags()
        if not tags:
            return await ctx.send(f"No tags saved for **{user.display_name}**.")
        emb = discord.Embed(
            title=f"Saved Tags for {user.display_name}",
            description=", ".join(f"#{t}" for t in tags),
            color=discord.Color.blue(),
        )
        await ctx.send(embed=emb)

    # ---------- !profile ----------

    @commands.command(name="profile")
    @commands.guild_only()
    async def profile(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
        tag: Optional[str] = None,
    ):
        """Show a player profile."""
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

        name = p.get("name", "?")
        trophies = p.get("trophies", 0)
        highest = p.get("highestTrophies", 0)
        exp_lvl = p.get("expLevel", "?")
        club_name = (p.get("club") or {}).get("name", "—")
        solo = p.get("soloVictories", 0)
        duo = p.get("duoVictories", 0)
        tvv = p.get("3vs3Victories", 0)

        top_b = None
        blist = (p.get("brawlers") or [])
        if blist:
            blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
            top_b = blist[0]

        emb = discord.Embed(
            title=f"{name} (#{use_tag})",
            description=f"**Club:** {club_name}",
            color=discord.Color.gold(),
        )
        emb.add_field(name="Trophies", value=f"{trophies}", inline=True)
        emb.add_field(name="PB", value=f"{highest}", inline=True)
        emb.add_field(name="EXP", value=f"{exp_lvl}", inline=True)
        emb.add_field(name="3v3 Wins", value=f"{tvv}", inline=True)
        emb.add_field(name="Solo Wins", value=f"{solo}", inline=True)
        emb.add_field(name="Duo Wins", value=f"{duo}", inline=True)
        if top_b:
            emb.add_field(
                name="Top Brawler",
                value=f"{top_b.get('name','?')} — {top_b.get('trophies',0)} 🏆 | "
                      f"Power {top_b.get('power','?')} | Rank {top_b.get('rank','?')}",
                inline=False,
            )
            emb.set_thumbnail(
                url=f"https://cdn.brawlify.com/brawler/{top_b.get('id',0)}.png"
            )
        emb.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=emb)

    # ---------- !brawlers ----------

    @commands.command(name="brawlers")
    @commands.guild_only()
    async def brawlers(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
        tag: Optional[str] = None,
        limit: Optional[int] = 10,
    ):
        """Show top brawlers by trophies."""
        if isinstance(tag, str) and tag.isdigit() and member is not None:
            limit = int(tag)
            tag = None

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

        blist = (p.get("brawlers") or [])
        if not blist:
            return await ctx.send("No brawler data available.")
        blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)

        n = max(1, min(int(limit or 10), 25))
        top = blist[:n]

        lines = []
        for b in top:
            bid = b.get("id", 0)
            lines.append(
                f"**{b.get('name','?')}** — {b.get('trophies',0)} 🏆 | "
                f"Power {b.get('power','?')} | Rank {b.get('rank','?')}\n"
                f"[​]({f'https://cdn.brawlify.com/brawler/{bid}.png'})"
            )

        emb = discord.Embed(
            title=f"Top Brawlers — #{use_tag}",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        emb.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=emb)

    # ---------- !battlelog ----------

    @commands.command(name="battlelog")
    @commands.guild_only()
    async def battlelog(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
        tag: Optional[str] = None,
        limit: Optional[int] = 5,
    ):
        """Show recent battles."""
        if isinstance(tag, str) and tag.isdigit() and member is not None:
            limit = int(tag)
            tag = None

        try:
            use_tag, who = await _resolve_tag(ctx, tag, member, self.config.user)
        except commands.UserFeedbackCheckFailure as e:
            return await ctx.send(str(e))

        client = await self._client()
        try:
            data = await client.get_player_battlelog(use_tag)
        except BSAPIError as e:
            return await ctx.send(f"API error: {e}")
        finally:
            await client.close()

        items = data.get("items", [])[: max(1, min(int(limit or 5), 25))]
        if not items:
            return await ctx.send("No recent battles found.")

        lines = []
        for it in items:
            evt = it.get("event") or {}
            mode = (evt.get("mode") or "?").title()
            mapn = evt.get("map", "—")
            btl = it.get("battle") or {}

            res = (btl.get("result") or "—").title()
            tchange = btl.get("trophyChange")
            tch = f" ({tchange:+})" if isinstance(tchange, int) else ""

            lines.append(f"• **{mode}** on *{mapn}*: **{res}**{tch}")

        emb = discord.Embed(
            title=f"Battlelog — #{use_tag}",
            description="\n".join(lines),
            color=discord.Color.purple(),
        )
        emb.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=emb)
