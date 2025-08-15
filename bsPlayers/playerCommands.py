# bsPlayers/playerCommands.py
from __future__ import annotations

import datetime as dt
from typing import List, Optional, Tuple, Dict, Any

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

from .api import BrawlStarsAPI, BSAPIError, normalize_tag

# =========================
# Brawlify CDN helpers (numeric IDs from the official API)
# =========================
CDN = "https://cdn.brawlify.com"

def brawler_img_url(brawler_id: int, *, style: str = "borderless") -> str:
    # Valid: "borderless" (recommended) or "borders"
    sub = "borderless" if style == "borderless" else "borders"
    return f"{CDN}/brawlers/{sub}/{int(brawler_id)}.png"

def profile_icon_url(icon_id: int) -> str:
    return f"{CDN}/profile-icons/{int(icon_id)}.png"

def club_badge_url(badge_id: int) -> str:
    return f"{CDN}/club-badges/{int(badge_id)}.png"

# =========================
# Style / utils
# =========================
COLOR_PRIMARY = discord.Color.from_rgb(52, 152, 219)   # blue
COLOR_WARN    = discord.Color.from_rgb(241, 196, 15)   # yellow
COLOR_BAD     = discord.Color.from_rgb(231, 76, 60)    # red

def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default

def _parse_battletime(s: str) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        if "-" in s:
            # "2024-03-19T11:31:47.000Z"
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
        # "20240319T113147.000Z"
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
    """Brawl Stars — single rich `!player` command (alias: !profile) with stacked fields."""

    __author__  = "Pat"
    __version__ = "2.2.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB51A11, force_registration=True)
        # optional per-user saved tags (used by _resolve_tag)
        self.config.register_user(tags=[])
        # global snapshot store for trophy deltas and last seen
        # { TAG: {today_base, today_base_date, week_base, week_base_date, last_trophies, record_high, last_seen} }
        self.config.register_global(stats={})
        # small cache for global brawler catalog (to know total count)
        self._brawler_catalog: Optional[List[Dict[str, Any]]] = None

    # ---------- internals ----------
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

    # =========================
    # !player (alias: !profile)
    # Usage:
    #   [p]player
    #   [p]player @user
    #   [p]player #TAG
    #   [p]player @user #TAG
    # =========================
    @commands.command(name="player", aliases=["profile"])
    @commands.guild_only()
    async def player(
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
            # grab 1 battle to compute "last seen"
            blog = await client.get_player_battlelog(use_tag)
        except BSAPIError as e:
            return await ctx.send(embed=discord.Embed(title="API error", description=str(e), color=COLOR_BAD))
        finally:
            await client.close()

        # ---- Top brawler + summary numbers ----
        blist = (p.get("brawlers") or [])
        blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
        top_b = blist[0] if blist else None

        catalog = await self._get_brawler_catalog()
        total_brawlers = len(catalog)
        have_cnt = len(blist)
        p11_cnt = sum(1 for b in blist if _safe_int(b.get("power", 0)) >= 11)
        avg_bt = round(sum(_safe_int(b.get("trophies", 0)) for b in blist) / have_cnt, 1) if have_cnt else 0.0

        # ---- last seen + baselines for deltas (Today/Week) ----
        latest_time = None
        for item in blog.get("items", [])[:1]:
            latest_time = _parse_battletime(item.get("battleTime"))

        now = dt.datetime.now(dt.timezone.utc)
        today_key = now.date().isoformat()
        monday_key = (now - dt.timedelta(days=now.weekday())).date().isoformat()

        stats = await self.config.stats()
        entry = stats.get(use_tag, {})
        current_trophies = _safe_int(p.get("trophies", 0))
        pb = _safe_int(p.get("highestTrophies", 0))

        # rotate/create baselines if needed
        if entry.get("today_base_date") != today_key:
            entry["today_base_date"] = today_key
            entry["today_base"] = current_trophies
        if entry.get("week_base_date") != monday_key:
            entry["week_base_date"] = monday_key
            entry["week_base"] = current_trophies

        # update last seen + record high
        if latest_time:
            entry["last_seen"] = latest_time.isoformat()
        entry["last_trophies"] = current_trophies
        entry["record_high"] = max(_safe_int(entry.get("record_high", 0)), pb, current_trophies)
        stats[use_tag] = entry
        await self.config.stats.set(stats)

        today_delta = current_trophies - _safe_int(entry.get("today_base", current_trophies))
        week_delta  = current_trophies - _safe_int(entry.get("week_base", current_trophies))
        last_seen_txt = _ago(dt.datetime.fromisoformat(entry["last_seen"])) if entry.get("last_seen") else "—"

        # ---- Embed (stacked sections, no inline) ----
        name = p.get("name", "?")
        icon_id = (p.get("icon") or {}).get("id")
        club = p.get("club") or {}
        club_name = club.get("name", "—")

        emb = discord.Embed(title=f"{name} (#{use_tag})", color=COLOR_PRIMARY)

        # visuals
        if top_b and "id" in top_b:
            emb.set_thumbnail(url=brawler_img_url(top_b["id"]))
        elif icon_id:
            emb.set_thumbnail(url=profile_icon_url(icon_id))
        if club.get("tag"):
            # try to show club badge
            try:
                # club badge id may not be present in player payload
                badge_id = None
                # if you already fetched club above, you can pass its badgeId in;
                # for one-call simplicity we omit a second fetch here.
                # emb.set_author(name=club_name, icon_url=club_badge_url(badge_id))  # if you have it
                emb.set_author(name=club_name)
            except Exception:
                emb.set_author(name=club_name)
        else:
            emb.set_author(name=club_name)

        # Overview
        emb.add_field(
            name="Overview",
            value=(
                f"**Trophies:** {current_trophies}\n"
                f"**Personal Best:** {pb}\n"
                f"**EXP Level:** {p.get('expLevel','?')}\n"
                f"**Last Seen:** {last_seen_txt}"
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

        # Brawlers summary
        emb.add_field(
            name="Brawlers",
            value=(
                f"**Collected:** {have_cnt}/{total_brawlers}\n"
                f"**Power 11:** {p11_cnt}\n"
                f"**Average Trophies (owned):** {avg_bt}"
            ),
            inline=False,
        )

        # Top Brawler card
        if top_b:
            emb.add_field(
                name="Top Brawler",
                value=(
                    f"**{top_b.get('name','?')}** — {top_b.get('trophies',0)} 🏆\n"
                    f"Power {top_b.get('power','?')} · Rank {top_b.get('rank','?')}"
                ),
                inline=False,
            )

        # Optional: show 6 more picks
        if blist:
            picks = []
            for b in blist[:6]:
                picks.append(
                    f"• **{b.get('name','?')}** — {b.get('trophies',0)} 🏆 · "
                    f"P{b.get('power','?')} · R{b.get('rank','?')}"
                )
            emb.add_field(name="Top Picks", value="\n".join(picks), inline=False)

        emb.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=emb)

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
        Show each brawler as a stacked block with CDN-style emoji icons (via custom emoji bindings).
        Use `[p]bsemoji set <key> <emoji>` to bind CDN-uploaded emojis to keys: trophy, power, rank, sp, gadget, gear, coin, pp.
        """
        # numeric 3rd arg as limit when member provided
        if isinstance(tag, str) and tag.isdigit() and member is not None:
            limit = int(tag); tag = None
    
        # resolve tag (explicit > member > author)
        try:
            use_tag, _ = await self._resolve_tag(ctx, tag, member)
        except commands.UserFeedbackCheckFailure as e:
            return await ctx.send(embed=discord.Embed(title="Missing tag", description=str(e), color=discord.Color.orange()))
    
        client = await self._client()
        try:
            p = await client.get_player(use_tag)
        except BSAPIError as e:
            return await ctx.send(embed=discord.Embed(title="API error", description=str(e), color=discord.Color.red()))
        finally:
            await client.close()
    
        blist = p.get("brawlers") or []
        if not blist:
            return await ctx.send(embed=discord.Embed(title="Brawlers", description="No data available.", color=discord.Color.orange()))
    
        # sort & slice
        blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
        n = max(1, min(int(limit or 10), 25))
        data = blist[:n]
    
        # emoji fetchers (use server-bound custom emojis if available)
        TROPHY = await _emo_str(ctx, "trophy")
        POWER  = await _emo_str(ctx, "power")
        RANK   = await _emo_str(ctx, "rank")
        SP     = await _emo_str(ctx, "sp")
        GADG   = await _emo_str(ctx, "gadget")
        GEAR   = await _emo_str(ctx, "gear")
    
        # Build a dense but clean card-style list
        lines: list[str] = []
        thumb_url = None
        for b in data:
            if thumb_url is None and "id" in b:
                # show first brawler as thumbnail
                thumb_url = f"https://cdn.brawlify.com/brawlers/borderless/{int(b['id'])}.png"
    
            name = b.get("name", "?")
            tr = _safe_int(b.get("trophies", 0))
            pb = _safe_int(b.get("highestTrophies", tr))
            pw = _safe_int(b.get("power", 0))
            rk = _safe_int(b.get("rank", 0))
    
            # counts
            sp_count   = len(b.get("starPowers") or [])
            gadg_count = len(b.get("gadgets") or [])
            gear_count = len(b.get("gears") or [])
    
            # Each block: name + line of stats + abilities line
            lines.append(
                f"**{name}**\n"
                f"{TROPHY} {tr}  ·  PB {pb}  ·  {POWER} {pw}  ·  {RANK} {rk}\n"
                f"{SP} {sp_count}  ·  {GADG} {gadg_count}  ·  {GEAR} {gear_count}\n"
            )
    
        # Compose embed
        emb = discord.Embed(
            title=f"{p.get('name','?')} (#{use_tag})",
            description="**Brawler list — Sorted by highest trophies**\n\n" + "\n".join(lines),
            color=discord.Color.from_rgb(52, 152, 219),
        )
        if thumb_url:
            emb.set_thumbnail(url=thumb_url)
        emb.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=emb)
