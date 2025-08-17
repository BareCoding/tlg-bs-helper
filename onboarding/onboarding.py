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

from brawlcommon.admin import bs_admin_check
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import tag_pretty, club_badge_url

ACCENT  = discord.Color.from_rgb(66, 135, 245)
SUCCESS = discord.Color.green()
WARN    = discord.Color.orange()
ERROR   = discord.Color.red()
GOLD    = discord.Color.gold()

MAX_MEMBERS = 30  # clubs are full at 30

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

class Onboarding(commands.Cog):
    """Onboarding flow in DMs (with full/under-req fail-safes and leadership pings)."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x0B0ABD, force_registration=True)
        default_guild = {"apply_notify_channel_id": None}
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

    @commands.group()
    async def onboarding(self, ctx):
        """Onboarding admin config."""
        pass

    @onboarding.command()
    @commands.guild_only()
    @bs_admin_check()
    async def setnotify(self, ctx, channel: discord.TextChannel):
        """Set the channel where application notifications are posted."""
        await self.config.guild(ctx.guild).apply_notify_channel_id.set(channel.id)
        e = discord.Embed(title="Notify channel set", description=f"Applications will be posted in {channel.mention}.", color=SUCCESS)
        await ctx.send(embed=e)

    # PUBLIC entrypoint called by BSInfo: runs fully in DMs
    async def start_application_dm(self, guild: discord.Guild, member: discord.Member):
        if guild is None:
            return
        try:
            dm = await member.create_dm()
        except discord.Forbidden:
            return

        api = await self._api(guild)
        bscog = self.bot.get_cog("BSInfo")
        if not bscog:
            await dm.send(embed=discord.Embed(title="Setup error", description="Tag store not available.", color=ERROR))
            return

        # STEP 1: choose or enter tag
        u = await bscog.config.user(member).all()
        saved = [t for t in u["tags"] if t]

        chosen_norm: Optional[str] = None
        if saved:
            emb = discord.Embed(
                title="Use an existing tag?",
                description="Pick one of your saved tags below, or choose **Enter a new tag…**",
                color=ACCENT
            )
            view = TagSelectView(member.id, saved)
            msg = await dm.send(embed=emb, view=view)
            await view.wait()
            try:
                await msg.edit(view=None)
            except Exception:
                pass
            if view.choice is None:
                return await dm.send(embed=discord.Embed(title="Timed out", color=ERROR))
            if view.choice != "_new":
                chosen_norm = view.choice

        if not chosen_norm:
            ask = discord.Embed(title="Your Tag", description="Reply with your player tag (e.g. `#ABCD123`).", color=ACCENT)
            await dm.send(embed=ask)

            def check_tag(m): return m.author.id == member.id and isinstance(m.channel, discord.DMChannel)
            try:
                raw = await self.bot.wait_for("message", check=check_tag, timeout=180)
            except Exception:
                return await dm.send(embed=discord.Embed(title="Timed out", color=ERROR))
            chosen_norm = api.norm_tag(raw.content)

        # Validate & save to bsinfo
        try:
            pdata = await api.get_player(chosen_norm)
        except Exception:
            return await dm.send(embed=discord.Embed(title="Invalid tag", description="I couldn't validate that tag. Please try again.", color=ERROR))

        trophies = pdata.get("trophies", 0)
        ign = pdata.get("name", "Player")
        async with bscog.config.user(member).tags() as tags:
            if chosen_norm not in tags and len(tags) < 3:
                tags.append(chosen_norm)
        await bscog.config.user(member).ign_cache.set(pdata.get("name") or "")
        club = pdata.get("club") or {}
        await bscog.config.user(member).club_tag_cache.set((club.get("tag") or "").replace("#", ""))

        # STEP 2: eligible clubs (LIVE) — skip full (>=30) and separate reasons
        clubs_cog = self.bot.get_cog("Clubs")
        tracked = await clubs_cog.config.guild(guild).clubs() if clubs_cog else {}
        if not tracked:
            return await dm.send(embed=discord.Embed(title="No clubs configured", description="Ask staff to add clubs with `[p]clubs add #TAG`.", color=ERROR))

        eligible_open: List[Tuple[str, Dict[str, Any]]] = []
        full_but_eligible: List[Tuple[str, Dict[str, Any]]] = []
        under_req: List[Tuple[str, Dict[str, Any]]] = []

        for ctag, cfg in tracked.items():
            try:
                cinfo = await api.get_club_by_tag(ctag)
            except Exception:
                continue
            members = len(cinfo.get("members") or [])
            req = int(cinfo.get("requiredTrophies", cfg.get("required_trophies", 0)))
            merged = {
                "name": cinfo.get("name") or cfg.get("name") or f"#{ctag}",
                "required_trophies": req,
                "badge_id": cinfo.get("badgeId") or cfg.get("badge_id") or 0,
                "role_id": cfg.get("role_id"),
                "log_channel_id": cfg.get("log_channel_id"),
                "leadership_role_id": cfg.get("leadership_role_id"),
                "_members": members,
                "_type": (cinfo.get("type") or "unknown").title(),
                "_club_trophies": cinfo.get("trophies", 0),
                "_desc": (cinfo.get("description") or "")[:180],
            }

            if trophies < req:
                under_req.append((ctag, merged))
            elif members >= MAX_MEMBERS:
                full_but_eligible.append((ctag, merged))
            else:
                eligible_open.append((ctag, merged))

        if not eligible_open:
            if full_but_eligible and not under_req:
                await dm.send(embed=discord.Embed(
                    title="All eligible clubs are full",
                    description="Right now every club you qualify for is at capacity. Leadership has been pinged — they’ll make space and follow up.",
                    color=WARN
                ))
                gconf = await self.config.guild(guild).all()
                notify = guild.get_channel(gconf.get("apply_notify_channel_id") or 0)
                if notify:
                    role = discord.utils.get(guild.roles, name="BS Club Leadership")
                    mention = role.mention if role else ""
                    e = discord.Embed(
                        title="Applicant waiting — all eligible clubs full",
                        description=f"**{ign}** ({pdata.get('tag','')}) qualifies but all eligible clubs are full (≥{MAX_MEMBERS}).",
                        color=ERROR
                    )
                    await notify.send(content=mention or None, embed=e)
                return
            else:
                return await dm.send(embed=discord.Embed(
                    title="No eligible clubs yet",
                    description="You don’t meet the trophy requirements for any of our clubs right now.\nKeep pushing trophies and try again soon!",
                    color=ERROR
                ))

        # Sort and pretty cards
        eligible_open.sort(key=lambda x: (x[1]["_members"], -x[1].get("required_trophies", 0)))
        cards = []
        for ctag, c in eligible_open[:5]:
            cards.append(
                f"**{c['name']}**  `#{ctag}`\n"
                f"**Members:** {c['_members']}/{MAX_MEMBERS} • **Req:** {c.get('required_trophies',0):,} • "
                f"**Club Trophies:** {c['_club_trophies']:,} • **Type:** {c['_type']}\n"
                f"{c['_desc'] or '—'}"
            )

        emb = discord.Embed(
            title=f"Hi {ign}! Pick an eligible club",
            description="\n\n".join(cards),
            color=GOLD
        )
        if len(eligible_open) == 1 and eligible_open[0][1]["badge_id"]:
            emb.set_thumbnail(url=club_badge_url(eligible_open[0][1]["badge_id"]))

        view = ClubPickView(member.id, eligible_open)
        msg2 = await dm.send(embed=emb, view=view)
        await view.wait()
        try:
            await msg2.edit(view=None)
        except Exception:
            pass
        if view.selected is None:
            return await dm.send(embed=discord.Embed(title="Cancelled", color=WARN))
        ctag, ccfg = view.selected

        await self.config.member_from_ids(guild.id, member.id).pending_club_tag.set(ctag)

        # Notify leadership (specific role if configured, else named role)
        gconf = await self.config.guild(guild).all()
        target = guild.get_channel(gconf.get("apply_notify_channel_id") or 0)
        leadership_ping = None
        cfg = tracked.get(ctag) or {}
        rid = cfg.get("leadership_role_id")
        if rid:
            role = guild.get_role(rid)
            if role:
                leadership_ping = role.mention
        if not leadership_ping:
            role = discord.utils.get(guild.roles, name="BS Club Leadership")
            if role:
                leadership_ping = role.mention

        if target:
            content = leadership_ping or None
            e = discord.Embed(
                title="New Application",
                description=f"**{ign}** ({pdata.get('tag','')}) wants to join **{ccfg['name']}** `#{ctag}`. Please accept in-game.",
                color=SUCCESS
            )
            await target.send(content=content, embed=e)

        done = discord.Embed(
            title="Next Step",
            description=f"Great! Request to join **{ccfg['name']}** in-game now. Once you’re in, I’ll update your roles and nickname.",
            color=SUCCESS
        )
        await dm.send(embed=done)

async def setup(bot: Red):
    await bot.add_cog(Onboarding(bot))
