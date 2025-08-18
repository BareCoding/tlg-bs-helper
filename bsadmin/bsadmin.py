# cogs/bsadmin/bsadmin.py
from __future__ import annotations

from typing import Dict, List, Optional
import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

ACCENT  = discord.Color.from_rgb(66, 135, 245)
SUCCESS = discord.Color.green()
WARN    = discord.Color.orange()
ERROR   = discord.Color.red()


class BSAdmin(commands.Cog):
    """
    Per-server ACLs for Brawl Stars commands with hierarchical scopes:
      - command: "bs player"
      - group:   "bs"
      - cog:     "BSInfo" (exact cog display name)

    Storage (per-guild):
      allow = {
        "cmd":   { "<qualified name>": [role_id, ...], ... },
        "group": { "<group name>":     [role_id, ...], ... },
        "cog":   { "<cog name>":       [role_id, ...], ... },
      }
    """

    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB5A_D11, force_registration=True)
        self.config.register_guild(
            allow={"cmd": {}, "group": {}, "cog": {}}
        )

    # ---------- Public API used by checks.py ----------
    async def is_allowed(
        self,
        guild: discord.Guild,
        member: discord.Member,
        *,
        cog_name: Optional[str],
        qualified_name: Optional[str],
        group_name: Optional[str],
    ) -> bool:
        """
        Return True if member is allowed by per-guild ACLs.
        Order: command -> group -> cog. If no rule matches, deny here (admins already bypassed in check).
        """
        data = await self.config.guild(guild).allow()
        cmd_map: Dict[str, List[int]] = data.get("cmd", {}) or {}
        grp_map: Dict[str, List[int]] = data.get("group", {}) or {}
        cog_map: Dict[str, List[int]] = data.get("cog", {}) or {}

        def has_any_role(role_ids: List[int]) -> bool:
            return any(r.id in (role_ids or []) for r in member.roles)

        # 1) command-level (lowercased key)
        if qualified_name:
            roles = cmd_map.get(qualified_name.lower(), [])
            if roles and has_any_role(roles):
                return True

        # 2) group-level (lowercased key)
        if group_name:
            roles = grp_map.get(group_name.lower(), [])
            if roles and has_any_role(roles):
                return True

        # 3) cog-level (exact cog display name)
        if cog_name:
            roles = cog_map.get(cog_name, [])
            if roles and has_any_role(roles):
                return True

        return False

    # --------------- Management Commands ---------------

    @commands.group()
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def bsadmin(self, ctx: commands.Context):
        """Manage per-server permissions for Brawl Stars commands."""
        pass

    # ---- ALLOW ----
    @bsadmin.command(name="allowcmd")
    async def allow_cmd(self, ctx: commands.Context, *, qualified_name: str, role: discord.Role):
        """
        Allow a role to run a specific command by its qualified name.
        Example: [p]bsadmin allowcmd bs player @Members
        """
        q = qualified_name.strip().lower()
        async with self.config.guild(ctx.guild).allow() as allow:
            allow.setdefault("cmd", {})
            allow["cmd"].setdefault(q, [])
            if role.id not in allow["cmd"][q]:
                allow["cmd"][q].append(role.id)
        await ctx.send(embed=discord.Embed(
            title="Allowed (command)",
            description=f"{role.mention} → `{q}`",
            color=SUCCESS
        ))

    @bsadmin.command(name="allowgroup")
    async def allow_group(self, ctx: commands.Context, *, group_name: str, role: discord.Role):
        """
        Allow a role to run an entire command group (all its subcommands).
        Example: [p]bsadmin allowgroup bs @Helpers
        """
        g = group_name.strip().lower()
        async with self.config.guild(ctx.guild).allow() as allow:
            allow.setdefault("group", {})
            allow["group"].setdefault(g, [])
            if role.id not in allow["group"][g]:
                allow["group"][g].append(role.id)
        await ctx.send(embed=discord.Embed(
            title="Allowed (group)",
            description=f"{role.mention} → group `{g}`",
            color=SUCCESS
        ))

    @bsadmin.command(name="allowcog")
    async def allow_cog(self, ctx: commands.Context, *, cog_name: str, role: discord.Role):
        """
        Allow a role to run all commands in a cog (exact cog display name).
        Example: [p]bsadmin allowcog BSInfo @Leads
        """
        # Validate to a real cog name to avoid typos
        actual = None
        for c in ctx.bot.cogs.values():
            if getattr(c, "__cog_name__", "") == cog_name:
                actual = cog_name
                break
        if not actual:
            return await ctx.send(embed=discord.Embed(
                title="Unknown cog",
                description=f"`{cog_name}` not found. Use exact cog name (e.g., `BSInfo`).",
                color=ERROR
            ))
        async with self.config.guild(ctx.guild).allow() as allow:
            allow.setdefault("cog", {})
            allow["cog"].setdefault(actual, [])
            if role.id not in allow["cog"][actual]:
                allow["cog"][actual].append(role.id)
        await ctx.send(embed=discord.Embed(
            title="Allowed (cog)",
            description=f"{role.mention} → cog **{actual}**",
            color=SUCCESS
        ))

    # ---- DISALLOW ----
    @bsadmin.command(name="disallowcmd")
    async def disallow_cmd(self, ctx: commands.Context, *, qualified_name: str, role: discord.Role):
        q = qualified_name.strip().lower()
        async with self.config.guild(ctx.guild).allow() as allow:
            roles = (allow.get("cmd", {}) or {}).get(q, [])
            if role.id in roles:
                roles.remove(role.id)
        await ctx.send(embed=discord.Embed(
            title="Disallowed (command)",
            description=f"{role.mention} ← `{q}`",
            color=WARN
        ))

    @bsadmin.command(name="disallowgroup")
    async def disallow_group(self, ctx: commands.Context, *, group_name: str, role: discord.Role):
        g = group_name.strip().lower()
        async with self.config.guild(ctx.guild).allow() as allow:
            roles = (allow.get("group", {}) or {}).get(g, [])
            if role.id in roles:
                roles.remove(role.id)
        await ctx.send(embed=discord.Embed(
            title="Disallowed (group)",
            description=f"{role.mention} ← group `{g}`",
            color=WARN
        ))

    @bsadmin.command(name="disallowcog")
    async def disallow_cog(self, ctx: commands.Context, *, cog_name: str, role: discord.Role):
        actual = None
        for c in ctx.bot.cogs.values():
            if getattr(c, "__cog_name__", "") == cog_name:
                actual = cog_name
                break
        if not actual:
            return await ctx.send(embed=discord.Embed(
                title="Unknown cog",
                description=f"`{cog_name}` not found.",
                color=ERROR
            ))
        async with self.config.guild(ctx.guild).allow() as allow:
            roles = (allow.get("cog", {}) or {}).get(actual, [])
            if role.id in roles:
                roles.remove(role.id)
        await ctx.send(embed=discord.Embed(
            title="Disallowed (cog)",
            description=f"{role.mention} ← cog **{actual}**",
            color=WARN
        ))

    # ---- SHOW / LIST / TEST ----
    @bsadmin.command(name="list")
    async def list_all(self, ctx: commands.Context):
        """List all ACL entries for this server."""
        allow = await self.config.guild(ctx.guild).allow()
        if not any((allow.get("cmd"), allow.get("group"), allow.get("cog"))):
            return await ctx.send("No custom permissions set. By default, only server Admins (and the bot Owner) can run restricted commands.")

        emb = discord.Embed(title="Brawl Stars ACLs", color=ACCENT)
        # Commands
        cmd_lines = []
        for key, ids in (allow.get("cmd") or {}).items():
            mentions = [ctx.guild.get_role(i).mention for i in ids if ctx.guild.get_role(i)]
            cmd_lines.append(f"`{key}` — {', '.join(mentions) if mentions else '—'}")
        emb.add_field(name="Commands", value="\n".join(cmd_lines) or "—", inline=False)

        # Groups
        grp_lines = []
        for key, ids in (allow.get("group") or {}).items():
            mentions = [ctx.guild.get_role(i).mention for i in ids if ctx.guild.get_role(i)]
            grp_lines.append(f"`{key}` — {', '.join(mentions) if mentions else '—'}")
        emb.add_field(name="Groups", value="\n".join(grp_lines) or "—", inline=False)

        # Cogs
        cog_lines = []
        for key, ids in (allow.get("cog") or {}).items():
            mentions = [ctx.guild.get_role(i).mention for i in ids if ctx.guild.get_role(i)]
            cog_lines.append(f"**{key}** — {', '.join(mentions) if mentions else '—'}")
        emb.add_field(name="Cogs", value="\n".join(cog_lines) or "—", inline=False)

        await ctx.send(embed=emb)

    @bsadmin.command(name="show")
    async def show_entry(self, ctx: commands.Context, scope: str, *, name: str):
        """
        Show roles allowed at a specific scope:
          • scope=cmd   name=<qualified name>   e.g.,  bs player
          • scope=group name=<group name>       e.g.,  bs
          • scope=cog   name=<cog name>         e.g.,  BSInfo
        """
        scope = scope.lower().strip()
        allow = await self.config.guild(ctx.guild).allow()

        if scope == "cmd":
            key = name.strip().lower()
            ids = (allow.get("cmd", {}) or {}).get(key, [])
            title = f"Command: `{key}`"
        elif scope == "group":
            key = name.strip().lower()
            ids = (allow.get("group", {}) or {}).get(key, [])
            title = f"Group: `{key}`"
        elif scope == "cog":
            key = name.strip()
            ids = (allow.get("cog", {}) or {}).get(key, [])
            title = f"Cog: **{key}**"
        else:
            return await ctx.send("Scope must be one of: `cmd`, `group`, `cog`.")

        roles = [ctx.guild.get_role(i).mention for i in ids if ctx.guild.get_role(i)] or ["—"]
        emb = discord.Embed(title=title, description=", ".join(roles), color=ACCENT)
        await ctx.send(embed=emb)

    @bsadmin.command(name="can")
    async def can_run(self, ctx: commands.Context, member: Optional[discord.Member] = None, *, command_path: str):
        """
        Test whether someone can run a command (by qualified name).
        Examples:
          [p]bsadmin can @Pat bs player
          [p]bsadmin can bs club
        """
        member = member or ctx.author

        # Build keys for resolution
        cmd = ctx.bot.get_command(command_path)
        if not cmd:
            return await ctx.send(embed=discord.Embed(
                title="Unknown command", description=f"`{command_path}`", color=ERROR
            ))
        qualified = cmd.qualified_name.lower()
        group = (cmd.full_parent_name or "").lower()
        cog_name = getattr(cmd.cog, "__cog_name__", None)

        # Admin/Owner bypass (matches the runtime check)
        if await ctx.bot.is_owner(member) or member.guild_permissions.administrator:
            ok = True
        else:
            ok = await self.is_allowed(ctx.guild, member, cog_name=cog_name, qualified_name=qualified, group_name=group)

        color = SUCCESS if ok else ERROR
        await ctx.send(embed=discord.Embed(
            title="Permission Check",
            description=f"{member.mention} **{'can' if ok else 'cannot'}** run `{qualified}`",
            color=color
        ))


async def setup(bot: Red):
    await bot.add_cog(BSAdmin(bot))
