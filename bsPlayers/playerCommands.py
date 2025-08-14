from __future__ import annotations
from typing import List, Optional, Tuple

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

from .api import BrawlStarsAPI, BSAPIError, normalize_tag

# =========================
# CDN helpers (Brawlify)
# =========================
CDN_BASE = "https://cdn.brawlify.com"

def brawler_icon_url(brawler_id: int) -> str:
    # square icon
    return f"{CDN_BASE}/brawler/{int(brawler_id)}.png"

def brawler_avatar_url(brawler_id: int) -> str:
    # full/portrait avatar
    return f"{CDN_BASE}/brawler/{int(brawler_id)}/avatar.png"

# =========================
# Pretty helpers / emojis
# =========================
EMO = {
    "club": "🏛️",
    "trophy": "🏆",
    "pb": "📈",
    "exp": "🎓",
    "wins3v3": "🛡️",
    "wins_solo": "💀",
    "wins_duo": "🔥",
    "ok": "✅",
    "ko": "❌",
    "draw": "➖",
    "map": "🗺️",
    "mode": "🎮",
    "tag": "🏷️",
}

COLOR_PRIMARY = 0x33CC99
COLOR_WARN = 0xE67E22
COLOR_ERR = 0xE74C3C

def _zws() -> str:
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
    """
    Priority: explicit #tag > mentioned member's first saved tag > author's first saved tag.
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

# =========================
# Embeds builders
# =========================
def build_profile_embed(p: dict, use_tag: string, requester: str) -> discord.Embed:
    name = p.get("name", "?")
    trophies = p.get("trophies", 0)
    highest = p.get("highestTrophies", 0)
    exp_lvl = p.get("expLevel", "?")
    club_name = (p.get("club") or {}).get("name", "—")
    solo = p.get("soloVictories", 0)
    duo = p.get("duoVictories", 0)
    wins3 = p.get("3vs3Victories", 0)

    blist = (p.get("brawlers") or [])
    top_icon = None
    if blist:
        blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
        b0 = blist[0]
        top_icon = brawler_avatar_url(b0.get("id", 0))

    emb = discord.Embed(
        title=f"{name} (#{use_tag})",
        description=f"{EMO['club']} **Club:** {club_name}",
        color=COLOR_PRIMARY,
    )
    if top_icon:
        emb.set_thumbnail(url=top_icon)

    emb.add_field(name=f"{EMO['trophy']} Trophies", value=str(trophies), inline=True)
    emb.add_field(name=f"{EMO['pb']} Personal Best", value=str(highest), inline=True)
    emb.add_field(name=f"{EMO['exp']} EXP", value=str(exp_lvl), inline=True)

    emb.add_field(name=_zws(), value=_zws(), inline=False)
    emb.add_field(name=f"{EMO['wins3v3']} 3v3 Wins", value=str(wins3), inline=True)
    emb.add_field(name=f"{EMO['wins_solo']} Solo Wins", value=str(solo), inline=True)
    emb.add_field(name=f"{EMO['wins_duo']} Duo Wins", value=str(duo), inline=True)

    emb.set_footer(text=f"Requested by {requester}")
    return emb

def build_brawlers_embed(p: dict, use_tag: str, limit: int) -> discord.Embed:
    blist = (p.get("brawlers") or [])
    blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
    n = max(1, min(int(limit or 10), 25))
    top = blist[:n]

    if not top:
        return discord.Embed(
            title=f"Top Brawlers — #{use_tag}",
            description="No brawler data available.",
            color=COLOR_WARN,
        )

    lines = []
    thumb_url = None
    for i, b in enumerate(top, start=1):
        if thumb_url is None:
            thumb_url = brawler_icon_url(b.get("id", 0))
        lines.append(
            f"**{i}. {b.get('name','?')}** — {b.get('trophies',0)} {EMO['trophy']}  ·  "
            f"Power {b.get('power','?')}  ·  Rank {b.get('rank','?')}"
        )

    emb = discord.Embed(
        title=f"Top Brawlers — #{use_tag}",
        description="\n".join(lines),
        color=COLOR_PRIMARY,
    )
    if thumb_url:
        emb.set_thumbnail(url=thumb_url)
    return emb

def _result_emoji(result: str) -> str:
    r = (result or "").lower()
    if r == "victory": return EMO["ok"]
    if r == "defeat":  return EMO["ko"]
    return EMO["draw"]

def build_battlelog_embed(data: dict, use_tag: str, limit: int, requester: str) -> discord.Embed:
    items = data.get("items", [])[: max(1, min(int(limit or 5), 25))]
    if not items:
        return discord.Embed(
            title=f"Battlelog — #{use_tag}",
            description="No recent battles found.",
            color=COLOR_WARN,
        )

    lines = []
    thumb_url = None

    for it in items:
        evt = it.get("event") or {}
        mode = (evt.get("mode") or "?").title()
        mapn = evt.get("map", "—")
        btl = it.get("battle") or {}

        res = (btl.get("result") or "—").title()
        tchange = btl.get("trophyChange")
        tch = f" ({tchange:+})" if isinstance(tchange, int) else ""

        # Try to find *this player's* brawler in the entry
        my_b = None
        if "teams" in btl:
            me = next((pl for team in btl.get("teams", []) for pl in team
                       if pl.get("tag", "").lstrip("#").upper() == use_tag.upper()), None)
            if me:
                my_b = (me.get("brawler") or {})
        elif "players" in btl:
            me = next((pl for pl in btl.get("players", [])
                       if pl.get("tag", "").lstrip("#").upper() == use_tag.upper()), None)
            if me:
                my_b = (me.get("brawler") or {})

        bname = (my_b or {}).get("name")
        if thumb_url is None and my_b and "id" in my_b:
            thumb_url = brawler_icon_url(my_b["id"])

        brawler_str = f" — {bname}" if bname else ""
        lines.append(
            f"{_result_emoji(res)} **{mode}** • {EMO['map']} *{mapn}* — **{res}**{tch}{brawler_str}"
        )

    emb = discord.Embed(
        title=f"Battlelog — #{use_tag}",
        description="\n".join(lines),
        color=COLOR_PRIMARY,
    )
    if thumb_url:
        emb.set_thumbnail(url=thumb_url)
    emb.set_footer(text=f"Requested by {requester}")
    return emb

# =========================
# Cog
# =========================
class PlayerCommands(commands.Cog):
    """Player commands for Brawl Stars: tag management, profile, brawlers, battlelog."""

    __author__ = "Pat"
    __version__ = "1.2.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB51A11, force_registration=True)
        self.config.register_user(tags=[])

    async def _client(self) -> BrawlStarsAPI:
        # Token pulled from Red's shared API tokens:
        #   [p]set api brawlstars api_key,YOURTOKEN
        return BrawlStarsAPI(self.bot)

    # ------------- tag group -------------

    @commands.group(name="tag", invoke_without_command=True)
    @commands.guild_only()
    async def tag_group(self, ctx: commands.Context):
        """Manage your saved Brawl Stars tags."""
        emb = discord.Embed(
            title="Tag commands",
            description=(
                f"`{ctx.clean_prefix}tag verify #TAG` — Save a verified tag\n"
                f"`{ctx.clean_prefix}tag remove #TAG` — Remove a saved tag\n"
                f"`{ctx.clean_prefix}tag list [@user]` — List saved tags"
            ),
            color=COLOR_PRIMARY,
        )
        emb.set_footer(text="Use #TAG or just the code; O is auto-corrected to 0.")
        await ctx.send(embed=emb)

    @tag_group.command(name="verify")
    async def tag_verify(self, ctx: commands.Context, tag: str):
        """Verify a tag against the API and save it to your account."""
        tag = normalize_tag(tag)
        client = await self._client()
        try:
            pdata = await client.get_player(tag)  # validates
        except BSAPIError as e:
            emb = discord.Embed(
                title="Tag verification failed",
                description=str(e),
                color=COLOR_ERR,
            )
            return await ctx.send(embed=emb)
        finally:
            await client.close()

        async with self.config.user(ctx.author).tags() as tags:
            if tag not in tags:
                tags.append(tag)

        emb = discord.Embed(
            title="Tag verified",
            description=(
                f"{EMO['tag']} **#{tag}** saved for **{ctx.author.display_name}**\n"
                f"{EMO['trophy']} Trophies: **{pdata.get('trophies', 0)}**"
            ),
            color=COLOR_PRIMARY,
        )
        await ctx.send(embed=emb)

    @tag_group.command(name="remove")
    async def tag_remove(self, ctx: commands.Context, tag: str):
        """Remove a saved tag from your account."""
        tag = normalize_tag(tag)
        removed = False
        async with self.config.user(ctx.author).tags() as tags:
            if tag in tags:
                tags.remove(tag)
                removed = True

        emb = discord.Embed(
            title="Tag removed" if removed else "Not found",
            description=(f"Removed **#{tag}**." if removed else "That tag is not saved on your account."),
            color=(COLOR_PRIMARY if removed else COLOR_WARN),
        )
        await ctx.send(embed=emb)

    @tag_group.command(name="list")
    async def tag_list(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        """List saved tags for you or the mentioned user."""
        user = user or ctx.author
        tags: List[str] = await self.config.user(user).tags()
        if not tags:
            emb = discord.Embed(
                title="No tags",
                description=f"No tags saved for **{user.display_name}**.",
                color=COLOR_WARN,
            )
            return await ctx.send(embed=emb)

        emb = discord.Embed(
            title=f"{user.display_name}'s tags",
            description="\n".join(f"• `#{t}`" for t in tags),
            color=COLOR_PRIMARY,
        )
        await ctx.send(embed=emb)

    # ------------- profile -------------

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
            emb = discord.Embed(title="Missing tag", description=str(e), color=COLOR_WARN)
            return await ctx.send(embed=emb)

        client = await self._client()
        try:
            p = await client.get_player(use_tag)
        except BSAPIError as e:
            emb = discord.Embed(title="API error", description=str(e), color=COLOR_ERR)
            return await ctx.send(embed=emb)
        finally:
            await client.close()

        emb = build_profile_embed(p, use_tag, ctx.author.display_name)
        await ctx.send(embed=emb)

    # ------------- brawlers -------------

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
            emb = discord.Embed(title="Missing tag", description=str(e), color=COLOR_WARN)
            return await ctx.send(embed=emb)

        client = await self._client()
        try:
            p = await client.get_player(use_tag)
        except BSAPIError as e:
            emb = discord.Embed(title="API error", description=str(e), color=COLOR_ERR)
            return await ctx.send(embed=emb)
        finally:
            await client.close()

        emb = build_brawlers_embed(p, use_tag, limit or 10)
        await ctx.send(embed=emb)

    # ------------- battlelog -------------

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
            emb = discord.Embed(title="Missing tag", description=str(e), color=COLOR_WARN)
            return await ctx.send(embed=emb)

        client = await self._client()
        try:
            data = await client.get_player_battlelog(use_tag)
        except BSAPIError as e:
            emb = discord.Embed(title="API error", description=str(e), color=COLOR_ERR)
            return await ctx.send(embed=emb)
        finally:
            await client.close()

        emb = build_battlelog_embed(data, use_tag, limit or 5, ctx.author.display_name)
        await ctx.send(embed=emb)
