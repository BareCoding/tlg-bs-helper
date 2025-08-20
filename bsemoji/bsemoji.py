from __future__ import annotations
import asyncio
import re
from typing import Dict, List, Optional

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from discord import HTTPException, Forbidden

try:
    import aiohttp
except ImportError:
    aiohttp = None

ACCENT  = discord.Color.from_rgb(66, 135, 245)
SUCCESS = discord.Color.green()
WARN    = discord.Color.orange()
ERROR   = discord.Color.red()

GITHUB_API_LIST = "https://api.github.com/repos/Brawlify/CDN/contents/brawlers/emoji"

NAME_RX = re.compile(r"^[a-z0-9_]{2,32}$")

def _sanitize(name: str) -> str:
    name = name.strip().lower().replace("-", "_")
    name = re.sub(r"[^a-z0-9_]", "", name)
    if len(name) < 2:
        name = f"bs_{name}"
    if len(name) > 32:
        name = name[:32]
    return name

def _too_large(blob: bytes) -> bool:
    return len(blob) > 256 * 1024  # 256KB limit

async def _fetch_json(session: aiohttp.ClientSession, url: str) -> Optional[list]:
    try:
        async with session.get(url, timeout=30, headers={"Accept": "application/vnd.github+json"}) as r:
            if r.status != 200:
                return None
            return await r.json()
    except Exception:
        return None

async def _fetch_bytes(session: aiohttp.ClientSession, url: str) -> Optional[bytes]:
    try:
        async with session.get(url, timeout=30) as r:
            if r.status != 200:
                return None
            return await r.read()
    except Exception:
        return None


