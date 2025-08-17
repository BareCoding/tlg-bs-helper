# bsinfo/bsinfo.py
# ---- TLGBS bootstrap: make sibling "brawlcommon" importable on cold start ----
import sys, pathlib
_COGS_DIR = pathlib.Path(__file__).resolve().parents[1]  # .../cogs
if str(_COGS_DIR) not in sys.path:
    sys.path.insert(0, str(_COGS_DIR))
# ------------------------------------------------------------------------------

from redbot.core import commands, Config
from redbot.core.bot import Red
import discord
from typing import List, Dict, Any, Optional

from discord.ui import View, button, Button

from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import (
    tag_pretty,
    player_avatar_url,
    club_badge_url,
    brawler_icon_url,
    mode_icon_url,
    map_image_url,
    find_brawler_id_by_name,
)

ACCENT  = discord.Color.from_rgb(66, 135, 245)
SUCCESS = discord.Color.green()
WARN    = discord.Color.orange()
ERROR   = discord.Color.red()
GOLD    = discord.Color.gold()


# ---- Helper: find a cog by name (case-insensitive)
def _find_cog(bot: Red, name: str):
    want = (name or "").lower()
    for cog in bot.cogs.values():
        if getattr(cog, "__cog_name__", "").lower() == want:
            return cog
    return None


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


# ---------- Simple pick menu for fallback flow ----------
class _PickButton(discord.ui.Button):
    def __init__(self, idx: int, label: str):
        super().__init__(style=discord.ButtonStyle.primary, label=f"{idx}. {label}")
        self.idx = idx

    async def callback(self, interaction: discord.Interaction):
        view: "_PickView" = self.view  # type: ignore
        if 1 <= self.idx <= len(view.options):
            view.selected = view.options[self.idx - 1]
            await interaction.response.defer()
            view.stop()


