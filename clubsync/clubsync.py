# clubsync/clubsync.py
from redbot.core import commands, Config, tasks
from redbot.core.bot import Red
import discord
from typing import Dict, List, Set, Optional
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token

class ClubSync(commands.Cog):
    """Poll clubs, diff members, dispatch join/leave events, and sync roles/nicknames."""

    def __init__(self, bot: Red):
        self.bot = bot
        # 0xC10C10 is valid hex
        self.config = Config.get_conf(self, identifier=0xC10C10, force_registration=True)
        default_guild = {
            "clubs": {},          # club_tag -> {name, role_id, badge_id, log_channel_id, required_trophies, min_slots}
            "rosters": {},        # club_tag -> [#TAG, ...] last snapshot
            "interval_sec": 60,
            "roster_counts": {}   # for onboarding suggestions
        }
        self.config.register_guild(**default_guild)
        self._apis: Dict[int, BrawlStarsAPI] = {}
        self.sync_loop.start()

    def cog_unload(self):
        self.sync_loop.cancel()

    async def _api(self, guild: discord.Guild) -> BrawlStarsAPI:
        token = await get_brawl_api_token(self.bot)
        cli = self._apis.get(guild.id)
        if not cli:
            cli = BrawlStarsAPI(token)
            self._apis[guild.id] = cli
        return cli

    @tasks.loop(seconds=60)
    async def sync_loop(self):
        for guild in self.bot.guilds:
            try:
                interval = await self.config.guild(guild).interval_sec()
                if self.sync_loop.seconds != interval:
                    self.sync_loop.change_interval(seconds=interval)
                await self._sync_guild(guild)
            except Exception:
                continue

    @sync_loop.before_loop
    async def before_sync(self):
        await self.bot.wait_until_red_ready()

    @commands.group()
    async def clubsync(self, ctx):
        """Club sync controls."""
        if ctx.invoked_subcommand is None:
            e = discord.Embed(title="ClubSync", color=discord.Color.blurple(),
                              description="`[p]clubsync interval <seconds>` – set polling interval (min 15s)")
            await ctx.send(embed=e)

    @clubsync.command(name="interval")
    @commands.admin()
    async def clubsync_interval(self, ctx, seconds: int):
        seconds = max(15, seconds)
        await self.config.guild(ctx.guild).interval_sec.set(seconds)
        e = discord.Embed(title="Interval Updated", description=f"Polling every **{seconds}s**.", color=discord.Color.green())
        await ctx.send(embed=e)

    async def _sync_guild(self, guild: discord.Guild):
        api = await self._api(guild)
        gconf = await self.config.guild(guild).all()
        clubs: Dict[str, Dict] = gconf.get("clubs", {})
        rosters: Dict[str, List[str]] = gconf.get("rosters", {})
        new_rosters = {}
        roster_counts = {}

        for ctag, cfg in clubs.items():
            cfg["tag"] = ctag
            try:
                members = await api.get_club_members(ctag)
            except Exception:
                continue

            tags_now: Set[str] = set()
            name_lookup = {}
            for m in members.get("items", []):
                ptag = f"#{api.norm_tag(m.get('tag',''))}"
                tags_now.add(ptag)
                name_lookup[ptag] = m.get("name")
            roster_counts[ctag] = len(tags_now)

            prev = set(rosters.get(ctag, []))
            joined = sorted(tags_now - prev)
            left   = sorted(prev - tags_now)

            # Dispatch events; clublogs cog will post embeds
            for t in joined:
                self.bot.dispatch("brawl_club_update", guild, {
                    "club_tag": ctag,
                    "club_name": cfg.get("name","Club"),
                    "badge_id": cfg.get("badge_id") or 0,
                    "event": "join",
                    "player_tag": t,
                    "player_name": name_lookup.get(t)
                })
            for t in left:
                self.bot.dispatch("brawl_club_update", guild, {
                    "club_tag": ctag,
                    "club_name": cfg.get("name","Club"),
                    "badge_id": cfg.get("badge_id") or 0,
                    "event": "leave",
                    "player_tag": t,
                    "player_name": name_lookup.get(t)
                })

            new_rosters[ctag] = list(tags_now)

        await self.config.guild(guild).rosters.set(new_rosters)
        await self.config.guild(guild).roster_counts.set(roster_counts)

        # Role/nickname sync
        pcog = self.bot.get_cog("Players")
        if not pcog:
            return
        for member in guild.members:
            u = await pcog.config.user(member).all()
            if not u["tags"]:
                continue
            try:
                pdata = await api.get_player(u["tags"][u["default_index"]])
            except Exception:
                continue
            club = pdata.get("club") or {}
            ctag = api.norm_tag(club.get("tag","")) if club.get("tag") else None
            ign  = pdata.get("name") or member.display_name
            if ctag and ctag in clubs:
                desired = f"{ign} | {clubs[ctag]['name']}"
                if guild.me.guild_permissions.manage_nicknames and member.display_name != desired:
                    try: await member.edit(nick=desired, reason="Club sync")
                    except: pass
                role_id = clubs[ctag].get("role_id")
                if role_id:
                    role = guild.get_role(role_id)
                    if role and role not in member.roles:
                        try: await member.add_roles(role, reason="Club sync")
                        except: pass
            else:
                for c in clubs.values():
                    rid = c.get("role_id")
                    if rid:
                        r = guild.get_role(rid)
                        if r and r in member.roles:
                            try: await member.remove_roles(r, reason="Left club")
                            except: pass

async def setup(bot: Red):
    await bot.add_cog(ClubSync(bot))
