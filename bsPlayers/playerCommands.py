# bsPlayers/playerCommands.py
from __future__ import annotations

import asyncio
import datetime as dt
from typing import List, Optional, Tuple, Dict, Any

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

from .api import BrawlStarsAPI, BSAPIError, normalize_tag


# ============== CDN helpers (Brawlify) ==============
CDN = "https://cdn.brawlify.com"

def brawler_img_url(brawler_id: int, *, style: str = "borderless") -> str:
    # Valid styles: "borderless" (recommended), "borders"
    sub = "borderless" if style == "borderless" else "borders"
    return f"{CDN}/brawlers/{sub}/{int(brawler_id)}.png"

def profile_icon_url(icon_id: int) -> str:
    return f"{CDN}/profile-icons/{int(icon_id)}.png"

def club_badge_url(badge_id: int) -> str:
    return f"{CDN}/club-badges/{int(badge_id)}.png"


# ============== misc helpers ==============
COLOR_PRIMARY = discord.Color.from_rgb(52, 152, 219)   # blue
COLOR_GOOD    = discord.Color.from_rgb(46, 204, 113)   # green
COLOR_WARN    = discord.Color.from_rgb(241, 196, 15)   # yellow
COLOR_BAD     = discord.Color.from_rgb(231, 76, 60)    # red
ZWS = "\u200b"

def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default

