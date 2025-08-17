# brawlcommon/utils.py
from typing import Dict, Any, List, Tuple, Optional
import re

# Brawlify CDN helpers
BRAWLIFY_PLAYER_AVATAR = "https://cdn.brawlify.com/profile/{icon_id}.png"
BRAWLIFY_CLUB_BADGE    = "https://cdn.brawlify.com/club/{badge_id}.png"
BRAWLIFY_BRAWLER       = "https://cdn.brawlify.com/brawler/{brawler_id}.png"
BRAWLIFY_STARPOWER     = "https://cdn.brawlify.com/starpower/{starpower_id}.png"
BRAWLIFY_GADGET        = "https://cdn.brawlify.com/gadget/{gadget_id}.png"
BRAWLIFY_GEAR          = "https://cdn.brawlify.com/gear/{gear_id}.png"
BRAWLIFY_MODE          = "https://cdn.brawlify.com/gamemode/{mode}.png"
BRAWLIFY_MAP           = "https://cdn.brawlify.com/map/{map_id}.png"

def tag_pretty(tag: str) -> str:
    return f"#{tag.upper().replace('#','')}"

def player_avatar_url(icon_id: int) -> str:
    return BRAWLIFY_PLAYER_AVATAR.format(icon_id=icon_id or 0)

def club_badge_url(badge_id: int) -> str:
    return BRAWLIFY_CLUB_BADGE.format(badge_id=badge_id or 0)

def brawler_icon_url(brawler_id: int) -> str:
    return BRAWLIFY_BRAWLER.format(brawler_id=int(brawler_id) if brawler_id else 0)

def starpower_icon_url(sp_id: int) -> str:
    return BRAWLIFY_STARPOWER.format(starpower_id=int(sp_id) if sp_id else 0)

def gadget_icon_url(g_id: int) -> str:
    return BRAWLIFY_GADGET.format(gadget_id=int(g_id) if g_id else 0)

def gear_icon_url(gear_id: int) -> str:
    return BRAWLIFY_GEAR.format(gear_id=int(gear_id) if gear_id else 0)

def mode_icon_url(mode: str) -> str:
    safe = re.sub(r"[^a-z0-9_-]", "", (mode or "").lower())
    return BRAWLIFY_MODE.format(mode=safe or "gem-grab")

def map_image_url(map_id: int) -> str:
    return BRAWLIFY_MAP.format(map_id=int(map_id) if map_id else 0)

def eligible_clubs(
    clubs_cfg: Dict[str, Dict[str, Any]],
    player_trophies: int,
    member_counts: Dict[str, int],
) -> List[Tuple[str, Dict[str, Any]]]:
    """
    API-driven eligibility:
      - player_trophies >= club.required_trophies (from official API)
      - member_count < 50
    Sorted: lowest members first, then higher requirements.
    """
    out = []
    for ctag, cfg in (clubs_cfg or {}).items():
        req = int(cfg.get("required_trophies", 0))
        members = int(member_counts.get(ctag, 0))
        if player_trophies >= req and members < 50:
            out.append((ctag, {**cfg, "_members": members}))
    out.sort(key=lambda x: (x[1]["_members"], -x[1].get("required_trophies", 0)))
    return out

def find_brawler_id_by_name(all_brawlers: Dict[str, Any], query: str) -> Optional[int]:
    """Quick fuzzy-ish match for a brawler name to its id."""
    q = (query or "").strip().lower()
    for item in (all_brawlers.get("items") or []):
        name = (item.get("name") or "").lower()
        if name == q:
            return int(item.get("id"))
    for item in (all_brawlers.get("items") or []):
        name = (item.get("name") or "").lower()
        if q in name:
            return int(item.get("id"))
    return None