class _PickView(discord.ui.View):
    def __init__(self, author_id: int, options, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.options = options[:5]
        self.selected = None
        for i, (ctag, cfg) in enumerate(self.options, start=1):
            self.add_item(_PickButton(i, cfg["name"]))
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def _cancel(interaction: discord.Interaction):
            self.selected = None
            await interaction.response.defer()
            self.stop()
        cancel.callback = _cancel  # type: ignore
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id


class BSInfo(commands.Cog):
    """
    Deep Brawl Stars lookups (players, clubs, brawlers, rankings, events)
    + per-user tag storage (max 3) used as defaults for lookups.
    Also exposes `!bs start` which DMs the application flow via Onboarding
    or a built-in fallback if Onboarding isn't loaded.
    """

    __version__ = "0.8.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB51F0C, force_registration=True)
        default_user = {"tags": [], "default_index": 0, "ign_cache": "", "club_tag_cache": ""}
        self.config.register_user(**default_user)
        self._apis: Dict[int, BrawlStarsAPI] = {}

    async def cog_unload(self):
        for api in self._apis.values():
            await api.close()

    # ---------- API client ----------
    async def _api(self, guild: discord.Guild) -> BrawlStarsAPI:
        token = await get_brawl_api_token(self.bot)  # raises nicely if not set
        cli = self._apis.get(guild.id)
        if not cli:
            cli = BrawlStarsAPI(token)
            self._apis[guild.id] = cli
        return cli

    # ---------- Small helpers ----------
    async def _get_default_tag(self, user: discord.User) -> Optional[str]:
        u = await self.config.user(user).all()
        if not u["tags"]:
            return None
        i = max(0, min(u["default_index"], len(u["tags"]) - 1))
        return u["tags"][i]

    async def _cache_player_bits(self, user: discord.User, pdata: Dict[str, Any]):
        await self.config.user(user).ign_cache.set(pdata.get("name") or "")
        club = pdata.get("club") or {}
        await self.config.user(user).club_tag_cache.set((club.get("tag") or "").replace("#", ""))

    # ---------- Minimal fallback application in DMs ----------
    async def _fallback_application_dm(self, guild: discord.Guild, member: discord.Member):
        """
        Minimal DM application if Onboarding cog isn't loaded.
        - asks for/uses default tag
        - validates via API
        - fetches LIVE club info for tracked clubs (via Clubs cog)
        - lets user pick; then notifies the club's log channel and/or leadership role
        """
        try:
            dm = await member.create_dm()
        except discord.Forbidden:
            return

        api = await self._api(guild)

        # 1) get a tag (use default if present)
        use_tag = await self._get_default_tag(member)
        if not use_tag:
            await dm.send(embed=discord.Embed(
                title="Your Tag",
                description="Reply with your player tag (e.g. `#ABCD123`).",
                color=ACCENT
            ))
            def _check(m): return m.author.id == member.id and isinstance(m.channel, discord.DMChannel)
            try:
                msg = await self.bot.wait_for("message", check=_check, timeout=180)
            except Exception:
                return await dm.send(embed=discord.Embed(title="Timed out", color=ERROR))
            use_tag = api.norm_tag(msg.content)

        # validate + cache
        try:
            pdata = await api.get_player(use_tag)
        except Exception:
            return await dm.send(embed=discord.Embed(title="Invalid tag", description="That tag couldn't be validated. Try again with `!bs tags save <tag>` in the server.", color=ERROR))
        # Save to bsinfo storage (max 3)
        async with self.config.user(member).tags() as tags:
            if use_tag not in tags and len(tags) < 3:
                tags.append(use_tag)
        await self._cache_player_bits(member, pdata)

        trophies = pdata.get("trophies", 0)
        ign = pdata.get("name", "Player")

        # 2) load tracked clubs and compute eligibility with LIVE data
        clubs_cog = _find_cog(self.bot, "clubs")
        tracked = await clubs_cog.config.guild(guild).clubs() if clubs_cog else {}
        if not tracked:
            return await dm.send(embed=discord.Embed(title="No clubs configured", description="Ask staff to add clubs with `[p]clubs add #TAG`.", color=ERROR))

        live_opts = []
        for ctag, cfg in tracked.items():
            try:
                cinfo = await api.get_club_by_tag(ctag)
            except Exception:
                continue
            members = len(cinfo.get("members") or [])
            req = int(cinfo.get("requiredTrophies", cfg.get("required_trophies", 0)))
            if trophies >= req and members < 50:
                live_opts.append((ctag, {
                    "name": cinfo.get("name") or cfg.get("name") or f"#{ctag}",
                    "required_trophies": req,
                    "role_id": cfg.get("role_id"),
                    "log_channel_id": cfg.get("log_channel_id"),
                    "leadership_role_id": cfg.get("leadership_role_id"),
                    "_members": members,
                    "_type": (cinfo.get("type") or "unknown").title(),
                    "_club_trophies": cinfo.get("trophies", 0),
                    "_desc": (cinfo.get("description") or "")[:140],
                }))
        if not live_opts:
            return await dm.send(embed=discord.Embed(title="No eligible clubs right now", color=ERROR))

        live_opts.sort(key=lambda x: (x[1]["_members"], -x[1].get("required_trophies", 0)))

        # 3) show options & let user pick
        lines = []
        for i, (ctag, ccfg) in enumerate(live_opts[:5], start=1):
            lines.append(
                f"**{i}. {ccfg['name']}**  #{ctag}\n"
                f"• Type: {ccfg['_type']} | Members: {ccfg['_members']}/50 | Club Trophies: {ccfg['_club_trophies']:,}\n"
                f"• Required Trophies: {ccfg.get('required_trophies', 0):,}\n"
                f"• {ccfg['_desc']}"
            )
        pick_embed = discord.Embed(
            title=f"Hi {ign}!",
            description="Pick a club by clicking a button below:\n\n" + "\n\n".join(lines),
            color=ACCENT
        )
        view = _PickView(member.id, live_opts)
        msg = await dm.send(embed=pick_embed, view=view)
        await view.wait()
        try:
            await msg.edit(view=None)
        except Exception:
            pass
        if view.selected is None:
            return await dm.send(embed=discord.Embed(title="Cancelled", color=WARN))
        ctag, ccfg = view.selected

        # 4) notify leadership / log channel
        content = None
        if ccfg.get("leadership_role_id"):
            role = guild.get_role(ccfg["leadership_role_id"])
            if role:
                content = role.mention
        target = None
        if ccfg.get("log_channel_id"):
            target = guild.get_channel(ccfg["log_channel_id"])
        if target:
            e = discord.Embed(
                title="New Application",
                description=f"**{ign}** ({pdata.get('tag','')}) wants to join **{ccfg['name']}** #{ctag}. Please accept in-game.",
                color=SUCCESS
            )
            await target.send(content=content, embed=e)

        await dm.send(embed=discord.Embed(
            title="Next Step",
            description=f"Great! Request to join **{ccfg['name']}** in-game now. "
                        f"Once you’re in, I’ll update your roles and nickname.",
            color=SUCCESS
        ))

    # ============================================================
    #                           COMMANDS
    # ============================================================

    @commands.group()
    async def bs(self, ctx):
        """Brawl Stars commands."""
        pass

    # ---------- Tag management: !bs tags ----------
    @bs.group(name="tags")
    async def bs_tags(self, ctx):
        """Manage your saved tags (max 3)."""
        pass

    @bs_tags.command(name="save")
    async def bs_tags_save(self, ctx, tag: str):
        """Save a tag after validating via the API."""
        if ctx.guild is None:
            return await ctx.send("This command can only be used in servers.")
        api = await self._api(ctx.guild)
        pdata = await api.get_player(tag)  # validate
        norm = api.norm_tag(tag)
        async with self.config.user(ctx.author).tags() as tags:
            if norm in tags:
                return await ctx.send(embed=discord.Embed(title="Tag already saved", description=f"{tag_pretty(norm)} is already in your list.", color=WARN))
            if len(tags) >= 3:
                return await ctx.send(embed=discord.Embed(title="Limit reached", description="You already have 3 tags saved.", color=ERROR))
            tags.append(norm)
        await self._cache_player_bits(ctx.author, pdata)
        await ctx.send(embed=discord.Embed(title="Tag saved", description=f"Added **{tag_pretty(norm)}**.", color=SUCCESS))

    @bs_tags.command(name="list")
    async def bs_tags_list(self, ctx):
        """Show your saved tags and the default one."""
        u = await self.config.user(ctx.author).all()
        tags = u["tags"]
        if not tags:
            return await ctx.send(embed=discord.Embed(title="No tags yet", description="Use `[p]bs tags save <tag>` to add one.", color=WARN))
        lines = []
        for i, t in enumerate(tags, start=1):
            star = " **(default)**" if (i - 1) == u["default_index"] else ""
            lines.append(f"**{i}.** {tag_pretty(t)}{star}")
        e = discord.Embed(title=f"{ctx.author.display_name}'s tags", description="\n".join(lines), color=ACCENT)
        await ctx.send(embed=e)

    @bs_tags.command(name="setdefault")
    async def bs_tags_setdefault(self, ctx, index: int):
        """Set which tag (1..3) is your default."""
        i = index - 1
        tags = await self.config.user(ctx.author).tags()
        if not (0 <= i < len(tags)):
            return await ctx.send(embed=discord.Embed(title="Invalid index", description="Choose an index from `[p]bs tags list`.", color=ERROR))
        await self.config.user(ctx.author).default_index.set(i)
        await ctx.send(embed=discord.Embed(title="Default updated", description=f"Default tag is now **{tag_pretty(tags[i])}**.", color=SUCCESS))

    @bs_tags.command(name="move")
    async def bs_tags_move(self, ctx, index_from: int, index_to: int):
        """Reorder your tags: move FROM to TO."""
        f = index_from - 1
        t = index_to - 1
        async with self.config.user(ctx.author).all() as u:
            tags: List[str] = u["tags"]
            if not (0 <= f < len(tags)) or not (0 <= t < len(tags)):
                return await ctx.send(embed=discord.Embed(title="Invalid index", description="Use indices from `[p]bs tags list`.", color=ERROR))
            item = tags.pop(f)
            tags.insert(t, item)
            # adjust default index
            if u["default_index"] == f:
                u["default_index"] = t
            elif f < u["default_index"] <= t:
                u["default_index"] -= 1
            elif t <= u["default_index"] < f:
                u["default_index"] += 1
        await ctx.send(embed=discord.Embed(title="Tags reordered", color=SUCCESS))

    @bs_tags.command(name="remove")
    async def bs_tags_remove(self, ctx, index: int):
        """Remove a saved tag by index (1..3)."""
        i = index - 1
        async with self.config.user(ctx.author).all() as u:
            tags: List[str] = u["tags"]
            if not (0 <= i < len(tags)):
                return await ctx.send(embed=discord.Embed(title="Invalid index", description="Use indices from `[p]bs tags list`.", color=ERROR))
            removed = tags.pop(i)
            if u["default_index"] >= len(tags):
                u["default_index"] = 0
        await ctx.send(embed=discord.Embed(title="Tag removed", description=f"Removed **{tag_pretty(removed)}**.", color=WARN))

    # ---------- Verify (guild-only) ----------
    @bs.command(name="verify")
    @commands.guild_only()
    async def bs_verify(self, ctx, tag: str):
        """Validate and save a tag (guild-only)."""
        await self.bs_tags_save(ctx, tag=tag)

    # ---------- Player (uses default tag if omitted) ----------
    @bs.command(name="player")
    async def bs_player(self, ctx, tag: Optional[str] = None):
        """Show a player's profile. If no tag is given, uses your default tag."""
        if ctx.guild is None and not tag:
            return await ctx.send("In DMs, please provide a tag: `bs player #TAG`.")
        api = await self._api(ctx.guild or self.bot.guilds[0])
        use_tag = tag or await self._get_default_tag(ctx.author)
        if not use_tag:
            return await ctx.send(embed=discord.Embed(title="No default tag", description="Use `[p]bs tags save <tag>` first, or provide a tag.", color=ERROR))
        p = await api.get_player(use_tag)

        name = p.get("name", "Unknown")
        tag_fmt = p.get("tag", "")
        trophies = p.get("trophies", 0)
        highest = p.get("highestTrophies", 0)
        exp     = p.get("expLevel", 0)
        icon_id = (p.get("icon") or {}).get("id", 0)
        club    = p.get("club") or {}
        club_name = club.get("name", "—")
        club_tag  = club.get("tag", "—")
        brawlers  = p.get("brawlers") or []

        e1 = discord.Embed(title=f"{name} ({tag_fmt})", color=ACCENT, description=f"**Club:** {club_name} {club_tag}")
        e1.add_field(name="Trophies", value=f"{trophies:,}")
        e1.add_field(name="Highest", value=f"{highest:,}")
        e1.add_field(name="EXP Level", value=str(exp))
        e1.add_field(name="Brawlers Owned", value=str(len(brawlers)))
        e1.set_thumbnail(url=player_avatar_url(icon_id))

        lines = []
        for b in sorted(brawlers, key=lambda x: (-x.get("trophies", 0), x.get("name", ""))):
            lines.append(f"**{b.get('name')}** — {b.get('trophies', 0):,} 🏆  | Pwr {b.get('power', 0)} | R{b.get('rank', 0)}")
        e2 = discord.Embed(title="Brawlers", color=ACCENT, description="\n".join(lines[:20]) or "—")

        pages = [e1, e2]
        view = EmbedPager(pages, author_id=ctx.author.id)
        await ctx.send(embed=e1, view=view)

    # ---------- Club overview ----------
    @bs.command(name="club")
    async def bs_club(self, ctx, club_tag: str):
        """Show club overview."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
        c = await api.get_club_by_tag(club_tag)

        name   = c.get("name", "Club")
        tag    = c.get("tag", "")
        desc   = c.get("description", "")
        badge  = c.get("badgeId") or 0
        ttype  = (c.get("type") or "unknown").title()
        req    = c.get("requiredTrophies", 0)
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

    # ---------- Club roster (paginated) ----------
    @bs.command(name="clubmembers")
    async def bs_clubmembers(self, ctx, club_tag: str):
        """List all members of a club (paginated)."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
        m = await api.get_club_members(club_tag)
        items = m.get("items") or []

        pages: List[discord.Embed] = []
        chunk = 20
        for i in range(0, len(items), chunk):
            part = items[i:i+chunk]
            desc = "\n".join(
                [
                    f"**{it.get('name')}** ({it.get('tag')}) • {it.get('trophies', 0):,} 🏆  • {it.get('role', 'member').title()}"
                    for it in part
                ]
            ) or "—"
            e = discord.Embed(title=f"Members ({i+1}-{min(i+chunk, len(items))}/{len(items)})", description=desc, color=ACCENT)
            pages.append(e)

        if not pages:
            pages = [discord.Embed(title="No members found", color=ERROR)]

        view = EmbedPager(pages, author_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    # ---------- Brawlers catalog ----------
    @bs.command(name="brawlers")
    async def bs_brawlers(self, ctx):
        """List all brawlers (paginated)."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
        data = await api.get_brawlers()
        items = data.get("items") or []
        items.sort(key=lambda b: (b.get("rarity", {}).get("rank", 99), b.get("name", "")))

        pages: List[discord.Embed] = []
        chunk = 12
        for i in range(0, len(items), chunk):
            part = items[i:i+chunk]
            lines = [f"**{b.get('name')}** — {b.get('rarity', {}).get('name', '?')}" for b in part]
            thumb_id = part[0].get("id", 0) if part else 0
            e = discord.Embed(title=f"Brawlers ({i+1}-{min(i+chunk, len(items))}/{len(items)})", description="\n".join(lines) or "—", color=ACCENT)
            if thumb_id:
                e.set_thumbnail(url=brawler_icon_url(thumb_id))
            pages.append(e)

        view = EmbedPager(pages, author_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    # ---------- Rankings ----------
    @bs.group(name="rankings")
    async def bs_rankings(self, ctx):
        """Global or country rankings."""
        pass

    @bs_rankings.command(name="players")
    async def bs_rankings_players(self, ctx, country: str = "global", limit: int = 25):
        """Top players (global or country code like 'AU', 'US')."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
        data = await api.get_rankings_players(country.lower(), limit)
        items = data.get("items") or []

        lines = []
        for i, it in enumerate(items, start=1):
            lines.append(f"**{i}.** {it.get('name')} ({it.get('tag')}) • {it.get('trophies', 0):,} 🏆")
        e = discord.Embed(title=f"Top Players — {country.upper()}", description="\n".join(lines) or "—", color=GOLD)
        await ctx.send(embed=e)

    @bs_rankings.command(name="clubs")
    async def bs_rankings_clubs(self, ctx, country: str = "global", limit: int = 25):
        """Top clubs (global or country code)."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
        data = await api.get_rankings_clubs(country.lower(), limit)
        items = data.get("items") or []

        lines = []
        for i, it in enumerate(items, start=1):
            lines.append(
                f"**{i}.** {it.get('name')} ({it.get('tag')}) • {it.get('trophies', 0):,} 🏆 • members {it.get('memberCount', 0)}"
            )
        e = discord.Embed(title=f"Top Clubs — {country.upper()}", description="\n".join(lines) or "—", color=GOLD)
        await ctx.send(embed=e)

    @bs_rankings.command(name="brawler")
    async def bs_rankings_brawler(self, ctx, id_or_name: str, country: str = "global", limit: int = 25):
        """Top players for a specific brawler."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
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
            lines.append(f"**{i}.** {player.get('name')} ({player.get('tag')}) • {it.get('trophies', 0):,} 🏆")
        e = discord.Embed(title=f"Top {id_or_name} — {country.upper()}", description="\n".join(lines) or "—", color=GOLD)
        e.set_thumbnail(url=brawler_icon_url(bid))
        await ctx.send(embed=e)

    # ---------- Events ----------
    @bs.command(name="events")
    async def bs_events(self, ctx):
        """Current event rotation (maps & modes)."""
        api = await self._api(ctx.guild or self.bot.guilds[0])
        rot = await api.get_events_rotation()
        active = rot.get("active") or rot.get("events") or rot.get("items") or rot
        if isinstance(active, dict):
            active = active.get("events") or active.get("items") or []

        pages: List[discord.Embed] = []
        for ev in (active or []):
            # tolerate either object or string shapes
            mode = ev.get("mode")
            if isinstance(mode, dict):
                mode = mode.get("name")
            elif isinstance(ev.get("event"), dict):
                mode = (ev["event"].get("mode") or {}).get("name")
            map_name = ev.get("map")
            if isinstance(map_name, dict):
                map_name = map_name.get("name")
            elif isinstance(ev.get("event"), dict):
                map_name = (ev["event"].get("map") or {}).get("name")
            map_id = (ev.get("map") or {}).get("id") or (ev.get("event", {}).get("map") or {}).get("id") or 0

            e = discord.Embed(title=map_name or "Unknown Map", description=f"Mode: **{(mode or 'Unknown')}**", color=ACCENT)
            if mode:
                e.set_thumbnail(url=mode_icon_url(str(mode)))
            if map_id:
                e.set_image(url=map_image_url(int(map_id)))
            pages.append(e)

        if not pages:
            pages = [discord.Embed(title="No active events reported.", color=WARN)]

        view = EmbedPager(pages, author_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    # ---------- Start application (server-only -> DMs) ----------
    @bs.command(name="start")
    @commands.guild_only()
    async def bs_start(self, ctx):
        """
        Start the application in DMs.
        - If Onboarding cog is loaded, we hand off to it.
        - Otherwise we run a minimal fallback flow so users aren't blocked.
        """
        # Open DM first (so we can guarantee a place to continue)
        try:
            dm = await ctx.author.create_dm()
            await dm.send(embed=discord.Embed(
                title="Club Application",
                description="Let's get you set up! Follow the prompts here.",
                color=ACCENT
            ))
        except discord.Forbidden:
            return await ctx.send(embed=discord.Embed(
                title="I can't DM you",
                description="Enable DMs from server members and try again.",
                color=ERROR
            ))

        await ctx.send(embed=discord.Embed(
            title="Check your DMs",
            description="I’ve sent you a message to continue your application.",
            color=SUCCESS
        ))

        # Prefer the full Onboarding flow if available (case-insensitive lookup)
        ob = _find_cog(self.bot, "onboarding")
        if ob and hasattr(ob, "start_application_dm"):
            return await ob.start_application_dm(ctx.guild, ctx.author)  # type: ignore[attr-defined]

        # Fallback: built-in application so the user isn't blocked
        await self._fallback_application_dm(ctx.guild, ctx.author)


async def setup(bot: Red):
    await bot.add_cog(BSInfo(bot))
