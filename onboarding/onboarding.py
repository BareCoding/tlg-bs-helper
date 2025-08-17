# onboarding/onboarding.py
from redbot.core import commands, Config
from redbot.core.bot import Red
import discord
from typing import Optional, Dict, Any, List, Tuple
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import eligible_clubs, tag_pretty

ACCENT  = discord.Color.from_rgb(66,135,245)
SUCCESS = discord.Color.green()
WARN    = discord.Color.orange()
ERROR   = discord.Color.red()

# ---------- UI components ----------

class TagSelect(discord.ui.Select):
    def __init__(self, saved_tags: List[str]):
        options = [discord.SelectOption(label=f"Use {tag_pretty(t)}", value=t) for t in saved_tags]
        options.append(discord.SelectOption(label="Enter a new tag…", value="_new", emoji="✍️"))
        super().__init__(placeholder="Choose a saved tag, or enter a new one…",
                         min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        view: "TagSelectView" = self.view  # type: ignore
        view.choice = self.values[0]
        await interaction.response.defer()
        view.stop()

class TagSelectView(discord.ui.View):
    def __init__(self, author_id: int, saved_tags: List[str], timeout: int = 180):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.choice: Optional[str] = None
        self.add_item(TagSelect(saved_tags))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def on_timeout(self) -> None:
        for c in self.children:
            if hasattr(c, "disabled"):
                c.disabled = True

class ClubPickButton(discord.ui.Button):
    def __init__(self, index: int, label: str):
        super().__init__(style=discord.ButtonStyle.primary, label=f"{index}. {label}")
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: "ClubPickView" = self.view  # type: ignore
        if 1 <= self.index <= len(view.options):
            view.selected = view.options[self.index - 1]
            await interaction.response.defer()
            view.stop()

class ClubPickView(discord.ui.View):
    def __init__(self, author_id: int, options: List[Tuple[str, Dict[str, Any]]], timeout: int = 180):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.options = options[:5]
        self.selected: Optional[Tuple[str, Dict[str, Any]]] = None
        for i, (ctag, cfg) in enumerate(self.options, start=1):
            self.add_item(ClubPickButton(i, cfg["name"]))
        # row 2 controls
        self.add_item(discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, disabled=False))

        # Wire the cancel button
        self.children[-1].callback = self._cancel  # type: ignore

    async def _cancel(self, interaction: discord.Interaction):
        self.selected = None
        await interaction.response.defer()
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def on_timeout(self) -> None:
        for c in self.children:
            if hasattr(c, "disabled"):
                c.disabled = True

# ---------- Cog ----------

