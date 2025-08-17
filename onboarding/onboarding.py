# onboarding/onboarding.py
# ---- TLGBS bootstrap: make sibling "brawlcommon" importable on cold start ----
import sys, pathlib
_COGS_DIR = pathlib.Path(__file__).resolve().parents[1]  # .../cogs
if str(_COGS_DIR) not in sys.path:
    sys.path.insert(0, str(_COGS_DIR))
# ------------------------------------------------------------------------------

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
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, disabled=False)
        cancel.callback = self._cancel  # type: ignore
        self.add_item(cancel)

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
    """Application flow in guild: confirm/save tag, show eligible clubs, ping leadership."""

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
        self._apis: Dict[int, BrawlStarsAPI] = {}

    async def _api(self, guild: discord.Guild) -> BrawlStarsAPI:
        token = await get_brawl_api_token(self.bot)
        cli = self._apis.get(guild.id)
        if not cli:
            cli = BrawlStarsAPI(token)
            self._apis[guild.id] = cli
        return cli

    # Public entrypoint called by bsinfo: !bs start
    async def start_application(self, ctx: commands.Context):
        """Runs the full application flow in the current guild channel."""
        guild = ctx.guild
        if guild is None:
            return await ctx.send(embed=discord.Embed(title="Run this in a server", color=ERROR))
        api = await self._api(guild)

        # Use bsinfo as tag store
        bscog = self.bot.get_cog("BSInfo")
        # STEP 1: choose or enter tag
        saved = []
        if bscog:
            u = await bscog.config.user(ctx.author).all()
            saved = [t for t in u["tags"] if t]

        chosen_norm: Optional[str] = None
        if saved:
            emb = discord.Embed(
                title="Use an existing tag?",
                description="Pick one of your saved tags below, or choose **Enter a new tag…**",
                color=ACCENT
            )
            view = TagSelectView(ctx.author.id, saved)
            msg = await ctx.send(embed=emb, view=view)
            await view.wait()
            try: await msg.edit(view=None)
            except: pass
            if view.choice is None:
                return await ctx.send(embed=discord.Embed(title="Timed out", color=ERROR))
            if view.choice != "_new":
                chosen_norm = view.choice

        if not chosen_norm:
            ask = discord.Embed(title="Your Tag", description="Reply with your player tag (e.g. `#ABCD123`).", color=ACCENT)
            await ctx.send(embed=ask)

            def check_tag(m): return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id
            try:
                raw = await self.bot.wait_for("message", check=check_tag, timeout=180)
            except Exception:
                return await ctx.send(embed=discord.Embed(title="Timed out", color=ERROR))
            chosen_norm = api.norm_tag(raw.content)

        # Validate & save to bsinfo
        try:
            pdata = await api.get_player(chosen_norm)
        except Exception:
            return await ctx.send(embed=discord.Embed(title="Invalid tag", description="I couldn't validate that tag. Please try again.", color=ERROR))

        trophies = pdata.get("trophies", 0)
        ign = pdata.get("name", "Player")
        if bscog:
            async with bscog.config.user(ctx.author).tags() as tags:
                if chosen_norm not in tags and len(tags) < 3:
                    tags.append(chosen_norm)
            await bscog.config.user(ctx.author).ign_cache.set(pdata.get("name") or "")
            club = pdata.get("club") or {}
            await bscog.config.user(ctx.author).club_tag_cache.set((club.get("tag") or "").replace("#",""))

        # STEP 2: eligible clubs
        clubs_cog = self.bot.get_cog("Clubs")
        clubs_cfg = await clubs_cog.config.guild(guild).clubs() if clubs_cog else {}
        gconf = await self.config.guild(guild).all()
        roster_counts = gconf.get("roster_counts", {})
        options = eligible_clubs(clubs_cfg, trophies, roster_counts)
        if not options:
            return await ctx.send(embed=discord.Embed(title="No eligible clubs right now", color=ERROR))

        # richer info from API
        rich: Dict[str, Dict[str, Any]] = {}
        for ctag, _ in options[:5]:
            try:
                cinfo = await api.get_club_by_tag(ctag)
            except Exception:
                cinfo = {}
            rich[ctag] = {
                "type": (cinfo.get("type") or "unknown").title(),
                "trophies": cinfo.get("trophies", 0),
                "desc": (cinfo.get("description") or "")[:140]
            }

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
        try: await msg2.edit(view=None)
        except: pass
        if view.selected is None:
            return await ctx.send(embed=discord.Embed(title="Cancelled", color=WARN))
        ctag, ccfg = view.selected

        await self.config.member_from_ids(guild.id, ctx.author.id).pending_club_tag.set(ctag)

        # STEP 3: notify leadership (ping leadership role if set)
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

        # Final message to applicant
        done = discord.Embed(
            title="Next Step",
            description=f"Great! Request to join **{ccfg['name']}** in-game now. "
                        f"Once you’re in, I’ll update your roles and nickname.",
            color=SUCCESS
        )
        await ctx.send(embed=done)

async def setup(bot: Red):
    await bot.add_cog(Onboarding(bot))