class BSEmoji(commands.Cog):
    """Install base brawler emojis from Brawlify CDN as custom emojis (one command)."""

    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB5E10, force_registration=True)
        # Per-guild registry: name -> emoji_id
        self.config.register_guild(registry={})

    # --------------- Commands ---------------

    @commands.group()
    @commands.guild_only()
    @commands.has_guild_permissions(manage_emojis_and_stickers=True)
    async def bsemoji(self, ctx: commands.Context):
        """Manage/import Brawl Stars brawler base emojis in this server."""
        pass

    @bsemoji.command(name="installbrawlers", aliases=["install", "sync"])
    async def installbrawlers(self, ctx: commands.Context, limit: Optional[int] = None, prefix: str = "bs_"):
        """
        Import the base brawler emoji set from the Brawlify CDN repo.

        Usage:
          [p]bsemoji installbrawlers
          [p]bsemoji installbrawlers 40
          [p]bsemoji installbrawlers 45 bs_

        Notes:
          • By default installs ALL base brawler emojis (subject to your server's emoji quota).
          • Use `limit` to cap how many you import (handy if you only have ~50 slots).
          • Emoji names will be `<prefix><file_stem>` (default: bs_shelly, bs_colt, ...).
        """
        if aiohttp is None:
            return await ctx.send("This cog requires `aiohttp`.")

        prefix = _sanitize(prefix)  # ensure safe prefix (may trim)
        if not prefix.endswith("_"):
            prefix += "_"

        async with aiohttp.ClientSession() as session:
            listing = await _fetch_json(session, GITHUB_API_LIST)
            if not isinstance(listing, list):
                return await ctx.send(embed=discord.Embed(
                    title="GitHub API error",
                    description="Couldn’t list `Brawlify/CDN/brawlers/emoji`.",
                    color=ERROR
                ))

            # Build name -> download_url mapping (only .png files)
            pairs: List[tuple[str, str]] = []
            for item in listing:
                if item.get("type") != "file":
                    continue
                name = item.get("name") or ""
                if not name.lower().endswith(".png"):
                    continue
                stem = name.rsplit(".", 1)[0]
                emoname = _sanitize(prefix + stem)
                url = item.get("download_url")
                if not url:
                    # GitHub sometimes omits download_url; construct raw URL as fallback
                    path = item.get("path") or f"brawlers/emoji/{name}"
                    url = f"https://raw.githubusercontent.com/Brawlify/CDN/master/{path}"
                pairs.append((emoname, url))

            # Sort deterministically by name
            pairs.sort(key=lambda p: p[0])

            # Respect limit if provided
            if isinstance(limit, int) and limit > 0:
                pairs = pairs[:limit]

            report = await self._install_many(ctx.guild, pairs, session=session)

        await ctx.send(embed=self._build_report("Brawler Emoji Install", report))

    @bsemoji.command(name="list")
    async def list_(self, ctx: commands.Context):
        """Show emojis recorded by this cog for this server."""
        reg = await self.config.guild(ctx.guild).registry()
        if not reg:
            return await ctx.send("No emojis recorded yet.")
        lines = []
        for name, eid in reg.items():
            e = ctx.guild.get_emoji(eid)
            if e:
                lines.append(f"<:{name}:{eid}> — `:{name}:`")
            else:
                lines.append(f"(missing) `:{name}:` — id {eid}")
        emb = discord.Embed(title="BSEmoji Registry", description="\n".join(lines)[:4000], color=ACCENT)
        await ctx.send(embed=emb)

    @bsemoji.command(name="purge")
    async def purge(self, ctx: commands.Context, confirm: bool = False):
        """Delete only emojis this cog created (names starting with your chosen prefix, default `bs_`)."""
        if not confirm:
            return await ctx.send("Add `true` to confirm: `[p]bsemoji purge true`")
        reg = await self.config.guild(ctx.guild).registry()
        removed = 0
        for name, eid in list(reg.items()):
            e = ctx.guild.get_emoji(eid)
            if not e:
                del reg[name]
                continue
            # Only delete ones we recorded (safe)
            try:
                await e.delete(reason="BSEmoji purge")
                removed += 1
                del reg[name]
            except Exception:
                pass
        await self.config.guild(ctx.guild).registry.set(reg)
        await ctx.send(embed=discord.Embed(
            title="Purge complete",
            description=f"Deleted {removed} emojis created by this cog.",
            color=WARN
        ))

    # --------------- Internals ---------------

    async def _install_many(
        self,
        guild: discord.Guild,
        pairs: List[tuple[str, str]],
        *,
        session: aiohttp.ClientSession
    ) -> Dict[str, str]:
        """
        Returns name -> status:
          ok | exists | download-failed | too-large | no-perms | quota-full | discord-error | invalid-name
        """
        results: Dict[str, str] = {}
        for name, url in pairs:
            name = _sanitize(name)
            if not NAME_RX.match(name):
                results[name] = "invalid-name"
                continue

            # Already exists by name?
            existing = discord.utils.get(guild.emojis, name=name)
            if existing:
                await self._remember(guild, name, existing.id)
                results[name] = "exists"
                continue

            blob = await _fetch_bytes(session, url)
            if not blob:
                results[name] = "download-failed"
                continue
            if _too_large(blob):
                results[name] = "too-large"
                continue

            try:
                emoji = await guild.create_custom_emoji(name=name, image=blob, reason="Managed by bsemoji")
            except Forbidden:
                results[name] = "no-perms"
                continue
            except HTTPException as e:
                msg = str(e).lower()
                if "maximum number" in msg or "maximum number of emojis" in msg or "exceeded" in msg:
                    results[name] = "quota-full"
                else:
                    results[name] = "discord-error"
                continue

            await self._remember(guild, name, emoji.id)
            results[name] = "ok"
            await asyncio.sleep(0.8)  # be nice to rate limits

        return results

    def _build_report(self, title: str, results: Dict[str, str]) -> discord.Embed:
        groups = {
            "Added":                 [k for k, v in results.items() if v == "ok"],
            "Already existed":       [k for k, v in results.items() if v == "exists"],
            "Too large (256KB+)":    [k for k, v in results.items() if v == "too-large"],
            "Download failed":       [k for k, v in results.items() if v == "download-failed"],
            "No permission":         [k for k, v in results.items() if v == "no-perms"],
            "Emoji quota full":      [k for k, v in results.items() if v == "quota-full"],
            "Other Discord errors":  [k for k, v in results.items() if v == "discord-error"],
            "Invalid name":          [k for k, v in results.items() if v == "invalid-name"],
        }
        e = discord.Embed(title=title, color=ACCENT)
        for label, names in groups.items():
            if names:
                e.add_field(name=label, value=", ".join(f":{n}:" for n in names)[:1000], inline=False)
        if not any(groups.values()):
            e.description = "Nothing to do."
        return e

    async def _remember(self, guild: discord.Guild, name: str, eid: int):
        async with self.config.guild(guild).registry() as reg:
            reg[name] = eid


async def setup(bot: Red):
    await bot.add_cog(BSEmoji(bot))
