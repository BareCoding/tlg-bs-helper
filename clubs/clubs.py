# clubs/clubs.py
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
import discord
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import club_badge_url

ACCENT  = discord.Color.from_rgb(66,135,245)
SUCCESS = discord.Color.green()
ERROR   = discord.Color.red()

class Clubs(commands.Cog):
    """Manage tracked clubs (API-driven requirements)."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xC10B5, force_registration=True)
        default_guild = {
            "clubs": {}  # club_tag -> {name, required_trophies, role_id, badge_id, log_channel_id}
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
        pass

    @clubs.command()
    async def add(self, ctx, club_tag: str):
        """Track a club (pulls name, badge, required trophies from API)."""
        api = await self._api(ctx.guild)
        c = await api.get_club_by_tag(club_tag)
        ctag = api.norm_tag(club_tag)
        cfg = {
            "name": c.get("name","Club"),
            "required_trophies": c.get("requiredTrophies", 0),
            "role_id": None,
            "badge_id": (c.get("badgeId") or 0),
            "log_channel_id": None
        }
        async with self.config.guild(ctx.guild).clubs() as clubs:
            clubs[ctag] = cfg
        e = discord.Embed(title="Club Tracked", description=f"**{cfg['name']}** #{ctag}", color=SUCCESS)
        if cfg["badge_id"]:
            e.set_thumbnail(url=club_badge_url(cfg["badge_id"]))
        e.add_field(name="Req. Trophies", value=f"{cfg['required_trophies']:,}")
        await ctx.send(embed=e)

    @clubs.command()
    async def refresh(self, ctx, club_tag: str):
        """Refresh a tracked club's info from the API."""
        api = await self._api(ctx.guild)
        ctag = api.norm_tag(club_tag)
        c = await api.get_club_by_tag(ctag)
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if ctag not in clubs:
                return await ctx.send(embed=discord.Embed(title="Not Tracked", description="Add the club first.", color=ERROR))
            clubs[ctag]["name"] = c.get("name","Club")
            clubs[ctag]["required_trophies"] = c.get("requiredTrophies", 0)
            clubs[ctag]["badge_id"] = (c.get("badgeId") or 0)
        e = discord.Embed(title="Club Refreshed", description=f"**{c.get('name','Club')}** #{ctag}", color=SUCCESS)
        if c.get("badgeId"):
            e.set_thumbnail(url=club_badge_url(c.get("badgeId")))
        e.add_field(name="Req. Trophies", value=f"{c.get('requiredTrophies',0):,}")
        await ctx.send(embed=e)

    @clubs.command()
    async def remove(self, ctx, club_tag: str):
        """Stop tracking a club."""
        ctag = club_tag.replace("#","").upper()
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if ctag in clubs:
                clubs.pop(ctag)
                e = discord.Embed(title="Club Removed", description=f"Stopped tracking #{ctag}.", color=discord.Color.orange())
            else:
                e = discord.Embed(title="Not Tracked", description=f"#{ctag} was not tracked.", color=ERROR)
        await ctx.send(embed=e)

    @clubs.command()
    async def setrole(self, ctx, club_tag: str, role: discord.Role):
        """Set the Discord role for members of this club."""
        ctag = club_tag.replace("#","").upper()
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if ctag not in clubs:
                return await ctx.send(embed=discord.Embed(title="Not Tracked", description="Add the club first.", color=ERROR))
            clubs[ctag]["role_id"] = role.id
        e = discord.Embed(title="Role Set", description=f"Members of #{ctag} get {role.mention}.", color=SUCCESS)
        await ctx.send(embed=e)

    @clubs.command()
    async def setlog(self, ctx, club_tag: str, channel: discord.TextChannel):
        """Set the per-club log channel."""
        ctag = club_tag.replace("#", "").upper()
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if ctag not in clubs:
                return await ctx.send(embed=discord.Embed(title="Not Tracked", description="Add the club first.", color=ERROR))
            clubs[ctag]["log_channel_id"] = channel.id
        e = discord.Embed(title="Log Channel Set", description=f"#{ctag} logs → {channel.mention}", color=SUCCESS)
        await ctx.send(embed=e)

    @clubs.command()
    async def list(self, ctx):
        """List tracked clubs."""
        clubs = await self.config.guild(ctx.guild).clubs()
        if not clubs:
            e = discord.Embed(title="No Clubs", description="Use `[p]clubs add <#TAG>`.", color=discord.Color.orange())
            return await ctx.send(embed=e)
        lines = []
        for k, v in clubs.items():
            lines.append(f"**{v['name']}**  #{k} | req {v['required_trophies']} | role: {'set' if v.get('role_id') else 'unset'} | log: {'set' if v.get('log_channel_id') else 'unset'}")
        e = discord.Embed(title="Tracked Clubs", description="\n".join(lines), color=ACCENT)
        await ctx.send(embed=e)

async def setup(bot: Red):
    await bot.add_cog(Clubs(bot))
