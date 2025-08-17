# onboarding/onboarding.py
from redbot.core import commands, Config
from redbot.core.bot import Red
import discord
from typing import Optional
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import eligible_clubs, tag_pretty

ACCENT  = discord.Color.from_rgb(66,135,245)
SUCCESS = discord.Color.green()
ERROR   = discord.Color.red()

class Onboarding(commands.Cog):
    """DM onboarding: confirm/save a tag, suggest eligible clubs, notify leaders."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xFEEDBEEF, force_registration=True)
        default_guild = {
            "apply_notify_channel_id": None,
            "roster_counts": {}   # updated by ClubSync
        }
        default_member = {"pending_club_tag": None}
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        self._apis = {}

    async def _api(self, guild: discord.Guild) -> BrawlStarsAPI:
        token = await get_brawl_api_token(self.bot)
        cli = self._apis.get(guild.id)
        if not cli:
            cli = BrawlStarsAPI(token)
            self._apis[guild.id] = cli
        return cli

    @commands.group()
    async def onboarding(self, ctx):
        """Onboarding admin config."""
        pass

    @onboarding.command()
    @commands.has_guild_permissions(manage_guild=True)
    async def setnotify(self, ctx, channel: discord.TextChannel):
        """Set the channel where new applications are announced."""
        await self.config.guild(ctx.guild).apply_notify_channel_id.set(channel.id)
        e = discord.Embed(title="Notify channel set", description=f"Applications will be posted in {channel.mention}.", color=SUCCESS)
        await ctx.send(embed=e)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        try:
            e = discord.Embed(
                title="Welcome!",
                description="To apply for a club, reply with your Brawl Stars tag (e.g. `#ABCD123`) or run `bsstart` here anytime.",
                color=ACCENT
            )
            await member.send(embed=e)
        except discord.Forbidden:
            pass

    @commands.command()
    async def bsstart(self, ctx):
        """Start the club application wizard in DMs."""
        if not isinstance(ctx.channel, discord.DMChannel):
            try:
                await ctx.author.send(embed=discord.Embed(title="Club Application", description="What's your player tag? (e.g. `#ABCD123`)", color=ACCENT))
                await ctx.send(embed=discord.Embed(title="Check your DMs", color=ACCENT))
            except discord.Forbidden:
                await ctx.send(embed=discord.Embed(title="Open your DMs", description="Please enable DMs and run `bsstart` again.", color=ERROR))
            return

        mutual = [g for g in self.bot.guilds if g.get_member(ctx.author.id)]
        if not mutual:
            return await ctx.send(embed=discord.Embed(title="No mutual server", color=ERROR))
        guild = mutual[0]
        api = await self._api(guild)
        pcog = self.bot.get_cog("Players")
        chosen_norm: Optional[str] = None

        if pcog:
            u = await pcog.config.user(ctx.author).all()
            if u["tags"]:
                lines = [f"**{i+1}.** {tag_pretty(t)}" for i, t in enumerate(u["tags"], start=1)]
                ask = discord.Embed(
                    title="Use an existing tag?",
                    description="I found these saved tags:\n"
                                + "\n".join(lines)
                                + "\n\nReply with the **number** to use, or type `new` to enter a different tag.",
                    color=ACCENT,
                )
                await ctx.send(embed=ask)

                def check_me(m): return m.author.id == ctx.author.id and isinstance(m.channel, discord.DMChannel)
                try:
                    choice = await self.bot.wait_for("message", check=check_me, timeout=180)
                    content = choice.content.strip().lower()
                    if content.isdigit():
                        idx = int(content) - 1
                        if 0 <= idx < len(u["tags"]):
                            chosen_norm = u["tags"][idx]
                except Exception:
                    return await ctx.send(embed=discord.Embed(title="Timed out", color=ERROR))

        if not chosen_norm:
            await ctx.send(embed=discord.Embed(title="Your Tag", description="Please send your player tag (e.g. `#ABCD123`).", color=ACCENT))
            def check_tag(m): return m.author.id == ctx.author.id and isinstance(m.channel, discord.DMChannel)
            try:
                msg = await self.bot.wait_for("message", check=check_tag, timeout=180)
            except Exception:
                return await ctx.send(embed=discord.Embed(title="Timed out", color=ERROR))
            chosen_norm = api.norm_tag(msg.content)

        try:
            pdata = await api.get_player(chosen_norm)
        except Exception:
            return await ctx.send(embed=discord.Embed(title="Invalid tag", description="I couldn't validate that tag. Please try again.", color=ERROR))

        trophies = pdata.get("trophies", 0)
        ign = pdata.get("name", "Player")
        if pcog:
            async with pcog.config.user(ctx.author).tags() as tags:
                if chosen_norm not in tags and len(tags) < 3:
                    tags.append(chosen_norm)
            await pcog.config.user(ctx.author).ign_cache.set(pdata.get("name") or "")
            club = pdata.get("club") or {}
            await pcog.config.user(ctx.author).club_tag_cache.set((club.get("tag") or "").replace("#",""))

        clubs_cog = self.bot.get_cog("Clubs")
        clubs_cfg = await clubs_cog.config.guild(guild).clubs() if clubs_cog else {}
        gconf = await self.config.guild(guild).all()
        roster_counts = gconf.get("roster_counts", {})
        options = eligible_clubs(clubs_cfg, trophies, roster_counts)
        if not options:
            return await ctx.send(embed=discord.Embed(title="No eligible clubs right now", color=ERROR))

        desc = "\n".join([f"**{i+1}** — {c[1]['name']} (req {c[1].get('required_trophies',0)} trophies)" for i, c in enumerate(options[:5])])
        await ctx.send(embed=discord.Embed(title=f"Hi {ign}!", description="Pick a club by number:\n" + desc, color=ACCENT))

        def check_pick(m): return m.author.id == ctx.author.id and isinstance(m.channel, discord.DMChannel)
        try:
            pick = await self.bot.wait_for("message", check=check_pick, timeout=180)
            idx = int(pick.content) - 1
            ctag, ccfg = options[idx]
        except Exception:
            return await ctx.send(embed=discord.Embed(title="Invalid choice", color=ERROR))

        await self.config.member_from_ids(guild.id, ctx.author.id).pending_club_tag.set(ctag)

        notify_id = gconf.get("apply_notify_channel_id")
        target = guild.get_channel(notify_id or 0)
        if not target and clubs_cog:
            clubs_cfg = await clubs_cog.config.guild(guild).clubs()
            cfg = clubs_cfg.get(ctag)
            if cfg:
                target = guild.get_channel(cfg.get("log_channel_id") or 0)

        if target:
            e = discord.Embed(title="New Application", color=SUCCESS,
                              description=f"**{ign}** ({pdata.get('tag','')}) wants to join **{ccfg['name']}** #{ctag}. Please accept in-game.")
            await target.send(embed=e)

        done = discord.Embed(title="Next Step", description="Great! Request to join that club in-game. I’ll update your roles once you’re in.", color=SUCCESS)
        await ctx.send(embed=done)

async def setup(bot: Red):
    await bot.add_cog(Onboarding(bot))
