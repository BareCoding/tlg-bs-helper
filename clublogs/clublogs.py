# clublogs/clublogs.py
from redbot.core import commands, Config
from redbot.core.bot import Red
import discord
from typing import Dict, List, Optional

class ClubLogs(commands.Cog):
    """
    Posts per-club join/leave embeds to channels when ClubSync dispatches updates.
    Also keeps a rolling in-Config history per club for quick viewing.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        # valid hex (avoid 'L')
        self.config = Config.get_conf(self, identifier=0xC10B09, force_registration=True)
        default_guild = {
            "club_logs": {},           # club_tag -> [ {ts, type, player_tag, player_name} ... ]
            "log_max": 500,            # cap per-club stored entries
            "default_log_channel_id": None  # fallback if a club lacks its own channel
        }
        self.config.register_guild(**default_guild)

    # ---------- helpers ----------
    def _mk_embed(self, guild: discord.Guild, payload: Dict) -> discord.Embed:
        event = payload["event"]  # 'join' | 'leave'
        color = discord.Color.green() if event == "join" else discord.Color.red()
        title = "Member Joined" if event == "join" else "Member Left"
        e = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
        e.add_field(name="Player", value=f"{payload.get('player_name') or 'Unknown'} ({payload['player_tag']})", inline=False)
        e.add_field(name="Club", value=payload.get("club_name","Club"), inline=True)
        e.add_field(name="Club Tag", value=f"#{payload.get('club_tag','?')}", inline=True)
        badge_id = payload.get("badge_id") or 0
        if badge_id:
            e.set_thumbnail(url=f"https://cdn.brawlify.com/club/{badge_id}.png")
        e.set_footer(text=guild.name)
        return e

    async def _append_log(self, guild_id: int, club_tag: str, entry: Dict):
        async with self.config.guild_from_id(guild_id).club_logs() as logs:
            arr: List[Dict] = logs.get(club_tag, [])
            arr.append(entry)
            maxlen = (await self.config.guild_from_id(guild_id).log_max()) or 500
            if len(arr) > maxlen:
                arr[:] = arr[-maxlen:]
            logs[club_tag] = arr

    # ---------- admin ----------
    @commands.group()
    async def clublog(self, ctx):
        """Club log utilities."""
        if ctx.invoked_subcommand is None:
            e = discord.Embed(title="ClubLogs", color=discord.Color.blurple(),
                              description=("`[p]clublog setdefault #channel` – default log channel\n"
                                           "`[p]clublog recent <#TAG> [count]` – view recent events\n"
                                           "`[p]clublog setmax <n>` – change stored events cap"))
            await ctx.send(embed=e)

    @clublog.command(name="setdefault")
    @commands.admin()
    async def clublog_setdefault(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).default_log_channel_id.set(channel.id)
        e = discord.Embed(title="Default Log Channel Set", description=f"Logs will default to {channel.mention} when a club has no channel.", color=discord.Color.green())
        await ctx.send(embed=e)

    @clublog.command(name="recent")
    async def clublog_recent(self, ctx, club_tag: str, count: int = 20):
        ctag = club_tag.replace("#","").upper()
        logs = await self.config.guild(ctx.guild).club_logs()
        arr = list(logs.get(ctag, []))[-max(1, min(count, 50)):]
        if not arr:
            e = discord.Embed(title="No Events", description="No entries recorded yet.", color=discord.Color.orange())
            return await ctx.send(embed=e)
        e = discord.Embed(title=f"Recent events for #{ctag}", color=discord.Color.blurple())
        for item in arr:
            sym = "➕" if item["type"] == "join" else "➖"
            e.add_field(
                name=item["ts"],
                value=f"{sym} {item.get('player_name') or 'Unknown'} ({item['player_tag']})",
                inline=False
            )
        await ctx.send(embed=e)

    @clublog.command(name="setmax")
    @commands.admin()
    async def clublog_setmax(self, ctx, max_entries: int):
        await self.config.guild(ctx.guild).log_max.set(max(50, min(max_entries, 5000)))
        e = discord.Embed(title="Stored Events Cap Updated", description=f"Now storing up to **{max(50, min(max_entries, 5000))}** per club.", color=discord.Color.green())
        await ctx.send(embed=e)

    # ---------- event listener ----------
    @commands.Cog.listener()
    async def on_brawl_club_update(self, guild: discord.Guild, payload: Dict):
        """
        Fired by ClubSync on each join/leave.
        payload = { club_tag, club_name, badge_id, event ('join'|'leave'), player_tag, player_name }
        """
        # persist lightweight history
        await self._append_log(
            guild.id,
            payload["club_tag"],
            {
                "ts": discord.utils.utcnow().isoformat(),
                "type": payload["event"],
                "player_tag": payload["player_tag"],
                "player_name": payload.get("player_name")
            }
        )

        # pick channel: per-club log from Clubs, else default
        clubs_cog = self.bot.get_cog("Clubs")
        channel_id: Optional[int] = None
        if clubs_cog:
            clubs_cfg = await clubs_cog.config.guild(guild).clubs()
            c = clubs_cfg.get(payload["club_tag"])
            if c:
                channel_id = c.get("log_channel_id")

        if not channel_id:
            channel_id = await self.config.guild(guild).default_log_channel_id()

        if not channel_id:
            return  # nowhere to post yet

        ch = guild.get_channel(channel_id)
        if not ch:
            return

        emb = self._mk_embed(guild, payload)
        try:
            await ch.send(embed=emb)
        except Exception:
            pass

async def setup(bot: Red):
    await bot.add_cog(ClubLogs(bot))
