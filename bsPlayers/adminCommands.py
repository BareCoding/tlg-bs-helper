# bsPlayers/adminCommands.py
from __future__ import annotations

import discord
from redbot.core import commands
from .api import BrawlStarsAPI, BSAPIError, normalize_tag


class AdminCommands(commands.Cog):
    """Admin commands for Brawl Stars"""

    def __init__(self, bot):
        self.bot = bot

    # ----- helpers -----
    async def _client(self) -> BrawlStarsAPI:
        # Token is fetched from Red's shared API tokens:
        #   [p]set api brawlstars api_key,YOURTOKEN
        return BrawlStarsAPI(self.bot)

    def _player_cog(self):
        pcog = self.bot.get_cog("PlayerCommands")
        if not pcog:
            raise commands.UserFeedbackCheckFailure("PlayerCommands cog is not loaded.")
        return pcog

    # ----- commands -----
    @commands.group(name="atag", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def atag_group(self, ctx: commands.Context):
        """Admin tag management"""
        await ctx.send_help()

    @atag_group.command(name="verify")
    @commands.admin_or_permissions(manage_guild=True)
    async def atag_verify(self, ctx: commands.Context, user: discord.Member, tag: str):
        """Verify a tag via API and save it for the specified user."""
        tag = normalize_tag(tag)
        client = await self._client()
        try:
            await client.get_player(tag)  # validate tag
        except BSAPIError as e:
            return await ctx.send(f"❌ Invalid tag: {e}")
        finally:
            await client.close()

        pcog = self._player_cog()
        async with pcog.config.user(user).tags() as tags:
            if tag not in tags:
                tags.append(tag)
        await ctx.send(f"✅ Tag **#{tag}** saved for **{user.display_name}**")

    @atag_group.command(name="remove")
    @commands.admin_or_permissions(manage_guild=True)
    async def atag_remove(self, ctx: commands.Context, user: discord.Member, tag: str):
        """Remove a saved tag from the specified user."""
        tag = normalize_tag(tag)
        pcog = self._player_cog()
        async with pcog.config.user(user).tags() as tags:
            if tag in tags:
                tags.remove(tag)
                return await ctx.send(f"🗑️ Removed **#{tag}** for **{user.display_name}**")
        await ctx.send("That tag isn't saved.")

    @commands.command(name="acheckapi")
    @commands.admin_or_permissions(manage_guild=True)
    async def acheckapi(self, ctx: commands.Context):
        """
        Check if the Brawl Stars API token is configured and working.
        Token must be set using: [p]set api brawlstars api_key,YOURTOKEN
        """
        client = await self._client()
        try:
            # simple smoke test: global brawler catalog
            await client.list_brawlers()
        except BSAPIError as e:
            return await ctx.send(
                "❌ API not working. Make sure the key is set with "
                "`[p]set api brawlstars api_key,YOURTOKEN` and the droplet IP is allow-listed.\n"
                f"Details: {e}"
            )
        finally:
            await client.close()

        await ctx.send("✅ API is working.")
