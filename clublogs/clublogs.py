# clublogs/clublogs.py
from redbot.core import commands, Config
from redbot.core.bot import Red
import discord

class ClubLogs(commands.Cog):
    """View per-club join/leave logs stored in Config."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xC10L09, force_registration=True)
        default_guild = {"club_logs": {}, "log_max": 500}
        self.config.register_guild(**default_guild)

    @commands.group()
    async def clublog(self, ctx):
        """Club log utilities."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @clublog.command(name="recent")
    async def clublog_recent(self, ctx, club_tag: str, count: int = 20):
        """Show the most recent events for a club."""
        ctag = club_tag.replace("#","").upper()
        logs = await self.config.guild(ctx.guild).club_logs()
        arr = list(logs.get(ctag, []))[-max(1, min(count, 50)):]
        if not arr:
            return await ctx.send("No events yet.")
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
        """Set how many events are stored per club (default 500)."""
        await self.config.guild(ctx.guild).log_max.set(max(50, min(max_entries, 5000)))
        await ctx.send("✅ Updated max stored events per club.")

async def setup(bot: Red):
    await bot.add_cog(ClubLogs(bot))
