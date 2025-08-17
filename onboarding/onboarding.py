# onboarding/onboarding.py
from redbot.core import commands, Config
from redbot.core.bot import Red
import discord
from brawlcommon.brawl_api import BrawlStarsAPI
from brawlcommon.token import get_brawl_api_token
from brawlcommon.utils import eligible_clubs

class Onboarding(commands.Cog):
    """DM onboarding flow for new members to pick an eligible club."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xFEEDBEEF, force_registration=True)
        default_guild = {
            "apply_notify_channel_id": None,
            "clubs": {},          # mirror of Clubs cog (name, required_trophies, min_slots, role_id, badge_id, log_channel_id)
            "roster_counts": {}   # club_tag -> approx member count (updated by ClubSync)
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
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @onboarding.command(name="setnotify")
    @commands.has_guild_permissions(manage_guild=True)
    async def onboarding_setnotify(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).apply_notify_channel_id.set(channel.id)
        await ctx.send("✅ Set application notify channel.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        try:
            await member.send("Welcome! To apply for a club, reply with your Brawl Stars tag (e.g. `#ABCD123`) or run `bsstart` here anytime.")
        except discord.Forbidden:
            pass

    @commands.command(name="bsstart")
    async def bsstart(self, ctx):
        """Start the club application wizard in DMs."""
        if not isinstance(ctx.channel, discord.DMChannel):
            try:
                await ctx.author.send("Starting your club application. What's your player tag?")
            except discord.Forbidden:
                return await ctx.send("Open your DMs and run `bsstart` again.")
            return

        # pick the first mutual guild with this cog loaded
        guilds = [g for g in self.bot.guilds if ctx.author in g.members]
        if not guilds:
            return await ctx.send("I don't see you in any servers with me.")
        guild = guilds[0]
        api = await self._api(guild)

        await ctx.send("Please send your player tag (e.g. `#ABCD123`).")
        def check(m): return m.author.id == ctx.author.id and isinstance(m.channel, discord.DMChannel)
        msg = await self.bot.wait_for("message", check=check, timeout=120)
        pdata = await api.get_player(msg.content)
        trophies = pdata.get("trophies", 0)
        ign = pdata.get("name", "Player")

        # store to Players cog
        pcog = self.bot.get_cog("Players")
        if pcog:
            try:
                await pcog.bs_verify.callback(pcog, ctx=ctx, tag=msg.content)  # reuse verify command logic
            except Exception:
                pass

        gconf = await self.config.guild(guild).all()
        roster_counts = gconf.get("roster_counts", {})
        clubs_cfg = gconf.get("clubs", {})
        options = eligible_clubs(clubs_cfg, trophies, roster_counts)
        if not options:
            return await ctx.send("No eligible clubs right now. A leader will contact you soon.")

        lines = [f"{i+1}. {c[1]['name']} (req {c[1].get('required_trophies',0)} trophies)" for i, c in enumerate(options[:5])]
        await ctx.send(f"Hi **{ign}**! Pick a club by number:\n" + "\n".join(lines))
        pick = await self.bot.wait_for("message", check=check, timeout=120)
        try:
            idx = int(pick.content) - 1
            ctag, ccfg = options[idx]
        except Exception:
            return await ctx.send("Invalid choice. Run `bsstart` again.")

        await self.config.member_from_ids(guild.id, ctx.author.id).pending_club_tag.set(ctag)

        # Notify leadership
        ch_id = gconf.get("apply_notify_channel_id")
        if ch_id:
            ch = guild.get_channel(ch_id)
            if ch:
                await ch.send(f"📥 **{ign}** ({pdata.get('tag','')}) wants to join **{ccfg['name']}** #{ctag}. Please accept in-game.")

        await ctx.send("Great! Please request to join that club in-game. I’ll update your roles once you’re in.")

async def setup(bot: Red):
    await bot.add_cog(Onboarding(bot))
