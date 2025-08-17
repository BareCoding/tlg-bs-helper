# brawlcommon/utils.py
from typing import Dict, Any, List, Tuple

BRAWLIFY_PLAYER_AVATAR = "https://cdn.brawlify.com/profile/{icon_id}.png"
BRAWLIFY_CLUB_BADGE    = "https://cdn.brawlify.com/club/{badge_id}.png"

def tag_pretty(tag: str) -> str:
    return f"#{tag.upper().replace('#','')}"

def player_avatar_url(icon_id: int) -> str:
    return BRAWLIFY_PLAYER_AVATAR.format(icon_id=icon_id or 0)

def club_badge_url(badge_id: int) -> str:
    return BRAWLIFY_CLUB_BADGE.format(badge_id=badge_id or 0)

def eligible_clubs(clubs_cfg: Dict[str, Dict[str, Any]], player_trophies: int, member_counts: Dict[str, int]) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Return list of eligible clubs sorted by lowest member count first,
    then higher required_trophies (to fill tougher clubs if equal members).
    """
    out = []
    for ctag, cfg in (clubs_cfg or {}).items():
        req = int(cfg.get("required_trophies", 0))
        minslots = int(cfg.get("min_slots", 0))
        members = int(member_counts.get(ctag, 0))
        has_slots = members <= (50 - minslots)
        if player_trophies >= req and has_slots:
            out.append((ctag, {**cfg, "_members": members}))
    out.sort(key=lambda x: (x[1]["_members"], -x[1].get("required_trophies", 0)))
    return out
