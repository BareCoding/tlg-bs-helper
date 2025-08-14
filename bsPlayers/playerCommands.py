# bsPlayers/playerCommands.py
from __future__ import annotations
from typing import List, Optional, Tuple

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

from .api import BrawlStarsAPI, BSAPIError, normalize_tag

# ---------- pretty helpers ----------

EMO = {
    "club": "🏛️",
    "trophy": "🏆",
    "pb": "📈",
    "exp": "🎓",
    "wins3v3": "🛡️",
    "wins_solo": "💀",
    "wins_duo": "🔥",
    "brawler": "🤖",
    "ok": "✅",
    "ko": "❌",
    "draw": "➖",
    "map": "🗺️",
    "mode": "🎮",
}

def zws() -> str:
    """A tiny blank line spacer inside embeds."""
    return "\u200b"

def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default

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

def build_profile_embed(p: dict, use_tag: str, requester: str) -> discord.Embed:
    name = p.get("name", "?")
    trophies = p.get("trophies", 0)
    highest = p.get("highestTrophies", 0)
    exp_lvl = p.get("expLevel", "?")
    club_name = (p.get("club") or {}).get("name", "—")
    solo = p.get("soloVictories", 0)
    duo = p.get("duoVictories", 0)
    wins3 = p.get("3vs3Victories", 0)

    # Top brawler block
    blist = (p.get("brawlers") or [])
    top_line = "—"
    if blist:
        blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
        b0 = blist[0]
        top_line = f"**{b0.get('name','?')}** — {b0.get('trophies',0)} {EMO['trophy']} | Power {b0.get('power','?')} | Rank {b0.get('rank','?')}"

    desc = f"{EMO['club']} **Club:** {club_name}"
    emb = discord.Embed(
        title=f"{name} (#{use_tag})",
        description=desc,
    )

    # Trophies / PB / EXP
    emb.add_field(name=f"{EMO['trophy']} Trophies", value=f"{trophies}", inline=True)
    emb.add_field(name=f"{EMO['pb']} Personal Best", value=f"{highest}", inline=True)
    emb.add_field(name=f"{EMO['exp']} EXP", value=f"{exp_lvl}", inline=True)

    # Spacer
    emb.add_field(name=zws(), value=zws(), inline=False)

    # Wins
    emb.add_field(name=f"{EMO['wins3v3']} 3v3 Wins", value=f"{wins3}", inline=True)
    emb.add_field(name=f"{EMO['wins_solo']} Solo Wins", value=f"{solo}", inline=True)
    emb.add_field(name=f"{EMO['wins_duo']} Duo Wins", value=f"{duo}", inline=True)

    # Spacer
    emb.add_field(name=zws(), value=zws(), inline=False)

    emb.add_field(name=f"{EMO['brawler']} Top Brawler", value=top_line, inline=False)
    emb.set_footer(text=f"Requested by {requester}")
    return emb

def build_brawlers_embed(p: dict, tag: str, limit: int) -> discord.Embed:
    blist = (p.get("brawlers") or [])
    blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
    n = max(1, min(int(limit or 10), 25))
    top = blist[:n]
    if not top:
        return discord.Embed(title=f"Top Brawlers — #{tag}", description="No brawler data available.")

    lines = []
    for b in top:
        lines.append(
            f"• **{b.get('name','?')}** — {b.get('trophies',0)} {EMO['trophy']} | "
            f"Power {b.get('power','?')} | Rank {b.get('rank','?')}"
        )
    return discord.Embed(title=f"Top Brawlers — #{tag}", description="\n".join(lines))

def _result_emoji(result: str) -> str:
    r = (result or "").lower()
    if r == "victory": return EMO["ok"]
    if r == "defeat":  return EMO["ko"]
    return EMO["draw"]

def build_battlelog_embed(data: dict, tag: str, limit: int, requester: str) -> discord.Embed:
    items = data.get("items", [])[: max(1, min(int(limit or 5), 25))]
    if not items:
        return discord.Embed(title=f"Battlelog — #{tag}", description="No recent battles found.")
    lines = []
    for it in items:
        evt = it.get("event") or {}
        mode = (evt.get("mode") or "?").title()
        mapn = evt.get("map", "—")
        btl = it.get("battle") or {}
        res = (btl.get("result") or "—").title()
        tchange = btl.get("trophyChange")
        tch = f" ({tchange:+})" if isinstance(tchange, int) else ""
        # try to find my brawler name
        my_b = None
        if "teams" in btl:
            me = next((pl for team in btl.get("teams", []) for pl in team
                       if pl.get("tag", "").lstrip("#").upper() == tag.upper()), None)
            if me:
                my_b = (me.get("brawler") or {}).get("name")
        elif "players" in btl:
            me = next((pl for pl in btl.get("players", [])
                       if pl.get("tag", "").lstrip("#").upper() == tag.upper()), None)
            if me:
                my_b = (me.get("brawler") or {}).get("name")
        brawler_str = f" — {my_b}" if my_b else ""
        lines.append(
            f"{_result_emoji(res)} **{mode}** • {EMO['map']} *{mapn}* — **{res}**{tch}{brawler_str}"
        )
    emb = discord.Embed(title=f"Battlelog — #{tag}", description="\n".join(lines))
    emb.set_footer(text=f"Requested by {requester}")
    return emb


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
        # Token is pulled from Red's shared API tokens:
        #   [p]set api brawlstars api_key,YOURTOKEN
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
        """Show a player profile (explicit #tag > @user > you)."""
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

        emb = build_profile_embed(p, use_tag, ctx.author.display_name)
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
        """Show top brawlers by trophies (explicit #tag > @user > you)."""
        if isinstance(tag, str) and tag.isdigit() and member is not None:
            limit = int(tag); tag = None

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

        emb = build_brawlers_embed(p, use_tag, limit or 10)
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
        """Show recent battles (explicit #tag > @user > you)."""
        if isinstance(tag, str) and tag.isdigit() and member is not None:
            limit = int(tag); tag = None

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

        emb = build_battlelog_embed(data, use_tag, limit or 5, ctx.author.display_name)
        await ctx.send(embed=emb)
