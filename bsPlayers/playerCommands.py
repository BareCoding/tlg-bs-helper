# bsPlayers/playerCommands.py
from __future__ import annotations

import asyncio
import datetime as dt
from typing import List, Optional, Tuple, Dict, Any

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

from .api import BrawlStarsAPI, BSAPIError, normalize_tag

# =========================
# Brawlify CDN helpers
# =========================
CDN = "https://cdn.brawlify.com"

def brawler_img_url(brawler_id: int, *, style: str = "borderless") -> str:
    # Valid: "borderless" (nice) or "borders"
    sub = "borderless" if style == "borderless" else "borders"
    return f"{CDN}/brawlers/{sub}/{int(brawler_id)}.png"

def profile_icon_url(icon_id: int) -> str:
    return f"{CDN}/profile-icons/{int(icon_id)}.png"

def club_badge_url(badge_id: int) -> str:
    return f"{CDN}/club-badges/{int(badge_id)}.png"

# =========================
# Utils / styles
# =========================
COLOR_PRIMARY = discord.Color.from_rgb(52, 152, 219)   # blue
COLOR_GOOD    = discord.Color.from_rgb(46, 204, 113)   # green
COLOR_WARN    = discord.Color.from_rgb(241, 196, 15)   # yellow
COLOR_BAD     = discord.Color.from_rgb(231, 76, 60)    # red

def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default

