# brawlcommon/brawl_api.py
import asyncio
import aiohttp
from typing import Optional, Dict, Any

API_BASE = "https://api.brawlstars.com/v1"

class BrawlStarsAPI:
    def __init__(self, token: str, session: Optional[aiohttp.ClientSession] = None):
        self._token = token
        self._session = session or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=8)
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

    async def _get(self, path: str) -> Dict[str, Any]:
        url = f"{API_BASE}{path}"
        async with self._lock:
            async with self._session.get(url, headers=self._headers()) as resp:
                if resp.status == 429:
                    # simple backoff
                    retry = int(resp.headers.get("Retry-After", "1"))
                    await asyncio.sleep(retry)
                    return await self._get(path)
                resp.raise_for_status()
                return await resp.json()

    async def get_player(self, tag: str) -> Dict[str, Any]:
        nt = self.norm_tag(tag)
        return await self._get(f"/players/%23{nt}")

    async def get_club_by_tag(self, club_tag: str) -> Dict[str, Any]:
        nt = self.norm_tag(club_tag)
        return await self._get(f"/clubs/%23{nt}")

    async def get_club_members(self, club_tag: str) -> Dict[str, Any]:
        nt = self.norm_tag(club_tag)
        return await self._get(f"/clubs/%23{nt}/members")
