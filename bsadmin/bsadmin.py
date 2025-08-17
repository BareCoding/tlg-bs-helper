# bsadmin/bsadmin.py
# ---- TLGBS bootstrap: make sibling "brawlcommon" importable on cold start ----
import sys, pathlib
_COGS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(_COGS_DIR) not in sys.path:
    sys.path.insert(0, str(_COGS_DIR))
# ------------------------------------------------------------------------------

from typing import List
import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

ACCENT  = discord.Color.from_rgb(66, 135, 245)
SUCCESS = discord.Color.green()
WARN    = discord.Color.orange()
ERROR   = discord.Color.red()

class BSAdmin(commands.Cog):
    """Central allow-list for TLGBS admin commands across cogs."""

    __version__ = "0.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xAD1A11, force_registration=True)
        default_guild = {"role_ids": [], "user_ids": []}
        self.config.register_guild(**default_guild)

    async def is_authorized(self, guild: discord.Guild, member: discord.Member) -> bool:
        data = await self.config.guild(guild).all()
        role_ids: List[int] = data.get("role_ids", [])
        user_ids: List[int] = data.get("user_ids", [])
        if member.id in user_ids:
            return True
        if any(r.id in role_ids for r in member.roles):
            return True
        p = member.guild_permissions
        return p.manage_guild or p.administrator

    @commands.group()
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def bsadmin(self, ctx: commands.Context):
        """Manage who can use TLGBS admin commands across cogs."""
        pass

    @bsadmin.command(name="allowrole")
    async def allowrole(self, ctx: commands.Context, role: discord.Role):
        async with self.config.guild(ctx.guild).role_ids() as roles:
            if role.id not in roles:
                roles.append(role.id)
        await ctx.send(embed=discord.Embed(
            title="Role allowed", description=f"{role.mention} can now use TLGBS admin commands.", color=SUCCESS
        ))

    @bsadmin.command(name="disallowrole")
    async def disallowrole(self, ctx: commands.Context, role: discord.Role):
        async with self.config.guild(ctx.guild).role_ids() as roles:
            if role.id in roles:
                roles.remove(role.id)
        await ctx.send(embed=discord.Embed(
            title="Role removed", description=f"{role.mention} removed from TLGBS admin allow-list.", color=WARN
        ))

    @bsadmin.command(name="allowuser")
    async def allowuser(self, ctx: commands.Context, member: discord.Member):
        async with self.config.guild(ctx.guild).user_ids() as users:
            if member.id not in users:
                users.append(member.id)
        await ctx.send(embed=discord.Embed(
            title="User allowed", description=f"{member.mention} can now use TLGBS admin commands.", color=SUCCESS
        ))

    @bsadmin.command(name="disallowuser")
    async def disallowuser(self, ctx: commands.Context, member: discord.Member):
        async with self.config.guild(ctx.guild).user_ids() as users:
            if member.id in users:
                users.remove(member.id)
        await ctx.send(embed=discord.Embed(
            title="User removed", description=f"{member.mention} removed from TLGBS admin allow-list.", color=WARN
        ))

    @bsadmin.command(name="list")
    async def list_(self, ctx: commands.Context):
        data = await self.config.guild(ctx.guild).all()
        role_ids = data.get("role_ids", [])
        user_ids = data.get("user_ids", [])
        roles = [f"<@&{rid}>" for rid in role_ids] or ["—"]
        users = [f"<@{uid}>" for uid in user_ids] or ["—"]
        e = discord.Embed(title="TLGBS Admin Allow-List", color=ACCENT)
        e.add_field(name="Roles", value="\n".join(roles), inline=False)
        e.add_field(name="Users", value="\n".join(users), inline=False)
        await ctx.send(embed=e)

    @bsadmin.command(name="test")
    async def test(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        ok = await self.is_authorized(ctx.guild, member)
        color = SUCCESS if ok else ERROR
        await ctx.send(embed=discord.Embed(
            title="Authorization Check",
            description=f"{member.mention} is **{'authorized' if ok else 'NOT authorized'}**.",
            color=color
        ))

async def setup(bot: Red):
    await bot.add_cog(BSAdmin(bot))
