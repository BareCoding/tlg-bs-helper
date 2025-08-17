# brawlcommon/token.py
from redbot.core.bot import Red

async def get_brawl_api_token(bot: Red) -> str:
    """
    Read the Brawl Stars API token from Red's shared api tokens.
    Set it with:
      [p]set api brawlstars api_key,YOUR_SUPERCELL_TOKEN
    """
    tokens = await bot.get_shared_api_tokens("brawlstars")
    token = tokens.get("api_key")
    if not token:
        raise RuntimeError(
            "No Brawl Stars API token configured. "
            "Use: `[p]set api brawlstars api_key,YOUR_SUPERCELL_TOKEN`"
        )
    return token
