# brawlcommon/brawl_api.py
import asyncio
import aiohttp
from typing import Optional, Dict, Any

API_BASE = "https://api.brawlstars.com/v1"

class BrawlStarsAPI:
    def __init__(self, token: str, session: Optional[aiohttp.ClientSession] = None):
        self._token = token
        self._session = session or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        )
        self._lock = asyncio.Lock()

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def norm_tag(tag: str) -> str:
        return tag.strip().upper().replace("#", "")

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{API_BASE}{path}"
        async with self._lock:
            async with self._session.get(url, headers=self._headers(), params=params) as resp:
                if resp.status == 429:
                    retry = int(resp.headers.get("Retry-After", "1"))
                    await asyncio.sleep(retry)
                    return await self._get(path, params=params)
                resp.raise_for_status()
                return await resp.json()

    # Players
    async def get_player(self, tag: str) -> Dict[str, Any]:
        nt = self.norm_tag(tag)
        return await self._get(f"/players/%23{nt}")

    # Clubs
    async def get_club_by_tag(self, club_tag: str) -> Dict[str, Any]:
        nt = self.norm_tag(club_tag)
        return await self._get(f"/clubs/%23{nt}")

    async def get_club_members(self, club_tag: str) -> Dict[str, Any]:
        nt = self.norm_tag(club_tag)
        return await self._get(f"/clubs/%23{nt}/members")

    # Brawlers
    async def get_brawlers(self) -> Dict[str, Any]:
        return await self._get("/brawlers")

    async def get_brawler(self, brawler_id: int) -> Dict[str, Any]:
        return await self._get(f"/brawlers/{int(brawler_id)}")

    # Rankings
    async def get_rankings_players(self, country: str = "global", limit: int = 25) -> Dict[str, Any]:
        return await self._get(f"/rankings/{country}/players", params={"limit": min(max(limit,1), 200)})

    async def get_rankings_clubs(self, country: str = "global", limit: int = 25) -> Dict[str, Any]:
        return await self._get(f"/rankings/{country}/clubs", params={"limit": min(max(limit,1), 200)})

    async def get_rankings_brawler(self, country: str, brawler_id: int, limit: int = 25) -> Dict[str, Any]:
        return await self._get(f"/rankings/{country}/brawlers/{int(brawler_id)}", params={"limit": min(max(limit,1), 200)})

    # Events
    async def get_events_rotation(self) -> Dict[str, Any]:
        return await self._get("/events/rotation")
