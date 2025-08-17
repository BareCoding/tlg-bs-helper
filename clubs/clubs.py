# clubs/clubs.py
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
import discord
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token

class Clubs(commands.Cog):
    """Manage tracked clubs and thresholds."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xC1UB5, force_registration=True)
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
            await ctx.send_help()

    @clubs.command(name="add")
    async def clubs_add(self, ctx, club_tag: str):
        """Track a club by its tag."""
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
        await ctx.send(f"✅ Added **{cfg['name']}** #{ctag}.")

    @clubs.command(name="remove")
    async def clubs_remove(self, ctx, club_tag: str):
        """Stop tracking a club."""
        ctag = club_tag.replace("#","").upper()
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if ctag in clubs:
                clubs.pop(ctag)
                await ctx.send(f"Removed #{ctag}.")
            else:
                await ctx.send("Not tracked.")

    @clubs.command(name="setrole")
    async def clubs_setrole(self, ctx, club_tag: str, role: discord.Role):
        """Set the Discord role for members of this club."""
        ctag = club_tag.replace("#","").upper()
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if ctag not in clubs: return await ctx.send("Not tracked.")
            clubs[ctag]["role_id"] = role.id
        await ctx.send("✅ Saved role.")

    @clubs.command(name="thresholds")
    async def clubs_thresholds(self, ctx, club_tag: str, requiredtrophies: int, minslots: int):
        """Set trophy requirement and minimum free slots to prioritize."""
        ctag = club_tag.replace("#","").upper()
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if ctag not in clubs: return await ctx.send("Not tracked.")
            clubs[ctag]["required_trophies"] = int(requiredtrophies)
            clubs[ctag]["min_slots"] = int(minslots)
        await ctx.send("✅ Saved thresholds.")

    @clubs.command(name="setlog")
    async def clubs_setlog(self, ctx, club_tag: str, channel: discord.TextChannel):
        """Set the per-club log channel where join/leave embeds will post."""
        ctag = club_tag.replace("#", "").upper()
        async with self.config.guild(ctx.guild).clubs() as clubs:
            if ctag not in clubs:
                return await ctx.send("Not tracked.")
            clubs[ctag]["log_channel_id"] = channel.id
        await ctx.send(f"✅ Log channel for #{ctag} set to {channel.mention}")

    @clubs.command(name="list")
    async def clubs_list(self, ctx):
        """List tracked clubs."""
        clubs = await self.config.guild(ctx.guild).clubs()
        if not clubs:
            return await ctx.send("No clubs tracked yet.")
        lines = [f"**{v['name']}**  #{k} | req {v['required_trophies']} | minslots {v['min_slots']} | log: {('<set>' if v.get('log_channel_id') else '<unset>')}" for k,v in clubs.items()]
        await ctx.send("\n".join(lines))

async def setup(bot: Red):
    await bot.add_cog(Clubs(bot))
