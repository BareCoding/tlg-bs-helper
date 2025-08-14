from __future__ import annotations
import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from ..bs_shared import BrawlStarsAPI, normalize_tag, BSAPIError

class BSAPIAdmin(commands.Cog):
    """Admin & smoke-test commands for the Brawl Stars API."""
    __version__ = "0.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB51AAA, force_registration=True)
        # token is per guild
        self.config.register_guild(token=None)

    # --- helpers ---
    async def _client(self, guild) -> BrawlStarsAPI:
        token = await self.config.guild(guild).token()
        if not token:
            raise commands.UserFeedbackCheckFailure(
                "No API token set. Ask an admin to run `[p]bsapi token <TOKEN>`."
            )
        return BrawlStarsAPI(token)

    # --- command group ---
    @commands.guild_only()
    @commands.group(name="bsapi", invoke_without_command=True)
    async def bsapi(self, ctx: commands.Context):
        """Brawl Stars API admin & test tools."""
        await ctx.send_help()

    # --- admin: set token ---
    @bsapi.command(name="token")
    @commands.admin_or_permissions(manage_guild=True)
    async def bsapi_token(self, ctx: commands.Context, token: str):
        """Set/replace the Brawl Stars API token for this server."""
        await self.config.guild(ctx.guild).token.set(token.strip())
        await ctx.tick()

    # --- tests: player & club ---
    @bsapi.command(name="player")
    async def bsapi_player(self, ctx: commands.Context, tag: str):
        """Fetch a player and show minimal info (smoke test)."""
        client = await self._client(ctx.guild)
        tag = normalize_tag(tag)
        try:
            data = await client.get_player(tag)
        except BSAPIError as e:
            return await ctx.send(f"API error: {e}")
        finally:
            await client.close()
        emb = discord.Embed(title=f"{data.get('name','?')} (#{tag})")
        emb.add_field(name="Trophies", value=data.get("trophies", 0))
        club = (data.get("club") or {}).get("name", "—")
        emb.add_field(name="Club", value=club)
        await ctx.send(embed=emb)

    @bsapi.command(name="club")
    async def bsapi_club(self, ctx: commands.Context, tag: str):
        """Fetch a club and show minimal info (smoke test)."""
        client = await self._client(ctx.guild)
        tag = normalize_tag(tag)
        try:
            data = await client.get_club(tag)
        except BSAPIError as e:
            return await ctx.send(f"API error: {e}")
        finally:
            await client.close()
        emb = discord.Embed(title=f"{data.get('name','?')} (#{tag})")
        emb.add_field(name="Members", value=len(data.get("members", [])))
        emb.add_field(name="Req. Trophies", value=data.get("requiredTrophies", 0))
        await ctx.send(embed=emb)
