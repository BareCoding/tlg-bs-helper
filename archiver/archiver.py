import asyncio
import io
from typing import Optional, List

import discord
from redbot.core import commands, checks, Config


__author__ = "yourname"
__version__ = "1.0.0"


DEFAULT_GUILD = {
    "management_guild_id": 773827710165844008,        # int
    "management_category_id": 1344350295219638363,     # Optional[int]
    "delete_after_archive": True,       # bool
}


class ChannelArchiver(commands.Cog):
    """
    Archive a channel to a management server: create a channel with the same
    name, copy all messages (content + attachments) via a webhook to preserve
    author names/avatars, then delete the original channel.

    ⚠️ Requires bot permissions:
      - Read Message History
      - Manage Webhooks (in destination channel)
      - Manage Channels (to create and delete channels)
      - Send Messages, Embed Links, Attach Files
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xA4C11FEE, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)

    # ---------- Admin setup commands ----------

    @commands.group(name="archiveset")
    @checks.admin_or_permissions(manage_guild=True)
    async def archiveset(self, ctx: commands.Context):
        """Configure where archives go and behavior."""
        if ctx.invoked_subcommand is None:
            data = await self.config.guild(ctx.guild).all()
            mg = data.get("management_guild_id")
            cat = data.get("management_category_id")
            await ctx.send(
                f"Management guild: {mg if mg else 'not set'}\n"
                f"Management category: {cat if cat else 'not set'}\n"
                f"Delete after archive: {data.get('delete_after_archive')}"
            )

    @archiveset.command(name="guild")
    async def archiveset_guild(self, ctx: commands.Context, management_guild_id: int):
        """Set the destination (management) server ID."""
        await self.config.guild(ctx.guild).management_guild_id.set(management_guild_id)
        await ctx.send(f"✅ Set management guild ID to `{management_guild_id}`.")

    @archiveset.command(name="category")
    async def archiveset_category(self, ctx: commands.Context, category_id: Optional[int] = None):
        """Set (or clear) the category ID in the management server for new channels."""
        await self.config.guild(ctx.guild).management_category_id.set(category_id)
        await ctx.send(
            f"✅ Set management category ID to `{category_id}`." if category_id else "✅ Cleared management category."
        )

    @archiveset.command(name="delete")
    async def archiveset_delete(self, ctx: commands.Context, delete_after: bool):
        """Choose whether to delete the original channel after archiving (default: True)."""
        await self.config.guild(ctx.guild).delete_after_archive.set(delete_after)
        await ctx.send(f"✅ Delete after archive set to `{delete_after}`.")

    # ---------- Archive command ----------

    @commands.command(name="archive")
    @checks.mod_or_permissions(manage_channels=True)
    @commands.guild_only()
    async def archive(self, ctx: commands.Context, *, confirm: Optional[str] = None):
        """
        Archive *this* channel to the configured management server.

        Usage: `[p]archive` — prompts for confirmation
               `[p]archive yes` — skip confirmation
        """
        src_channel: discord.TextChannel = ctx.channel
        src_guild: discord.Guild = ctx.guild

        settings = await self.config.guild(src_guild).all()
        mg_id = settings.get("management_guild_id")
        cat_id = settings.get("management_category_id")
        delete_after = settings.get("delete_after_archive", True)

        if not mg_id:
            return await ctx.send("❌ Management guild ID is not set. Use `[p]archiveset guild <id>`.")

        dest_guild = self.bot.get_guild(int(mg_id))
        if not dest_guild:
            return await ctx.send("❌ I am not in the management guild or it is unavailable.")

        if confirm is None or confirm.lower() not in {"y", "yes", "confirm", "--force"}:
            return await ctx.send(
                "⚠️ This will copy all messages & attachments to the management server and "
                + ("**delete this channel**" if delete_after else "**keep this channel**")
                + ".\nType `[p]archive yes` to proceed."
            )

        status_msg = await ctx.send("🚚 Starting archive… this may take a while for large channels.")

        # Create destination text channel with same name
        overwrites = None  # optional: could restrict perms; leaving default of category/server
        dest_category = dest_guild.get_channel(cat_id) if cat_id else None
        try:
            dest_channel = await dest_guild.create_text_channel(name=src_channel.name, category=dest_category, overwrites=overwrites, reason=f"Archive of #{src_channel.name} from {src_guild.name}")
        except discord.Forbidden:
            return await status_msg.edit(content="❌ Missing permission to create channel in management server.")
        except Exception as e:
            return await status_msg.edit(content=f"❌ Failed to create destination channel: {e}")

        # Create a webhook to mirror author name & avatar
        try:
            webhook = await dest_channel.create_webhook(name=f"ArchiveMirror-{src_channel.name}")
        except discord.Forbidden:
            return await status_msg.edit(content="❌ Missing Manage Webhooks in destination channel.")

        # Header message in destination
        await dest_channel.send(
            embed=discord.Embed(
                title="Channel Archived",
                description=(
                    f"Archived from **{src_guild.name}** `{src_guild.id}`\n"
                    f"Source channel: **#{src_channel.name}** `{src_channel.id}`\n"
                    f"Messages are replayed below using a webhook to preserve author names and avatars."
                ),
                color=discord.Color.blurple(),
            ).set_footer(text=f"Started by {ctx.author} ({ctx.author.id})")
        )

        # Copy messages oldest -> newest
        total = 0
        files_in_flight = 0
        try:
            async for message in src_channel.history(limit=None, oldest_first=True):
                # Skip messages from the bot that control the process to reduce noise
                if message.id == status_msg.id:
                    continue

                username = f"{message.author.display_name}"
                avatar_url = message.author.display_avatar.url if message.author.display_avatar else None

                # Build content with timestamp
                ts = discord.utils.format_dt(message.created_at, style='F')
                header = f"[`{ts}`]"
                content = (message.content or "").strip()
                final_text = f"{header} {content}" if content else header

                # Prepare files (download and reupload to avoid remote hotlinks expiring)
                files: List[discord.File] = []
                for att in message.attachments:
                    try:
                        buf = io.BytesIO()
                        await att.save(buf)
                        buf.seek(0)
                        files.append(discord.File(buf, filename=att.filename))
                    except Exception:
                        # Fallback: include URL if download fails
                        final_text += f"\n[Attachment could not be mirrored, original URL]({att.url})"

                # Include embeds as raw dict fallbacks
                embeds: List[discord.Embed] = []
                for em in message.embeds:
                    try:
                        # Best-effort conversion: rebuild basic embed
                        e = discord.Embed.from_dict(em.to_dict())
                        embeds.append(e)
                    except Exception:
                        pass

                # Chunk if content too long for webhook
                async def send_chunk(chunk_text: str):
                    await webhook.send(
                        content=chunk_text or None,
                        username=username,
                        avatar_url=avatar_url,
                        embeds=embeds if embeds else None,
                        files=files if files else None,
                        allowed_mentions=discord.AllowedMentions.none(),
                        wait=True,
                    )

                if final_text and len(final_text) > 2000:
                    # Split on newlines or spaces
                    remaining = final_text
                    first = True
                    while remaining:
                        piece = remaining[:2000]
                        # try not to split mid-word
                        if len(remaining) > 2000:
                            cut = piece.rfind("\n")
                            if cut < 1000:
                                cut = piece.rfind(" ")
                            if cut < 1:
                                cut = 2000
                            piece = remaining[:cut]
                            remaining = remaining[cut:]
                        else:
                            remaining = ""
                        await send_chunk(piece)
                        if first and (files or embeds):
                            # Only attach files/embeds to first chunk
                            files, embeds = [], []
                            first = False
                else:
                    await send_chunk(final_text)

                total += 1
                if total % 50 == 0:
                    try:
                        await status_msg.edit(content=f"📦 Archived {total} messages so far…")
                    except Exception:
                        pass

                await asyncio.sleep(0.1)  # gentle backoff for rate limit safety

        except discord.Forbidden:
            await status_msg.edit(content="❌ I don't have permission to read the channel history.")
            return
        except Exception as e:
            await status_msg.edit(content=f"⚠️ Archive encountered an error after {total} messages: {e}")
            return
        finally:
            try:
                await webhook.delete(reason="Archive complete")
            except Exception:
                pass

        await dest_channel.send(f"✅ Archive complete. Mirrored **{total}** messages.")
        await status_msg.edit(content=f"✅ Archive complete. Mirrored **{total}** messages to {dest_guild.name} → {dest_channel.mention}.")

        # Delete original channel if configured
        if delete_after:
            try:
                await src_channel.delete(reason=f"Archived to {dest_guild.name} by {ctx.author} ({ctx.author.id})")
            except discord.Forbidden:
                await ctx.send("⚠️ Archive succeeded but I couldn't delete the source channel (missing Manage Channels).")
            except Exception as e:
                await ctx.send(f"⚠️ Archive succeeded but failed to delete source channel: {e}")


async def setup(bot):
    await bot.add_cog(ChannelArchiver(bot))
