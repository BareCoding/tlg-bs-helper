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

MAX_MEMBERS = 30  # treat 30 as full

def _find_cog(bot: Red, name: str):
    want = (name or "").lower()
    for cog in bot.cogs.values():
        if getattr(cog, "__cog_name__", "").lower() == want:
            return cog
    return None

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
    """Lookups + per-user tag storage + robust DM application fallback."""

    __version__ = "0.9.1"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB51F0C, force_registration=True)
        default_user = {"tags": [], "default_index": 0, "ign_cache": "", "club_tag_cache": ""}
        self.config.register_user(**default_user)
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

    async def _fallback_application_dm(self, guild: discord.Guild, member: discord.Member):
        try:
            dm = await member.create_dm()
        except discord.Forbidden:
            return
        api = await self._api(guild)

        # 1) tag
        use_tag = await self._get_default_tag(member)
        if not use_tag:
            await dm.send(embed=discord.Embed(
                title="Your Tag", description="Reply with your player tag (e.g. `#ABCD123`).", color=ACCENT
            ))
            def _check(m): return m.author.id == member.id and isinstance(m.channel, discord.DMChannel)
            try:
                msg = await self.bot.wait_for("message", check=_check, timeout=180)
            except Exception:
                return await dm.send(embed=discord.Embed(title="Timed out", color=ERROR))
            use_tag = api.norm_tag(msg.content)

        try:
            pdata = await api.get_player(use_tag)
        except Exception:
            return await dm.send(embed=discord.Embed(
                title="Invalid tag",
                description="That tag couldn't be validated. Try again with `!bs tags save <tag>` in the server.",
                color=ERROR
            ))
        async with self.config.user(member).tags() as tags:
            if use_tag not in tags and len(tags) < 3:
                tags.append(use_tag)
        await self._cache_player_bits(member, pdata)

        trophies = pdata.get("trophies", 0)
        ign = pdata.get("name", "Player")

        # 2) clubs + reasons
        clubs_cog = _find_cog(self.bot, "clubs")
        tracked = await clubs_cog.config.guild(guild).clubs() if clubs_cog else {}
        if not tracked:
            return await dm.send(embed=discord.Embed(
                title="No clubs configured", description="Ask staff to add clubs with `[p]clubs add #TAG`.", color=ERROR
            ))

        eligible_open, full_but_eligible, under_req = [], [], []
        for ctag, cfg in tracked.items():
            try:
                cinfo = await api.get_club_by_tag(ctag)
            except Exception:
                continue
            members = len(cinfo.get("members") or [])
            req = int(cinfo.get("requiredTrophies", cfg.get("required_trophies", 0)))
            merged = {
                "name": cinfo.get("name") or cfg.get("name") or f"#{ctag}",
                "required_trophies": req,
                "role_id": cfg.get("role_id"),
                "log_channel_id": cfg.get("log_channel_id"),
                "leadership_role_id": cfg.get("leadership_role_id"),
                "_members": members,
                "_type": (cinfo.get("type") or "unknown").title(),
                "_club_trophies": cinfo.get("trophies", 0),
                "_desc": (cinfo.get("description") or "")[:180],
                "badge_id": cinfo.get("badgeId") or 0,
            }
            if trophies < req:
                under_req.append((ctag, merged))
            elif members >= MAX_MEMBERS:
                full_but_eligible.append((ctag, merged))
            else:
                eligible_open.append((ctag, merged))

        if not eligible_open:
            if full_but_eligible and not under_req:
                await dm.send(embed=discord.Embed(
                    title="All eligible clubs are full",
                    description="Right now every club you qualify for is at capacity. Leadership has been pinged — they’ll make space and follow up.",
                    color=WARN
                ))
                ob = _find_cog(self.bot, "onboarding")
                notify_id = None
                if ob:
                    gconf = await ob.config.guild(guild).all()
                    notify_id = gconf.get("apply_notify_channel_id")
                if notify_id:
                    notify = guild.get_channel(notify_id)
                    if notify:
                        role = discord.utils.get(guild.roles, name="BS Club Leadership")
                        mention = role.mention if role else None
                        e = discord.Embed(
                            title="Applicant waiting — all eligible clubs full",
                            description=f"**{ign}** ({pdata.get('tag','')}) qualifies but all eligible clubs are full (≥{MAX_MEMBERS}).",
                            color=ERROR
                        )
                        await notify.send(content=mention, embed=e)
                return
            else:
                return await dm.send(embed=discord.Embed(
                    title="No eligible clubs yet",
                    description="You don’t meet the trophy requirements for any of our clubs right now.\nKeep pushing trophies and try again soon!",
                    color=ERROR
                ))

        eligible_open.sort(key=lambda x: (x[1]["_members"], -x[1].get("required_trophies", 0)))
        cards = []
        for ctag, c in eligible_open[:5]:
            cards.append(
                f"**{c['name']}**  `#{ctag}`\n"
                f"**Members:** {c['_members']}/{MAX_MEMBERS} • **Req:** {c.get('required_trophies',0):,} • "
                f"**Club Trophies:** {c['_club_trophies']:,} • **Type:** {c['_type']}\n"
                f"{c['_desc'] or '—'}"
            )
        pick_embed = discord.Embed(
            title=f"Hi {ign}! Pick an eligible club",
            description="\n\n".join(cards),
            color=GOLD
        )
        if len(eligible_open) == 1 and eligible_open[0][1]["badge_id"]:
            pick_embed.set_thumbnail(url=club_badge_url(eligible_open[0][1]["badge_id"]))

        view = _PickView(member.id, eligible_open)
        msg = await dm.send(embed=pick_embed, view=view)
        await view.wait()
        try:
            await msg.edit(view=None)
        except Exception:
            pass
        if view.selected is None:
            return await dm.send(embed=discord.Embed(title="Cancelled", color=WARN))
        ctag, ccfg = view.selected

        content = None
        rid = ccfg.get("leadership_role_id")
        if rid:
            role = guild.get_role(rid)
            if role:
                content = role.mention
        if not content:
            role = discord.utils.get(guild.roles, name="BS Club Leadership")
            if role:
                content = role.mention

        target = guild.get_channel(ccfg.get("log_channel_id") or 0)
        if target:
            e = discord.Embed(
                title="New Application",
                description=f"**{ign}** ({pdata.get('tag','')}) wants to join **{ccfg['name']}** `#{ctag}`. Please accept in-game.",
                color=SUCCESS
            )
            await target.send(content=content, embed=e)

        await dm.send(embed=discord.Embed(
            title="Next Step",
            description=f"Great! Request to join **{ccfg['name']}** in-game now. Once you’re in, I’ll update your roles and nickname.",
            color=SUCCESS
        ))

    # ============================ Commands ============================

    @commands.group()
    async def bs(self, ctx):
        """Brawl Stars commands."""
        pass

    @bs.group(name="tags")
    async def bs_tags(self, ctx):
        """Manage your saved tags (max 3)."""
        pass

    @bs_tags.command(name="save")
    async def bs_tags_save(self, ctx, tag: str):
        """Save a tag after validating via the API (guild-only)."""
        if ctx.guild is None:
            return await ctx.send("This command can only be used in servers.")
        api = await self._api(ctx.guild)
        pdata = await api.get_player(tag)  # validate
        norm = api.norm_tag(tag)
        async with self.config.user(ctx.author).tags() as tags:
            if norm in tags:
                return await ctx.send(embed=discord.Embed(
                    title="Tag already saved", description=f"{tag_pretty(norm)} is already in your list.", color=WARN
                ))
            if len(tags) >= 3:
                return await ctx.send(embed=discord.Embed(
                    title="Limit reached", description="You already have 3 tags saved.", color=ERROR
                ))
            tags.append(norm)
        await self._cache_player_bits(ctx.author, pdata)
        await ctx.send(embed=discord.Embed(title="Tag saved", description=f"Added **{tag_pretty(norm)}**.", color=SUCCESS))

    @bs_tags.command(name="list")
    async def bs_tags_list(self, ctx):
        u = await self.config.user(ctx.author).all()
        tags = u["tags"]
        if not tags:
            return await ctx.send(embed=discord.Embed(
                title="No tags yet",
                description="Use `[p]bs tags save <tag>` to add one.",
                color=WARN
            ))
        lines = []
        for i, t in enumerate(tags, start=1):
            star = " **(default)**" if (i - 1) == u["default_index"] else ""
            lines.append(f"**{i}.** {tag_pretty(t)}{star}")
        e = discord.Embed(title=f"{ctx.author.display_name}'s tags", description="\n".join(lines), color=ACCENT)
        await ctx.send(embed=e)

    @bs_tags.command(name="setdefault")
    async def bs_tags_setdefault(self, ctx, index: int):
        i = index - 1
        tags = await self.config.user(ctx.author).tags()
        if not (0 <= i < len(tags)):
            return await ctx.send(embed=discord.Embed(
                title="Invalid index", description="Choose an index from `[p]bs tags list`.", color=ERROR
            ))
        await self.config.user(ctx.author).default_index.set(i)
        await ctx.send(embed=discord.Embed(
            title="Default updated", description=f"Default tag is now **{tag_pretty(tags[i])}**.", color=SUCCESS
        ))

    @bs_tags.command(name="move")
    async def bs_tags_move(self, ctx, index_from: int, index_to: int):
        f = index_from - 1
        t = index_to - 1
        async with self.config.user(ctx.author).all() as u:
            tags: List[str] = u["tags"]
            if not (0 <= f < len(tags)) or not (0 <= t < len(tags)):
                return await ctx.send(embed=discord.Embed(
                    title="Invalid index", description="Use indices from `[p]bs tags list`.", color=ERROR
                ))
            item = tags.pop(f)
            tags.insert(t, item)
            if u["default_index"] == f:
                u["default_index"] = t
            elif f < u["default_index"] <= t:
                u["default_index"] -= 1
            elif t <= u["default_index"] < f:
                u["default_index"] += 1
        await ctx.send(embed=discord.Embed(title="Tags reordered", color=SUCCESS))

    @bs_tags.command(name="remove")
    async def bs_tags_remove(self, ctx, index: int):
        i = index - 1
        async with self.config.user(ctx.author).all() as u:
            tags: List[str] = u["tags"]
            if not (0 <= i < len(tags)):
                return await ctx.send(embed=discord.Embed(
                    title="Invalid index", description="Use indices from `[p]bs tags list`.", color=ERROR
                ))
            removed = tags.pop(i)
            if u["default_index"] >= len(tags):
                u["default_index"] = 0
        await ctx.send(embed=discord.Embed(title="Tag removed", description=f"Removed **{tag_pretty(removed)}**.", color=WARN))

    @bs.command(name="verify")
    @commands.guild_only()
    async def bs_verify(self, ctx, tag: str):
        """Validate and save a tag (guild-only)."""
        await self.bs_tags_save(ctx, tag=tag)

    @bs.command(name="player")
    async def bs_player(self, ctx, tag: Optional[str] = None):
        """Show a player's profile. If no tag is given, uses your default tag."""
        if ctx.guild is None and not tag:
            return await ctx.send("In DMs, please provide a tag: `bs player #TAG`.")
        api = await self._api(ctx.guild or self.bot.guilds[0])
        use_tag = tag or await self._get_default_tag(ctx.author)
        if not use_tag:
            pref = ctx.clean_prefix
            return await ctx.send(embed=discord.Embed(
                title="No tag to look up",
                description=(
                    "You didn’t provide a tag and you don’t have a default tag saved.\n\n"
                    f"• Save a tag: `{pref}bs tags save #YOURTAG`\n"
                    f"• Or verify & save: `{pref}bs verify #YOURTAG`\n"
                    f"• Or run with a tag: `{pref}bs player #YOURTAG`"
                ),
                color=ERROR
            ))

        p = await api.get_player(use_tag)
        name      = p.get("name", "Unknown")
        tag_fmt   = p.get("tag", "")
        trophies  = p.get("trophies", 0)
        highest   = p.get("highestTrophies", 0)
        exp       = p.get("expLevel", 0)
        icon_id   = (p.get("icon") or {}).get("id", 0)
        club      = p.get("club") or {}
        club_name = club.get("name", "—")
        club_tag  = club.get("tag", "—")
        club_role = (p.get("role") or club.get("role") or "member").title()
        brawlers  = p.get("brawlers") or []

        # Extra stats
        solo_wins = p.get("soloVictories", 0)
        duo_wins  = p.get("duoVictories", 0)
        v3_wins   = p.get("3vs3Victories", p.get("3v3Victories", 0))

        sp_cnt = sum(len(b.get("starPowers") or []) for b in brawlers)
        gd_cnt = sum(len(b.get("gadgets") or []) for b in brawlers)
        gear_cnt = sum(len(b.get("gears") or []) for b in brawlers)

        e1 = discord.Embed(
            title=f"{name} ({tag_fmt})",
            description=f"**Club:** {club_name} {club_tag} • **Role:** {club_role}",
            color=ACCENT
        )
        e1.add_field(name="Trophies", value=f"{trophies:,}")
        e1.add_field(name="Best (All-time)", value=f"{highest:,}")
        e1.add_field(name="EXP Level", value=str(exp))
        e1.add_field(name="Brawlers Owned", value=str(len(brawlers)))
        e1.add_field(name="Star Powers", value=str(sp_cnt))
        e1.add_field(name="Gadgets", value=str(gd_cnt))
        e1.add_field(name="Gears", value=str(gear_cnt))
        if icon_id:
            e1.set_thumbnail(url=player_avatar_url(icon_id))

        e2 = discord.Embed(title="Modes & Progress", color=ACCENT)
        e2.add_field(name="3v3 Victories", value=f"{v3_wins:,}")
        e2.add_field(name="Solo Victories", value=f"{solo_wins:,}")
        e2.add_field(name="Duo Victories", value=f"{duo_wins:,}")

        lines = []
        for b in sorted(brawlers, key=lambda x: (-x.get("trophies", 0), x.get("name", "")))[:20]:
            nm = b.get("name")
            tr = b.get("trophies", 0)
            pw = b.get("power", 0)
            rk = b.get("rank", 0)
            sps = len(b.get("starPowers") or [])
            gds = len(b.get("gadgets") or [])
            grs = len(b.get("gears") or [])
            extra = []
            if sps: extra.append(f"{sps}⭐")
            if gds: extra.append(f"{gds}🛠️")
            if grs: extra.append(f"{grs}⚙️")
            addon = (" • " + " ".join(extra)) if extra else ""
            lines.append(f"**{nm}** — {tr:,} 🏆 | Pwr {pw} | R{rk}{addon}")
        e3 = discord.Embed(title="Top Brawlers", description="\n".join(lines) or "—", color=ACCENT)

        pages = [e1, e2, e3]
        view = EmbedPager(pages, author_id=ctx.author.id)
        await ctx.send(embed=e1, view=view)

    @bs.command(name="club")
    async def bs_club(self, ctx, club_tag: str):
        api = await self._api(ctx.guild or self.bot.guilds[0])
        c = await api.get_club_by_tag(club_tag)
        name = c.get("name", "Club")
        tag  = c.get("tag", "")
        desc = c.get("description", "")
        badge = c.get("badgeId") or 0
        ttype = (c.get("type") or "unknown").title()
        req = c.get("requiredTrophies", 0)
        count = len(c.get("members") or [])
        trophies = c.get("trophies", 0)
        e = discord.Embed(title=f"{name} ({tag})", color=GOLD, description=desc or "—")
        e.add_field(name="Type", value=ttype)
        e.add_field(name="Req. Trophies", value=f"{req:,}")
        e.add_field(name="Members", value=f"{count}/{MAX_MEMBERS}")
        e.add_field(name="Club Trophies", value=f"{trophies:,}")
        if badge:
            e.set_thumbnail(url=club_badge_url(badge))
        await ctx.send(embed=e)

    @bs.command(name="clubmembers")
    async def bs_clubmembers(self, ctx, club_tag: str):
        api = await self._api(ctx.guild or self.bot.guilds[0])
        m = await api.get_club_members(club_tag)
        items = m.get("items") or []
        pages: List[discord.Embed] = []
        chunk = 20
        for i in range(0, len(items), chunk):
            part = items[i:i+chunk]
            desc = "\n".join(
                [f"**{it.get('name')}** ({it.get('tag')}) • {it.get('trophies', 0):,} 🏆 • {it.get('role', 'member').title()}" for it in part]
            ) or "—"
            e = discord.Embed(title=f"Members ({i+1}-{min(i+chunk, len(items))}/{len(items)})", description=desc, color=ACCENT)
            pages.append(e)
        if not pages:
            pages = [discord.Embed(title="No members found", color=ERROR)]
        view = EmbedPager(pages, author_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    @bs.command(name="brawlers")
    async def bs_brawlers(self, ctx):
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
            e = discord.Embed(
                title=f"Brawlers ({i+1}-{min(i+chunk, len(items))}/{len(items)})",
                description="\n".join(lines) or "—",
                color=ACCENT
            )
            if thumb_id:
                e.set_thumbnail(url=brawler_icon_url(thumb_id))
            pages.append(e)
        view = EmbedPager(pages, author_id=ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    @bs.group(name="rankings")
    async def bs_rankings(self, ctx):
        """Global or country rankings."""
        pass

    @bs_rankings.command(name="players")
    async def bs_rankings_players(self, ctx, country: str = "global", limit: int = 25):
        api = await self._api(ctx.guild or self.bot.guilds[0])
        data = await api.get_rankings_players(country.lower(), limit)
        items = data.get("items") or []
        lines = [f"**{i}.** {it.get('name')} ({it.get('tag')}) • {it.get('trophies', 0):,} 🏆" for i, it in enumerate(items, start=1)]
        e = discord.Embed(title=f"Top Players — {country.upper()}", description="\n".join(lines) or "—", color=GOLD)
        await ctx.send(embed=e)

    @bs_rankings.command(name="clubs")
    async def bs_rankings_clubs(self, ctx, country: str = "global", limit: int = 25):
        api = await self._api(ctx.guild or self.bot.guilds[0])
        data = await api.get_rankings_clubs(country.lower(), limit)
        items = data.get("items") or []
        lines = [f"**{i}.** {it.get('name')} ({it.get('tag')}) • {it.get('trophies', 0):,} 🏆 • members {it.get('memberCount', 0)}"
                 for i, it in enumerate(items, start=1)]
        e = discord.Embed(title=f"Top Clubs — {country.upper()}", description="\n".join(lines) or "—", color=GOLD)
        await ctx.send(embed=e)

    @bs_rankings.command(name="brawler")
    async def bs_rankings_brawler(self, ctx, id_or_name: str, country: str = "global", limit: int = 25):
        api = await self._api(ctx.guild or self.bot.guilds[0])
        all_b = await api.get_brawlers()
        if id_or_name.isdigit():
            bid: Optional[int] = int(id_or_name)
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

    @bs.command(name="events")
    async def bs_events(self, ctx):
        api = await self._api(ctx.guild or self.bot.guilds[0])
        rot = await api.get_events_rotation()
        active = rot.get("active") or rot.get("events") or rot.get("items") or rot
        if isinstance(active, dict):
            active = active.get("events") or active.get("items") or []
        pages: List[discord.Embed] = []
        for ev in (active or []):
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

    @bs.command(name="start")
    @commands.guild_only()
    async def bs_start(self, ctx):
        """Start the application in DMs; uses Onboarding if loaded, otherwise fallback."""
        # open DM first
        try:
            dm = await ctx.author.create_dm()
            await dm.send(embed=discord.Embed(
                title="Club Application", description="Let's get you set up! Follow the prompts here.", color=ACCENT
            ))
        except discord.Forbidden:
            return await ctx.send(embed=discord.Embed(
                title="I can't DM you", description="Enable DMs from server members and try again.", color=ERROR
            ))
        await ctx.send(embed=discord.Embed(
            title="Check your DMs", description="I’ve sent you a message to continue your application.", color=SUCCESS
        ))
        ob = _find_cog(self.bot, "onboarding")
        if ob and hasattr(ob, "start_application_dm"):
            return await ob.start_application_dm(ctx.guild, ctx.author)  # type: ignore
        await self._fallback_application_dm(ctx.guild, ctx.author)

async def setup(bot: Red):
    await bot.add_cog(BSInfo(bot))
