# bsPlayers/playerCommands.py
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
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB51A11, force_registration=True)
        self.config.register_user(tags=[])

    async def _client(self) -> BrawlStarsAPI:
        tok = await get_token()
        if not tok:
            raise commands.UserFeedbackCheckFailure(
                f"No API token set. Admins must run `{self.bot.command_prefix}acheckapi <TOKEN>` "
                f"or a token setter command from your admin cog."
            )
        return BrawlStarsAPI(tok)

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

        await ctx.send(
            f"✅ Verified **{pdata.get('name','?')}** (#{tag}) "
            f"— trophies: **{pdata.get('trophies', 0)}**. Tag saved."
        )

    @tag_group.command(name="remove")
    async def tag_remove(self, ctx: commands.Context, tag: str):
        """Remove a saved tag from your account."""
        tag = normalize_tag(tag)
        async with self.config.user(ctx.author).tags() as tags:
            if tag in tags:
                tags.remove(tag)
                return await ctx.send(f"🗑️ Removed tag #{tag}.")
        await ctx.send("That tag isn’t saved on your account.")

    @tag_group.command(name="list")
    async def tag_list(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        """List saved tags for you or the mentioned user."""
        user = user or ctx.author
        tags: List[str] = await self.config.user(user).tags()
        if not tags:
            return await ctx.send(f"No tags saved for **{user.display_name}**.")
        await ctx.send(f"**{user.display_name}** tags: " + ", ".join(f"#{t}" for t in tags))

    # ---------- !profile ----------

    @commands.command(name="profile")
    @commands.guild_only()
    async def profile(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
        tag: Optional[str] = None,
    ):
        """
        Show a player profile.
        Usage:
          [p]profile
          [p]profile @user
          [p]profile #TAG
          [p]profile @user #TAG
        """
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

        # compute top brawler
        blist = (p.get("brawlers") or [])
        top_b = None
        if blist:
            blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
            top_b = blist[0]

        emb = discord.Embed(
            title=f"{name} (#{use_tag})",
            description=f"**Club:** {club_name}",
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
        """
        Show top brawlers by trophies.
        Usage:
          [p]brawlers
          [p]brawlers @user
          [p]brawlers #TAG
          [p]brawlers @user #TAG
        """
        # allow numeric third arg as limit when member is supplied
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
            lines.append(
                f"• **{b.get('name','?')}** — {b.get('trophies',0)} 🏆 | "
                f"Power {b.get('power','?')} | Rank {b.get('rank','?')}"
            )

        emb = discord.Embed(
            title=f"Top Brawlers — #{use_tag}",
            description="\n".join(lines),
        )
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
        """
        Show recent battles.
        Usage:
          [p]battlelog
          [p]battlelog @user
          [p]battlelog #TAG
          [p]battlelog @user #TAG
        """
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

            # try to show played brawler if present
            brawler_name = None
            if "teams" in btl:
                # 3v3: teams -> list[list[player]]
                me = next(
                    (pl for team in btl.get("teams", []) for pl in team if pl.get("tag", "").lstrip("#").upper() == use_tag),
                    None
                )
                if me:
                    bn = (me.get("brawler") or {}).get("name")
                    if bn:
                        brawler_name = bn
            elif "players" in btl:
                # solo/duo: players -> list[player]
                me = next(
                    (pl for pl in btl.get("players", []) if pl.get("tag", "").lstrip("#").upper() == use_tag),
                    None
                )
                if me:
                    bn = (me.get("brawler") or {}).get("name")
                    if bn:
                        brawler_name = bn

            brawler_str = f" — {brawler_name}" if brawler_name else ""
            lines.append(f"• **{mode}** on *{mapn}*: **{res}**{tch}{brawler_str}")

        emb = discord.Embed(
            title=f"Battlelog — #{use_tag}",
            description="\n".join(lines),
        )
        emb.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=emb)