def _parse_battletime(s: str) -> Optional[dt.datetime]:
    # BS API often: "2024-03-19T11:31:47.000Z" (ISO) — handle both ISO/compact
    if not s:
        return None
    try:
        if "-" in s:
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
        return dt.datetime.strptime(s, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None

def _ago(t: Optional[dt.datetime]) -> str:
    if not t:
        return "—"
    delta = dt.datetime.now(dt.timezone.utc) - t
    secs = int(delta.total_seconds())
    if secs < 60: return f"{secs}s ago"
    if secs < 3600: return f"{secs//60}m ago"
    if secs < 86400: return f"{secs//3600}h ago"
    if secs < 604800: return f"{secs//86400}d ago"
    return f"{secs//604800}w ago"

# =========================
# Cog
# =========================
class PlayerCommands(commands.Cog):
    """Brawl Stars player commands — tags, profile, brawlers, battlelog, with rich embeds."""

    __author__  = "Pat"
    __version__ = "2.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB51A11, force_registration=True)
        # per-user data
        self.config.register_user(tags=[])
        # global snapshots for deltas/last seen
        self.config.register_global(stats={})  # {TAG: {today_base, today_base_date, week_base, week_base_date, last_trophies, record_high, last_seen}}
        # warm cache for /brawlers (for total count)
        self._brawler_catalog: Optional[List[Dict[str, Any]]] = None
        # background snapshotter
        self._snap_task = self.bot.loop.create_task(self._snapshot_loop())

    def cog_unload(self):
        try:
            self._snap_task.cancel()
        except Exception:
            pass

    # ---------- client + helpers ----------
    async def _client(self) -> BrawlStarsAPI:
        return BrawlStarsAPI(self.bot)

    async def _resolve_tag(
        self,
        ctx: commands.Context,
        explicit_tag: Optional[str],
        member: Optional[discord.Member],
    ) -> Tuple[str, discord.Member]:
        if explicit_tag:
            return normalize_tag(explicit_tag), (member or ctx.author)
        target = member or ctx.author
        tags: List[str] = await self.config.user(target).tags()
        if not tags:
            raise commands.UserFeedbackCheckFailure(
                f"No saved tags for **{target.display_name}**. "
                f"Use `{ctx.clean_prefix}tag verify #YOURTAG` or provide a #tag."
            )
        return tags[0], target

    async def _get_brawler_catalog(self) -> List[Dict[str, Any]]:
        if self._brawler_catalog is None:
            client = await self._client()
            try:
                data = await client.list_brawlers()
            finally:
                await client.close()
            items = data.get("items", data) if isinstance(data, dict) else data
            self._brawler_catalog = items or []
        return self._brawler_catalog

    # ---------- background snapshots ----------
    async def _snapshot_loop(self):
        await asyncio.sleep(5)
        while True:
            try:
                await self._take_snapshots()
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            await asyncio.sleep(3600)  # hourly

    async def _take_snapshots(self):
        users = await self.config.all_users()
        uniq_tags: set[str] = set()
        for u in users.values():
            for t in u.get("tags", []):
                uniq_tags.add(normalize_tag(t))
        if not uniq_tags:
            return

        now = dt.datetime.now(dt.timezone.utc)
        today_key = now.date().isoformat()
        monday_key = (now - dt.timedelta(days=now.weekday())).date().isoformat()

        stats = await self.config.stats()
        client = await self._client()
        try:
            for tag in list(uniq_tags):
                try:
                    p = await client.get_player(tag)
                    bl = await client.get_player_battlelog(tag)
                except BSAPIError:
                    continue

                entry = stats.get(tag, {})  # dict
                cur = _safe_int(p.get("trophies", 0))
                entry["last_trophies"] = cur
                entry["record_high"] = max(_safe_int(entry.get("record_high", 0)), _safe_int(p.get("highestTrophies", 0)), cur)

                # last seen
                latest = None
                for it in bl.get("items", [])[:1]:
                    latest = _parse_battletime(it.get("battleTime"))
                if latest:
                    entry["last_seen"] = latest.isoformat()

                # baseline rotate
                if entry.get("today_base_date") != today_key:
                    entry["today_base_date"] = today_key
                    entry["today_base"] = cur
                if entry.get("week_base_date") != monday_key:
                    entry["week_base_date"] = monday_key
                    entry["week_base"] = cur

                stats[tag] = entry
                await asyncio.sleep(0.2)  # gentle on rate limits
        finally:
            await self.config.stats.set(stats)
            await client.close()

    # =========================
    # TAG COMMANDS
    # =========================
    @commands.group(name="tag", invoke_without_command=True)
    @commands.guild_only()
    async def tag_group(self, ctx: commands.Context):
        emb = discord.Embed(
            title="Tag commands",
            description=(
                f"**Verify:** `{ctx.clean_prefix}tag verify #TAG`\n"
                f"**Remove:** `{ctx.clean_prefix}tag remove #TAG`\n"
                f"**List:** `{ctx.clean_prefix}tag list [@user]`"
            ),
            color=COLOR_PRIMARY,
        )
        await ctx.send(embed=emb)

    @tag_group.command(name="verify")
    async def tag_verify(self, ctx: commands.Context, tag: str):
        tag = normalize_tag(tag)
        client = await self._client()
        try:
            player = await client.get_player(tag)
        except BSAPIError as e:
            return await ctx.send(embed=discord.Embed(title="Tag verification failed", description=str(e), color=COLOR_BAD))
        finally:
            await client.close()

        async with self.config.user(ctx.author).tags() as tags:
            if tag not in tags:
                tags.append(tag)

        icon_id = (player.get("icon") or {}).get("id")
        emb = discord.Embed(
            title="Tag verified",
            description=(
                f"**{player.get('name','?')}** — `#{tag}`\n"
                f"🏆 **Trophies:** {player.get('trophies', 0)}"
            ),
            color=COLOR_GOOD,
        )
        if icon_id:
            emb.set_thumbnail(url=profile_icon_url(icon_id))
        await ctx.send(embed=emb)

    @tag_group.command(name="remove")
    async def tag_remove(self, ctx: commands.Context, tag: str):
        tag = normalize_tag(tag)
        removed = False
        async with self.config.user(ctx.author).tags() as tags:
            if tag in tags:
                tags.remove(tag)
                removed = True
        emb = discord.Embed(
            title=("Tag removed" if removed else "Tag not found"),
            description=(f"Removed **#{tag}**." if removed else "That tag isn’t saved on your account."),
            color=(COLOR_GOOD if removed else COLOR_WARN),
        )
        await ctx.send(embed=emb)

    @tag_group.command(name="list")
    async def tag_list(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        user = user or ctx.author
        tags = await self.config.user(user).tags()
        if not tags:
            return await ctx.send(embed=discord.Embed(title="No tags", description=f"No tags saved for **{user.display_name}**.", color=COLOR_WARN))
        emb = discord.Embed(
            title=f"{user.display_name}'s tags",
            description="\n".join(f"• `#{t}`" for t in tags),
            color=COLOR_PRIMARY,
        )
        await ctx.send(embed=emb)

    # =========================
    # PROFILE
    # =========================
    @commands.command(name="profile")
    @commands.guild_only()
    async def profile(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
        tag: Optional[str] = None,
    ):
        try:
            use_tag, _ = await self._resolve_tag(ctx, tag, member)
        except commands.UserFeedbackCheckFailure as e:
            return await ctx.send(embed=discord.Embed(title="Missing tag", description=str(e), color=COLOR_WARN))

        client = await self._client()
        try:
            p = await client.get_player(use_tag)
            club_badge_id = None
            club = p.get("club") or {}
            if club.get("tag"):
                try:
                    cdata = await client.get_club(club["tag"])
                    club_badge_id = cdata.get("badgeId")
                except BSAPIError:
                    club_badge_id = None
        except BSAPIError as e:
            return await ctx.send(embed=discord.Embed(title="API error", description=str(e), color=COLOR_BAD))
        finally:
            await client.close()

        # brawlers + top
        blist = p.get("brawlers", []) or []
        blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
        top_b = blist[0] if blist else None

        # brawler summary
        catalog = await self._get_brawler_catalog()
        total_brawlers = len(catalog)
        have_cnt = len(blist)
        p11_cnt = sum(1 for b in blist if _safe_int(b.get("power", 0)) >= 11)
        avg_bt = round(sum(_safe_int(b.get("trophies", 0)) for b in blist) / have_cnt, 1) if have_cnt else 0.0

        # snapshots/deltas
        stats = await self.config.stats()
        s = stats.get(use_tag, {})
        trophies = _safe_int(p.get("trophies", 0))
        pb = _safe_int(p.get("highestTrophies", 0))
        today_delta = trophies - _safe_int(s.get("today_base", trophies))
        week_delta  = trophies - _safe_int(s.get("week_base", trophies))
        last_seen = _ago(dt.datetime.fromisoformat(s["last_seen"])) if s.get("last_seen") else "—"

        icon_id = (p.get("icon") or {}).get("id")
        name = p.get("name", "?")
        club_name = (p.get("club") or {}).get("name", "—")

        emb = discord.Embed(title=f"{name} (#{use_tag})", color=COLOR_PRIMARY)
        # header visuals
        if top_b and "id" in top_b:
            emb.set_thumbnail(url=brawler_img_url(top_b["id"]))
        elif icon_id:
            emb.set_thumbnail(url=profile_icon_url(icon_id))
        if club_badge_id:
            emb.set_author(name=club_name, icon_url=club_badge_url(club_badge_id))
        else:
            emb.set_author(name=club_name)

        # Overview
        emb.add_field(
            name="Overview",
            value=(
                f"**Trophies:** {trophies}\n"
                f"**Personal Best:** {pb}\n"
                f"**EXP Level:** {p.get('expLevel','?')}\n"
                f"**Last Seen:** {last_seen}"
            ),
            inline=False,
        )

        # Progression
        emb.add_field(
            name="Trophy Progression",
            value=f"**Today:** {today_delta:+}\n**Week:** {week_delta:+}",
            inline=False,
        )

        # Wins
        emb.add_field(
            name="Wins",
            value=(
                f"**3v3 Wins:** {p.get('3vs3Victories', 0)}\n"
                f"**Solo Wins:** {p.get('soloVictories', 0)}\n"
                f"**Duo Wins:** {p.get('duoVictories', 0)}"
            ),
            inline=False,
        )

        # Brawlers Summary
        emb.add_field(
            name="Brawlers",
            value=(
                f"**Collected:** {have_cnt}/{total_brawlers}\n"
                f"**Power 11:** {p11_cnt}\n"
                f"**Average Trophies (owned):** {avg_bt}"
            ),
            inline=False,
        )

        # Top Brawler (rich line)
        if top_b:
            emb.add_field(
                name="Top Brawler",
                value=(
                    f"**{top_b.get('name','?')}** — {top_b.get('trophies',0)} 🏆\n"
                    f"Power {top_b.get('power','?')} · Rank {top_b.get('rank','?')}"
                ),
                inline=False,
            )

        # Optional: show first 6 brawlers compact
        if blist:
            top_lines = []
            for b in blist[:6]:
                top_lines.append(
                    f"• **{b.get('name','?')}** — {b.get('trophies',0)} 🏆 · "
                    f"P{b.get('power','?')} · R{b.get('rank','?')}"
                )
            emb.add_field(name="Top Picks", value="\n".join(top_lines), inline=False)

        emb.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=emb)

    # =========================
    # BRAWLERS
    # =========================
    @commands.command(name="brawlers")
    @commands.guild_only()
    async def brawlers(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
        tag: Optional[str] = None,
        limit: Optional[int] = 10,
    ):
        if isinstance(tag, str) and tag.isdigit() and member is not None:
            limit = int(tag); tag = None

        try:
            use_tag, _ = await self._resolve_tag(ctx, tag, member)
        except commands.UserFeedbackCheckFailure as e:
            return await ctx.send(embed=discord.Embed(title="Missing tag", description=str(e), color=COLOR_WARN))

        client = await self._client()
        try:
            p = await client.get_player(use_tag)
        except BSAPIError as e:
            return await ctx.send(embed=discord.Embed(title="API error", description=str(e), color=COLOR_BAD))
        finally:
            await client.close()

        blist = (p.get("brawlers") or [])
        if not blist:
            return await ctx.send(embed=discord.Embed(title="Brawlers", description="No brawler data available.", color=COLOR_WARN))

        blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
        n = max(1, min(int(limit or 10), 25))
        top = blist[:n]

        lines = []
        thumb_url = None
        total_trophs = 0
        p11_cnt = 0
        for i, b in enumerate(top, 1):
            total_trophs += _safe_int(b.get("trophies", 0))
            if _safe_int(b.get("power", 0)) >= 11:
                p11_cnt += 1
            if thumb_url is None and "id" in b:
                thumb_url = brawler_img_url(b["id"])
            lines.append(
                f"**{i}. {b.get('name','?')}** — {b.get('trophies',0)} 🏆\n"
                f"Power {b.get('power','?')} · Rank {b.get('rank','?')}"
            )

        avg = round(total_trophs / len(top), 1) if top else 0.0

        emb = discord.Embed(
            title=f"{p.get('name','?')}'s Top {n} Brawlers",
            description="\n\n".join(lines),
            color=COLOR_PRIMARY,
        )
        if thumb_url:
            emb.set_thumbnail(url=thumb_url)
        emb.add_field(
            name="Summary",
            value=f"**Avg trophies (top {n}):** {avg}\n**Power 11 in top {n}:** {p11_cnt}",
            inline=False,
        )
        emb.set_footer(text=f"#{use_tag}")
        await ctx.send(embed=emb)

    # =========================
    # BATTLELOG
    # =========================
    @commands.command(name="battlelog")
    @commands.guild_only()
    async def battlelog(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
        tag: Optional[str] = None,
        limit: Optional[int] = 6,
    ):
        if isinstance(tag, str) and tag.isdigit() and member is not None:
            limit = int(tag); tag = None

        try:
            use_tag, _ = await self._resolve_tag(ctx, tag, member)
        except commands.UserFeedbackCheckFailure as e:
            return await ctx.send(embed=discord.Embed(title="Missing tag", description=str(e), color=COLOR_WARN))

        client = await self._client()
        try:
            data = await client.get_player_battlelog(use_tag)
        except BSAPIError as e:
            return await ctx.send(embed=discord.Embed(title="API error", description=str(e), color=COLOR_BAD))
        finally:
            await client.close()

        items = data.get("items", [])[:max(1, min(int(limit or 6), 25))]
        if not items:
            return await ctx.send(embed=discord.Embed(title="Battlelog", description="No recent battles found.", color=COLOR_WARN))

        thumb_url = None
        lines = []
        wins = losses = draws = 0

        for it in items:
            evt = it.get("event") or {}
            btl = it.get("battle") or {}
            mode = (evt.get("mode") or "?").title()
            mapn = evt.get("map", "—")
            res = (btl.get("result") or "—").title()
            tch = btl.get("trophyChange")
            when = _parse_battletime(it.get("battleTime"))
            me = None
            if "teams" in btl:
                me = next((pl for team in btl.get("teams", []) for pl in team if normalize_tag(pl.get("tag","")) == use_tag), None)
            elif "players" in btl:
                me = next((pl for pl in btl.get("players", []) if normalize_tag(pl.get("tag","")) == use_tag), None)

            bname = None
            if me:
                bw = (me.get("brawler") or {})
                bname = bw.get("name")
                if thumb_url is None and "id" in bw:
                    thumb_url = brawler_img_url(bw["id"])

            emoji = "🟩" if res.lower() == "victory" else "🟥" if res.lower() == "defeat" else "🟨"
            if res.lower() == "victory": wins += 1
            elif res.lower() == "defeat": losses += 1
            else: draws += 1

            tchs = f" ({tch:+})" if isinstance(tch, int) else ""
            when_s = f" · {_ago(when)}" if when else ""
            btxt = f" — {bname}" if bname else ""
            lines.append(
                f"{emoji} **{mode}** • *{mapn}* — **{res}**{tchs}{btxt}{when_s}"
            )

        summary = f"**W/L/D:** {wins}/{losses}/{draws}"
        emb = discord.Embed(
            title=f"Recent Battles — #{use_tag}",
            description="\n".join(lines) + "\n\n" + summary,
            color=COLOR_PRIMARY,
        )
        if thumb_url:
            emb.set_thumbnail(url=thumb_url)
        emb.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=emb)