class Onboarding(commands.Cog):
    """DM onboarding: confirm/save a tag, show rich eligible-club info, ping leadership on apply."""

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
        """Start the club application wizard in DMs (select menus & buttons)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            try:
                await ctx.author.send(embed=discord.Embed(title="Club Application", description="Let's get you set up in here. 👍", color=ACCENT))
                await ctx.send(embed=discord.Embed(title="Check your DMs", color=ACCENT))
            except discord.Forbidden:
                await ctx.send(embed=discord.Embed(title="Open your DMs", description="Please enable DMs and run `bsstart` again.", color=ERROR))
            return

        mutual = [g for g in self.bot.guilds if g.get_member(ctx.author.id)]
        if not mutual:
            return await ctx.send(embed=discord.Embed(title="No mutual server", color=ERROR))
        guild = mutual[0]
        api = await self._api(guild)

        # Step 1: choose/enter tag
        pcog = self.bot.get_cog("Players")
        chosen_norm: Optional[str] = None

        if pcog:
            u = await pcog.config.user(ctx.author).all()
            saved = [t for t in u["tags"] if t]
            if saved:
                emb = discord.Embed(
                    title="Use an existing tag?",
                    description="Pick one of your saved tags below, or choose **Enter a new tag…**",
                    color=ACCENT
                )
                view = TagSelectView(ctx.author.id, saved)
                msg = await ctx.send(embed=emb, view=view)
                await view.wait()
                await msg.edit(view=None)
                if view.choice is None:
                    return await ctx.send(embed=discord.Embed(title="Timed out", color=ERROR))
                if view.choice != "_new":
                    chosen_norm = view.choice

        if not chosen_norm:
            ask = discord.Embed(title="Your Tag", description="Please send your player tag (e.g. `#ABCD123`).", color=ACCENT)
            await ctx.send(embed=ask)
            def check_tag(m): return m.author.id == ctx.author.id and isinstance(m.channel, discord.DMChannel)
            try:
                raw = await self.bot.wait_for("message", check=check_tag, timeout=180)
            except Exception:
                return await ctx.send(embed=discord.Embed(title="Timed out", color=ERROR))
            chosen_norm = api.norm_tag(raw.content)

        # Validate & save minimal cache
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

        # Step 2: eligible clubs
        clubs_cog = self.bot.get_cog("Clubs")
        clubs_cfg = await clubs_cog.config.guild(guild).clubs() if clubs_cog else {}
        gconf = await self.config.guild(guild).all()
        roster_counts = gconf.get("roster_counts", {})
        options = eligible_clubs(clubs_cfg, trophies, roster_counts)
        if not options:
            return await ctx.send(embed=discord.Embed(title="No eligible clubs right now", color=ERROR))

        # Pull richer info for the top 5 (type, total trophies, description)
        rich: Dict[str, Dict[str, Any]] = {}
        for ctag, _ in options[:5]:
            try:
                cinfo = await api.get_club_by_tag(ctag)
            except Exception:
                cinfo = {}
            rich[ctag] = {
                "type": (cinfo.get("type") or "unknown").title(),
                "trophies": cinfo.get("trophies", 0),
                "desc": (cinfo.get("description") or "")[:140]  # short preview
            }

        # Build pretty list
        lines = []
        for i, (ctag, ccfg) in enumerate(options[:5], start=1):
            members = roster_counts.get(ctag, 0)
            r = rich.get(ctag, {})
            lines.append(
                f"**{i}. {ccfg['name']}**  #{ctag}\n"
                f"• Type: {r.get('type','Unknown')} | Members: {members}/50 | Club Trophies: {r.get('trophies',0):,}\n"
                f"• Required Trophies: {ccfg.get('required_trophies',0):,}\n"
                f"• {r.get('desc','')}"
            )

        emb = discord.Embed(
            title=f"Hi {ign}!",
            description="Pick a club by clicking a button below:\n\n" + "\n\n".join(lines),
            color=ACCENT
        )
        view = ClubPickView(ctx.author.id, options)
        msg2 = await ctx.send(embed=emb, view=view)
        await view.wait()
        await msg2.edit(view=None)
        if view.selected is None:
            return await ctx.send(embed=discord.Embed(title="Cancelled", color=WARN))
        ctag, ccfg = view.selected

        await self.config.member_from_ids(guild.id, ctx.author.id).pending_club_tag.set(ctag)

        # Step 3: notify leadership (ping leadership role if set)
        notify_id = gconf.get("apply_notify_channel_id")
        target = guild.get_channel(notify_id or 0)
        leadership_ping = None
        if clubs_cog:
            tracked = await clubs_cog.config.guild(guild).clubs()
            cfg = tracked.get(ctag) or {}
            if not target and cfg.get("log_channel_id"):
                target = guild.get_channel(cfg.get("log_channel_id"))
            if cfg.get("leadership_role_id"):
                role = guild.get_role(cfg["leadership_role_id"])
                if role:
                    leadership_ping = role.mention

        if target:
            content = leadership_ping or None
            e = discord.Embed(
                title="New Application",
                description=f"**{ign}** ({pdata.get('tag','')}) wants to join **{ccfg['name']}** #{ctag}. Please accept in-game.",
                color=SUCCESS
            )
            await target.send(content=content, embed=e)

        done = discord.Embed(
            title="Next Step",
            description=f"Great! Request to join **{ccfg['name']}** in-game now. I’ll update your roles once you’re in.",
            color=SUCCESS
        )
        await ctx.send(embed=done)

async def setup(bot: Red):
    await bot.add_cog(Onboarding(bot))