def _parse_battletime(s: str) -> Optional[dt.datetime]:
    # BS API format: "2024-03-19T11:31:47.000Z" or "20240319T113147.000Z"
    if not s:
        return None
    try:
        if "-" in s:
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
        # legacy compact form
        return dt.datetime.strptime(s, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None

def _humanize_delta(delta: dt.timedelta) -> str:
    secs = int(abs(delta.total_seconds()))
    if secs < 60:        return f"{secs}s"
    if secs < 3600:      return f"{secs//60}m"
    if secs < 86400:     return f"{secs//3600}h"
    if secs < 86400*14:  return f"{secs//86400}d"
    return f"{secs//(86400*7)}w"


# ============== Cog ==============
class PlayerCommands(commands.Cog):
    """Player commands for Brawl Stars: tag management, profile, brawlers, battlelog + snapshots."""

    __author__  = "Pat"
    __version__ = "2.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB51A11, force_registration=True)
        # user scope: saved tags
        self.config.register_user(tags=[])
        # global scope: snapshots & catalog cache
        self.config.register_global(
            stats={},                   # {tag: {last_seen, record_high, today_base, week_base, last_trophies}}
        )
        self._catalog: Optional[Dict[str, Any]] = None  # cached /brawlers result
        self._snap_task = self.bot.loop.create_task(self._snapshot_loop())

    def cog_unload(self):
        try:
            self._snap_task.cancel()
        except Exception:
            pass

    # ------ internal clients/helpers ------
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

    async def _get_brawler_catalog(self) -> Dict[str, Any]:
        # Cache the global /brawlers (mainly for counts)
        if self._catalog is None:
            client = await self._client()
            try:
                data = await client.list_brawlers()
            finally:
                await client.close()
            # official returns {"items":[...]} ; normalize
            items = data.get("items", data) if isinstance(data, dict) else data
            self._catalog = {"items": items or []}
        return self._catalog

    # ------ background snapshot loop ------
    async def _snapshot_loop(self):
        await asyncio.sleep(5)  # give bot time to finish loading
        while True:
            try:
                await self._take_snapshots()
            except asyncio.CancelledError:
                break
            except Exception:
                # keep going even if one iteration fails
                pass
            # run roughly hourly
            await asyncio.sleep(3600)

    async def _take_snapshots(self):
        # Gather all unique normalized tags across users
        all_users = await self.config.all_users()
        seen: set[str] = set()
        for _, udata in all_users.items():
            for t in udata.get("tags", []):
                seen.add(normalize_tag(t))

        if not seen:
            return

        stats = await self.config.stats()
        now = dt.datetime.now(dt.timezone.utc)
        today_key = now.date().isoformat()
        week_ago = now - dt.timedelta(days=7)

        client = await self._client()
        try:
            for tag in list(seen)[:1000]:
                # fetch player (and a tiny bit of battlelog for last seen)
                try:
                    p = await client.get_player(tag)
                    bl = await client.get_player_battlelog(tag)
                except BSAPIError:
                    continue

                entry = stats.get(tag, {})  # mutable copy ok
                cur_trophies = _safe_int(p.get("trophies", 0))
                # record high
                rh = _safe_int(p.get("highestTrophies", 0))
                entry["record_high"] = max(_safe_int(entry.get("record_high", 0)), rh, cur_trophies)

                # last seen from battlelog's newest time
                latest_time = None
                for item in bl.get("items", [])[:1]:
                    t = _parse_battletime(item.get("battleTime"))
                    if t:
                        latest_time = t
                        break
                if latest_time:
                    entry["last_seen"] = latest_time.isoformat()

                # keep a "today" and "week" baseline
                # set baseline once per (day/week) if not present
                # (simple scheme; rotates when day/week flips)
                # base today
                last_today = entry.get("today_base_date")
                if last_today != today_key:
                    entry["today_base_date"] = today_key
                    entry["today_base"] = cur_trophies
                # base week (store monday-of-week key)
                monday = (now - dt.timedelta(days=now.weekday())).date().isoformat()
                if entry.get("week_base_date") != monday:
                    entry["week_base_date"] = monday
                    entry["week_base"] = cur_trophies

                entry["last_trophies"] = cur_trophies
                stats[tag] = entry

                # be nice to rate limits
                await asyncio.sleep(0.2)
        finally:
            await self.config.stats.set(stats)
            await client.close()

    # ========== TAG GROUP ==========
    @commands.group(name="tag", invoke_without_command=True)
    @commands.guild_only()
    async def tag_group(self, ctx: commands.Context):
        """Manage your saved Brawl Stars tags."""
        emb = discord.Embed(
            title="Tag commands",
            description=(
                f"`{ctx.clean_prefix}tag verify #TAG` – save a verified tag\n"
                f"`{ctx.clean_prefix}tag remove #TAG` – remove a saved tag\n"
                f"`{ctx.clean_prefix}tag list [@user]` – list saved tags"
            ),
            color=COLOR_PRIMARY,
        )
        await ctx.send(embed=emb)

    @tag_group.command(name="verify")
    async def tag_verify(self, ctx: commands.Context, tag: str):
        """Verify a tag against the API and save it to your account."""
        tag = normalize_tag(tag)
        client = await self._client()
        try:
            pdata = await client.get_player(tag)
        except BSAPIError as e:
            emb = discord.Embed(title="Tag verification failed", description=str(e), color=COLOR_BAD)
            return await ctx.send(embed=emb)
        finally:
            await client.close()

        async with self.config.user(ctx.author).tags() as tags:
            if tag not in tags:
                tags.append(tag)

        # player profile icon if present
        icon_id = (pdata.get("icon") or {}).get("id")
        emb = discord.Embed(
            title="Tag verified",
            description=f"**{pdata.get('name','?')}** — `#{tag}`\n🏆 Trophies: **{pdata.get('trophies',0)}**",
            color=COLOR_GOOD,
        )
        if icon_id:
            emb.set_thumbnail(url=profile_icon_url(icon_id))
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
            title="Tag removed" if removed else "Tag not found",
            description=(f"Removed **#{tag}**." if removed else "That tag isn’t saved on your account."),
            color=(COLOR_GOOD if removed else COLOR_WARN),
        )
        await ctx.send(embed=emb)

    @tag_group.command(name="list")
    async def tag_list(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        """List saved tags for you or the mentioned user."""
        user = user or ctx.author
        tags: List[str] = await self.config.user(user).tags()
        if not tags:
            emb = discord.Embed(title="No tags", description=f"No tags saved for **{user.display_name}**.", color=COLOR_WARN)
            return await ctx.send(embed=emb)
        emb = discord.Embed(
            title=f"{user.display_name}'s tags",
            description="\n".join(f"• `#{t}`" for t in tags),
            color=COLOR_PRIMARY,
        )
        await ctx.send(embed=emb)

    # ========== PROFILE ==========
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
            use_tag, _ = await self._resolve_tag(ctx, tag, member)
        except commands.UserFeedbackCheckFailure as e:
            return await ctx.send(embed=discord.Embed(title="Missing tag", description=str(e), color=COLOR_WARN))

        client = await self._client()
        p = None
        club_badge_id = None
        try:
            p = await client.get_player(use_tag)
            # optional: try to fetch club badge for nicer author icon
            club = p.get("club") or {}
            if club.get("tag"):
                try:
                    cdata = await client.get_club(club["tag"])
                    club_badge_id = cdata.get("badgeId")
                except BSAPIError:
                    pass
        except BSAPIError as e:
            return await ctx.send(embed=discord.Embed(title="API error", description=str(e), color=COLOR_BAD)))
        finally:
            await client.close()

        # Top brawler + catalog counts
        blist = (p.get("brawlers") or [])
        blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
        top_b = blist[0] if blist else None
        cat = await self._get_brawler_catalog()
        total_brawlers = len(cat.get("items", []))

        # Snapshots
        stats = await self.config.stats()
        s = stats.get(use_tag, {})
        trophies = _safe_int(p.get("trophies", 0))
        today_delta = trophies - _safe_int(s.get("today_base", trophies))
        week_delta  = trophies - _safe_int(s.get("week_base", trophies))

        # Last seen
        last_seen = s.get("last_seen")
        ls_txt = "—"
        if last_seen:
            try:
                t = dt.datetime.fromisoformat(last_seen)
                delta = dt.datetime.now(dt.timezone.utc) - t
                ls_txt = f"{_humanize_delta(delta)} ago"
            except Exception:
                pass

        # Build embed
        name = p.get("name", "?")
        icon_id = (p.get("icon") or {}).get("id")
        eb = discord.Embed(
            title=f"{name} (#{use_tag})",
            color=COLOR_PRIMARY,
        )
        if top_b and "id" in top_b:
            eb.set_thumbnail(url=brawler_img_url(top_b["id"]))

        if club_badge_id:
            eb.set_author(name=(p.get("club") or {}).get("name", "—"), icon_url=club_badge_url(club_badge_id))

        # Row 1: Club + Last seen
        eb.add_field(name="Club", value=(p.get("club") or {}).get("name", "—"), inline=True)
        eb.add_field(name="Last Seen", value=ls_txt, inline=True)
        eb.add_field(name=ZWS, value=ZWS, inline=True)

        # Row 2: Core trophies info
        eb.add_field(name="Trophies", value=f"{trophies}", inline=True)
        eb.add_field(name="Personal Best", value=f"{p.get('highestTrophies', 0)}", inline=True)
        eb.add_field(name="EXP Level", value=f"{p.get('expLevel', '?')}", inline=True)

        # Row 3: Progression
        d_today = f"{today_delta:+}" if today_delta else "0"
        d_week  = f"{week_delta:+}" if week_delta else "0"
        eb.add_field(name="Progression", value=f"Today {d_today}\nWeek {d_week}", inline=True)
        eb.add_field(name="Season End", value="—", inline=True)  # requires season schedule; placeholder
        eb.add_field(name=ZWS, value=ZWS, inline=True)

        # Row 4: Wins
        eb.add_field(name="3v3 Wins", value=f"{p.get('3vs3Victories', 0)}", inline=True)
        eb.add_field(name="Solo Wins", value=f"{p.get('soloVictories', 0)}", inline=True)
        eb.add_field(name="Duo Wins", value=f"{p.get('duoVictories', 0)}", inline=True)

        # Row 5: Brawlers overview
        have_cnt = len(blist)
        eb.add_field(
            name="Brawlers",
            value=f"{have_cnt}/{total_brawlers} collected",
            inline=True,
        )
        if top_b:
            eb.add_field(
                name="Top Brawler",
                value=f"**{top_b.get('name','?')}** — {top_b.get('trophies',0)} 🏆\n"
                      f"Power {top_b.get('power','?')} | Rank {top_b.get('rank','?')}",
                inline=True,
            )
        else:
            eb.add_field(name="Top Brawler", value="—", inline=True)
        eb.add_field(name=ZWS, value=ZWS, inline=True)

        # Footer/thumb
        if icon_id and not top_b:
            eb.set_thumbnail(url=profile_icon_url(icon_id))
        eb.set_footer(text=f"Requested by {ctx.author.display_name}")

        await ctx.send(embed=eb)

    # ========== BRAWLERS ==========
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
        # allow numeric third arg as limit when member is provided
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
            return await ctx.send(embed=discord.Embed(title="Top Brawlers", description="No brawler data available.", color=COLOR_WARN))

        blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
        n = max(1, min(int(limit or 10), 25))
        top = blist[:n]

        # Build a clean, compact block
        lines = []
        thumb_url = None
        for i, b in enumerate(top, 1):
            if thumb_url is None and "id" in b:
                thumb_url = brawler_img_url(b["id"])
            lines.append(
                f"**{i}. {b.get('name','?')}** — {b.get('trophies',0)} 🏆  ·  "
                f"Power {b.get('power','?')}  ·  Rank {b.get('rank','?')}"
            )

        eb = discord.Embed(
            title=f"{p.get('name','?')}'s Top Brawlers",
            description="\n".join(lines),
            color=COLOR_PRIMARY,
        )
        if thumb_url:
            eb.set_thumbnail(url=thumb_url)
        eb.set_footer(text=f"#{use_tag}")
        await ctx.send(embed=eb)

    # ========== BATTLELOG ==========
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

        items = data.get("items", [])[: max(1, min(int(limit or 5), 25))]
        if not items:
            return await ctx.send(embed=discord.Embed(title="Battlelog", description="No recent battles found.", color=COLOR_WARN))

        # derive a "first item" brawler image if present
        thumb_url = None
        lines = []
        for it in items:
            evt = it.get("event") or {}
            btl = it.get("battle") or {}

            mode = (evt.get("mode") or "?").title()
            mapn = evt.get("map", "—")
            res  = (btl.get("result") or "—").title()
            tch  = btl.get("trophyChange")
            tchs = f" ({tch:+})" if isinstance(tch, int) else ""
            when = _parse_battletime(it.get("battleTime"))
            when_s = f" · {_humanize_delta(dt.datetime.now(dt.timezone.utc) - when)} ago" if when else ""

            # find our player to show the played brawler
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

            result_emoji = "🟩" if res.lower() == "victory" else "🟥" if res.lower() == "defeat" else "🟨"
            btxt = f" — {bname}" if bname else ""
            lines.append(f"{result_emoji} **{mode}** • *{mapn}* — **{res}**{tchs}{btxt}{when_s}")

        eb = discord.Embed(
            title=f"Recent Battles — #{use_tag}",
            description="\n".join(lines),
            color=COLOR_PRIMARY,
        )
        if thumb_url:
            eb.set_thumbnail(url=thumb_url)
        eb.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=eb)
