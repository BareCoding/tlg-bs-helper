# bsPlayers/playerCommands.py
from __future__ import annotations

import datetime as dt
from typing import List, Optional, Tuple, Dict, Any

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

from .api import BrawlStarsAPI, BSAPIError, normalize_tag


# =========================
# Styles / small utils
# =========================
COLOR_PRIMARY = discord.Color.from_rgb(52, 152, 219)   # blue
COLOR_GOOD    = discord.Color.from_rgb(46, 204, 113)   # green
COLOR_WARN    = discord.Color.from_rgb(241, 196, 15)   # yellow
COLOR_BAD     = discord.Color.from_rgb(231, 76, 60)    # red

BULLET_OK  = "🟩"
BULLET_BAD = "🟥"
BULLET_NEU = "🟨"

def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default

def _parse_battletime(s: str) -> Optional[dt.datetime]:
    """
    BS API battleTime is usually ISO (2024-03-19T11:31:47.000Z),
    sometimes compact (20240319T113147.000Z). Return aware UTC.
    """
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

def _fmt_brawler_line(b: dict) -> str:
    """Compact, readable single-line summary for a brawler."""
    name = b.get("name", "?")
    tr   = _safe_int(b.get("trophies", 0))
    pw   = _safe_int(b.get("power", 0))
    rk   = _safe_int(b.get("rank", 0))
    sp   = len(b.get("starPowers") or [])
    gdg  = len(b.get("gadgets") or [])
    gear = len(b.get("gears") or [])
    return f"• **{name}** — {tr} 🏆  ·  ⚡ {pw}  ·  🎖️ {rk}  ·  ⭐ {sp}  ·  🛠️ {gdg}  ·  ⚙️ {gear}"


