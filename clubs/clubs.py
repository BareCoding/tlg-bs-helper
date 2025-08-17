# clubs/clubs.py
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
import discord
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import club_badge_url

class Clubs(commands.Cog):
    """Manage tracked clubs and thresholds."""

    def __init__(self, bot: Red):
        self.bot = bot
        # Valid hex (avoid letters beyond A-F)
        self.config = Config.get_conf(self, identifier=0xC10B5, force_registration=True)
        default_guild = {
            "clubs": {}  # club_tag -> {name, required_trophies, min_slots, role_id, badge_id, log_channel_id}
        }
        self.config.register_guild(**default_guild)
        self._apis = {}

    async def _api(self, guild: discord.Guild) -> BrawlStarsAPI:
        token = await get_brawl_api_token(self.bot)
        cli = self._apis.get(guild.id)
        if not cli:
            cli = BrawlStarsAPI(token)
            self._apis[guild.id] = cli
        return cli

    @commands.group()
    @checks.admin()
    async def clubs(self, ctx):
        """Clubs directory admin."""
        if ctx.invoked_subcommand is None:
            e = discord.Embed(title="Clubs Admin", color=discord.Color.blurple(),
                              description=("`[p]clubs add <#TAG>` • track club\n"
                                           "`[p]clubs remove <#TAG>` • untrack club\n"
                                           "`[p]clubs thresholds <#TAG> <req_trophies> <min_slots>`\n"
                                           "`[p]clubs setrole <#TAG> @Role`\n"
                                           "`[p]clubs setlog <#TAG> #channel`\n"
                                           "`[p]clubs list`"))
            await ctx.send(embed=e)

    @clubs.command(name="add")
    async def clubs_add(self, ctx, club_tag: str):
        api = await self._api(ctx.guild)
        c = await api.get_club_by_tag(club_tag)
        ctag = api.norm_tag(club_tag)
        cfg = {
            "name": c.get("name","Club"),
            "required_trophies": c.get("requiredTrophies", 0),
            "min_slots": 1,
            "role_id": None,
            "badge_id": (c.get("badgeId") or 0),
            "log_channel_id": None
        }
        async with self.config.guild(ctx.guild).clubs() as clubs:
            clubs[ctag] = cfg
        e = discord.Embed(title="Club Tracked", description=f"**{cfg['name']}** #{ctag}", color=discord.Color.green())
        if cfg["badge_id"]:
            e.set_thumbnail(url=club_badge_url(cfg["badge_id"]))
        await ctx.send(embed=e)

    @clubs.command(name="remove")
    async def clubs_remove(self, ctx, club_tag: str):
        ctag = club_tag.replace("#","").upper()
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if ctag in clubs:
                clubs.pop(ctag)
                e = discord.Embed(title="Club Removed", description=f"Stopped tracking #{ctag}.", color=discord.Color.orange())
            else:
                e = discord.Embed(title="Not Tracked", description=f"#{ctag} was not tracked.", color=discord.Color.red())
        await ctx.send(embed=e)

    @clubs.command(name="setrole")
    async def clubs_setrole(self, ctx, club_tag: str, role: discord.Role):
        ctag = club_tag.replace("#","").upper()
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if ctag not in clubs:
                return await ctx.send(embed=discord.Embed(title="Not Tracked", description="Add the club first.", color=discord.Color.red()))
            clubs[ctag]["role_id"] = role.id
        e = discord.Embed(title="Role Set", description=f"Members of #{ctag} get {role.mention}.", color=discord.Color.green())
        await ctx.send(embed=e)

    @clubs.command(name="thresholds")
    async def clubs_thresholds(self, ctx, club_tag: str, requiredtrophies: int, minslots: int):
        ctag = club_tag.replace("#","").upper()
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if ctag not in clubs:
                return await ctx.send(embed=discord.Embed(title="Not Tracked", description="Add the club first.", color=discord.Color.red()))
            clubs[ctag]["required_trophies"] = int(requiredtrophies)
            clubs[ctag]["min_slots"] = int(minslots)
        e = discord.Embed(title="Thresholds Updated", description=f"#{ctag}: req {requiredtrophies}, min slots {minslots}", color=discord.Color.green())
        await ctx.send(embed=e)

    @clubs.command(name="setlog")
    async def clubs_setlog(self, ctx, club_tag: str, channel: discord.TextChannel):
        ctag = club_tag.replace("#", "").upper()
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if ctag not in clubs:
                return await ctx.send(embed=discord.Embed(title="Not Tracked", description="Add the club first.", color=discord.Color.red()))
            clubs[ctag]["log_channel_id"] = channel.id
        e = discord.Embed(title="Log Channel Set", description=f"#{ctag} logs will go to {channel.mention}.", color=discord.Color.green())
        await ctx.send(embed=e)

    @clubs.command(name="list")
    async def clubs_list(self, ctx):
        clubs = await self.config.guild(ctx.guild).clubs()
        if not clubs:
            e = discord.Embed(title="No Clubs", description="Use `[p]clubs add <#TAG>` to track a club.", color=discord.Color.orange())
            return await ctx.send(embed=e)
        lines = []
        for k, v in clubs.items():
            lines.append(f"**{v['name']}**  #{k} | req {v['required_trophies']} | minslots {v['min_slots']} | log: {'set' if v.get('log_channel_id') else 'unset'}")
        e = discord.Embed(title="Tracked Clubs", description="\n".join(lines), color=discord.Color.blurple())
        await ctx.send(embed=e)

async def setup(bot: Red):
    await bot.add_cog(Clubs(bot))