# =========================
# Cog
# =========================
class PlayerCommands(commands.Cog):
    """Brawl Stars — user commands with polished, consistent embeds."""

    __author__  = "Pat"
    __version__ = "3.2.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB51A11, force_registration=True)
        # per-user data
        self.config.register_user(tags=[])
        # global snapshots for trophy deltas & last seen
        # {TAG: {today_base, today_base_date, week_base, week_base_date, last_trophies, record_high, last_seen}}
        self.config.register_global(stats={})
        # global cache for /brawlers list (to know total brawler count)
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

    async def _update_baselines(
        self,
        tag: str,
        *,
        current_trophies: int,
        latest_battle_time: Optional[dt.datetime],
        personal_best: int,
    ) -> Dict[str, Any]:
        """Create/rotate today/week baselines & store last seen; return merged entry."""
        stats = await self.config.stats()
        entry = stats.get(tag, {})

        now = dt.datetime.now(dt.timezone.utc)
        today_key = now.date().isoformat()
        monday_key = (now - dt.timedelta(days=now.weekday())).date().isoformat()

        if entry.get("today_base_date") != today_key:
            entry["today_base_date"] = today_key
            entry["today_base"] = current_trophies

        if entry.get("week_base_date") != monday_key:
            entry["week_base_date"] = monday_key
            entry["week_base"] = current_trophies

        if latest_battle_time:
            entry["last_seen"] = latest_battle_time.isoformat()

        entry["last_trophies"] = current_trophies
        entry["record_high"] = max(_safe_int(entry.get("record_high", 0)), personal_best, current_trophies)

        stats[tag] = entry
        await self.config.stats.set(stats)
        return entry

    # =========================
    # TAG COMMANDS
    # =========================
    @commands.group(name="tag", invoke_without_command=True)
    @commands.guild_only()
    async def tag_group(self, ctx: commands.Context):
        """Manage your saved Brawl Stars tags."""
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

        emb = discord.Embed(
            title="Tag verified",
            description=f"**{player.get('name','?')}** — `#{tag}`\n🏆 **Trophies:** {player.get('trophies', 0)}",
            color=COLOR_GOOD,
        )
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
    # PLAYER (alias: profile)
    # =========================
    @commands.command(name="player", aliases=["profile"])
    @commands.guild_only()
    async def player(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
        tag: Optional[str] = None,
    ):
        """Detailed player profile with stacked sections and trophy deltas."""
        try:
            use_tag, _ = await self._resolve_tag(ctx, tag, member)
        except commands.UserFeedbackCheckFailure as e:
            return await ctx.send(embed=discord.Embed(title="Missing tag", description=str(e), color=COLOR_WARN))

        client = await self._client()
        try:
            p = await client.get_player(use_tag)
            blog = await client.get_player_battlelog(use_tag)
        except BSAPIError as e:
            return await ctx.send(embed=discord.Embed(title="API error", description=str(e), color=COLOR_BAD))
        finally:
            await client.close()

        # Last seen (from freshest battle)
        latest_time = None
        for item in blog.get("items", [])[:1]:
            latest_time = _parse_battletime(item.get("battleTime"))

        # Brawlers (for stats + top)
        blist = (p.get("brawlers") or [])
        blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
        top_b = blist[0] if blist else None

        catalog = await self._get_brawler_catalog()
        total_brawlers = len(catalog)
        have_cnt = len(blist)
        p11_cnt = sum(1 for b in blist if _safe_int(b.get("power", 0)) >= 11)
        avg_bt = round(sum(_safe_int(b.get("trophies", 0)) for b in blist) / have_cnt, 1) if have_cnt else 0.0

        # Baselines (today/week) + record/last seen
        trophies = _safe_int(p.get("trophies", 0))
        pb = _safe_int(p.get("highestTrophies", 0))
        entry = await self._update_baselines(
            use_tag,
            current_trophies=trophies,
            latest_battle_time=latest_time,
            personal_best=pb,
        )
        today_delta = trophies - _safe_int(entry.get("today_base", trophies))
        week_delta  = trophies - _safe_int(entry.get("week_base", trophies))
        last_seen_txt = _ago(dt.datetime.fromisoformat(entry["last_seen"])) if entry.get("last_seen") else "—"

        name = p.get("name", "?")
        club_name = (p.get("club") or {}).get("name", "—")
        emb = discord.Embed(title=f"{name} (#{use_tag})", color=COLOR_PRIMARY)
        emb.set_author(name=club_name)

        # Overview
        emb.add_field(
            name="Overview",
            value=(
                f"**Trophies:** {trophies}\n"
                f"**Personal Best:** {pb}\n"
                f"**EXP Level:** {p.get('expLevel','?')}\n"
                f"**Last Seen:** {last_seen_txt}"
            ),
            inline=False,
        )
        # Progress
        emb.add_field(
            name="Trophy Progression",
            value=f"**Today:** {today_delta:+}\n**Week:** {week_delta:+}",
            inline=False,
        )
        # Wins
        emb.add_field(
            name="Wins",
            value=(
                f"**3v3:** {p.get('3vs3Victories', 0)}\n"
                f"**Solo:** {p.get('soloVictories', 0)}\n"
                f"**Duo:** {p.get('duoVictories', 0)}"
            ),
            inline=False,
        )
        # Brawlers
        emb.add_field(
            name="Brawlers",
            value=(
                f"**Collected:** {have_cnt}/{total_brawlers}\n"
                f"**Power 11:** {p11_cnt}\n"
                f"**Avg Trophies (owned):** {avg_bt}"
            ),
            inline=False,
        )
        # Top brawler
        if top_b:
            emb.add_field(
                name="Top Brawler",
                value=(
                    f"**{top_b.get('name','?')}** — {top_b.get('trophies',0)} 🏆\n"
                    f"Power {top_b.get('power','?')} · Rank {top_b.get('rank','?')}"
                ),
                inline=False,
            )
            # Short picks row
            picks = []
            for b in blist[:6]:
                picks.append(
                    f"• **{b.get('name','?')}** — {b.get('trophies',0)} 🏆 · "
                    f"P{b.get('power','?')} · R{b.get('rank','?')}"
                )
            emb.add_field(name="Top Picks", value="\n".join(picks), inline=False)

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
        """
        Clean brawler list (one line per brawler), sorted by trophies.
        Default top 10, max 25. Splits across pages if long.
        """
        # allow "15" as third arg when member provided
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

        blist = p.get("brawlers") or []
        if not blist:
            return await ctx.send(embed=discord.Embed(title="Brawlers", description="No brawler data available.", color=COLOR_WARN))

        blist.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
        n = max(1, min(int(limit or 10), 25))
        top = blist[:n]

        lines = [_fmt_brawler_line(b) for b in top]

        title = f"{p.get('name','?')}'s Top {n} Brawlers"
        footer = f"#{use_tag}"

        # Split across multiple embeds if needed (avoid 4k description cap)
        chunks: list[list[str]] = []
        cur, cur_len = [], 0
        for line in lines:
            if cur_len + len(line) + 1 > 3500:
                chunks.append(cur); cur, cur_len = [], 0
            cur.append(line); cur_len += len(line) + 1
        if cur:
            chunks.append(cur)

        for i, chunk in enumerate(chunks, 1):
            emb = discord.Embed(
                title=title if len(chunks) == 1 else f"{title} (Page {i}/{len(chunks)})",
                description="\n".join(chunk),
                color=COLOR_PRIMARY,
            )
            emb.set_footer(text=footer)
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
        """Recent matches in a tidy list with W/L/D, trophy delta, and time-ago."""
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

        # Update "last seen" baseline using freshest item
        latest_time = None
        for item in items[:1]:
            latest_time = _parse_battletime(item.get("battleTime"))
        if latest_time:
            stats = await self.config.stats()
            entry = stats.get(use_tag, {})
            entry["last_seen"] = latest_time.isoformat()
            stats[use_tag] = entry
            await self.config.stats.set(stats)

        wins = losses = draws = 0
        lines: List[str] = []
        for it in items:
            evt = it.get("event") or {}
            btl = it.get("battle") or {}

            mode = (evt.get("mode") or "?").title()
            mapn = evt.get("map", "—")
            res  = (btl.get("result") or "—").title()
            tch  = btl.get("trophyChange")
            tchs = f" ({tch:+})" if isinstance(tch, int) else ""

            when = _parse_battletime(it.get("battleTime"))
            when_s = f" · {_ago(when)}" if when else ""

            # find which brawler the target used
            me = None
            if "teams" in btl:
                me = next((pl for team in btl.get("teams", []) for pl in team if normalize_tag(pl.get("tag","")) == use_tag), None)
            elif "players" in btl:
                me = next((pl for pl in btl.get("players", []) if normalize_tag(pl.get("tag","")) == use_tag), None)
            bname = (me.get("brawler") or {}).get("name") if me else None
            btxt = f" — {bname}" if bname else ""

            if res.lower() == "victory": wins += 1
            elif res.lower() == "defeat": losses += 1
            else: draws += 1

            bullet = BULLET_OK if res.lower() == "victory" else BULLET_BAD if res.lower() == "defeat" else BULLET_NEU
            lines.append(f"{bullet} **{mode}** • *{mapn}* — **{res}**{tchs}{btxt}{when_s}")

        summary = f"**W/L/D:** {wins}/{losses}/{draws}"
        emb = discord.Embed(
            title=f"Recent Battles — #{use_tag}",
            description="\n".join(lines) + "\n\n" + summary,
            color=COLOR_PRIMARY,
        )
        emb.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=emb)

    # =========================
    # COMPARE
    # =========================
    @commands.command(name="compare")
    @commands.guild_only()
    async def compare(self, ctx: commands.Context, first: Optional[str] = None, second: Optional[str] = None):
        """
        Compare two players. Each argument can be a mention or a #TAG.
        If one argument is provided, compares YOU vs that target.

        Usage:
          [p]compare @user
          [p]compare #TAG
          [p]compare @user1 @user2
          [p]compare #TAG1 #TAG2
          [p]compare @user #TAG
        """
        def _is_tag(s: Optional[str]) -> bool:
            return isinstance(s, str) and s.strip().startswith("#")

        def _member_for_position(pos: int) -> Optional[discord.Member]:
            # use actual mentions from the message
            if pos < len(ctx.message.mentions):
                return ctx.message.mentions[pos]
            return None

        async def _tag_for_subject(arg: Optional[str], mention: Optional[discord.Member], default_to_author: bool) -> Tuple[str, Optional[discord.Member]]:
            """Resolve a subject to a tag."""
            if _is_tag(arg):
                return normalize_tag(arg), mention
            if mention:
                tags = await self.config.user(mention).tags()
                if tags:
                    return normalize_tag(tags[0]), mention
                else:
                    raise commands.UserFeedbackCheckFailure(f"No saved tags for **{mention.display_name}**. Ask them to `tag verify #TAG`.")
            if default_to_author:
                tags = await self.config.user(ctx.author).tags()
                if tags:
                    return normalize_tag(tags[0]), ctx.author
                else:
                    raise commands.UserFeedbackCheckFailure(
                        f"You don't have a saved tag. Use `{ctx.clean_prefix}tag verify #YOURTAG`."
                    )
            raise commands.UserFeedbackCheckFailure("Could not resolve a player for comparison.")

        if not first and not second:
            return await ctx.send(embed=discord.Embed(
                title="Compare",
                description="Provide at least one target: `@user` or `#TAG`.\nExamples:\n"
                            f"`{ctx.clean_prefix}compare @user`\n"
                            f"`{ctx.clean_prefix}compare #TAG`\n"
                            f"`{ctx.clean_prefix}compare @user1 @user2`",
                color=COLOR_WARN,
            ))

        if second is None:
            a_tag, a_member = await _tag_for_subject(None, None, default_to_author=True)
            b_tag, b_member = await _tag_for_subject(first, _member_for_position(0), default_to_author=False)
        else:
            a_tag, a_member = await _tag_for_subject(first, _member_for_position(0), default_to_author=False)
            b_tag, b_member = await _tag_for_subject(second, _member_for_position(1), default_to_author=False)

        if a_tag == b_tag:
            return await ctx.send(embed=discord.Embed(
                title="Compare",
                description="Those two resolve to the **same** player. Provide two different targets.",
                color=COLOR_WARN,
            ))

        client = await self._client()
        try:
            A = await client.get_player(a_tag)
            B = await client.get_player(b_tag)
        except BSAPIError as e:
            return await ctx.send(embed=discord.Embed(title="API error", description=str(e), color=COLOR_BAD))
        finally:
            try:
                await client.close()
            except Exception:
                pass

        def _summ(p: Dict[str, Any]) -> Dict[str, Any]:
            bl = p.get("brawlers") or []
            bl.sort(key=lambda b: _safe_int(b.get("trophies", 0)), reverse=True)
            top = bl[0] if bl else None
            return {
                "name": p.get("name", "?"),
                "tag": p.get("tag", ""),
                "club": (p.get("club") or {}).get("name", "—"),
                "trophies": _safe_int(p.get("trophies", 0)),
                "pb": _safe_int(p.get("highestTrophies", 0)),
                "exp": _safe_int(p.get("expLevel", 0)),
                "w3": _safe_int(p.get("3vs3Victories", 0)),
                "ws": _safe_int(p.get("soloVictories", 0)),
                "wd": _safe_int(p.get("duoVictories", 0)),
                "top": (
                    f"{top.get('name')} — { _safe_int(top.get('trophies',0)) } 🏆 · "
                    f"P{_safe_int(top.get('power',0))} · R{_safe_int(top.get('rank',0))}"
                ) if top else "—",
            }

        SA = _summ(A); SB = _summ(B)

        def diff(a: int, b: int) -> str:
            return f"{a - b:+}"

        title = f"Compare — {SA['name']} (#{a_tag}) vs {SB['name']} (#{b_tag})"
        emb = discord.Embed(title=title, color=COLOR_PRIMARY)

        emb.add_field(
            name="Overview A",
            value=(
                f"**Name/Tag:** {SA['name']} — `#{a_tag}`\n"
                f"**Club:** {SA['club']}\n"
                f"**Trophies:** {SA['trophies']}  |  **PB:** {SA['pb']}  |  **EXP:** {SA['exp']}\n"
                f"**Top:** {SA['top']}"
            ),
            inline=False,
        )
        emb.add_field(
            name="Overview B",
            value=(
                f"**Name/Tag:** {SB['name']} — `#{b_tag}`\n"
                f"**Club:** {SB['club']}\n"
                f"**Trophies:** {SB['trophies']}  |  **PB:** {SB['pb']}  |  **EXP:** {SB['exp']}\n"
                f"**Top:** {SB['top']}"
            ),
            inline=False,
        )
        emb.add_field(
            name="Diffs (A − B)",
            value=(
                f"**Trophies:** {diff(SA['trophies'], SB['trophies'])}\n"
                f"**PB:** {diff(SA['pb'], SB['pb'])}\n"
                f"**EXP:** {diff(SA['exp'], SB['exp'])}\n"
                f"**3v3 Wins:** {diff(SA['w3'], SB['w3'])}\n"
                f"**Solo Wins:** {diff(SA['ws'], SB['ws'])}\n"
                f"**Duo Wins:** {diff(SA['wd'], SB['wd'])}"
            ),
            inline=False,
        )

        emb.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=emb)
