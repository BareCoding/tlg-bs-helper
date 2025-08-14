from __future__ import annotations
import asyncio, time
from typing import Optional, Dict, Any, Mapping
import aiohttp
from urllib.parse import urlencode

API_BASE = "https://api.brawlstars.com/v1"

class BSAPIError(RuntimeError):
    def __init__(self, status: int, text: str):
        super().__init__(f"Brawl Stars API {status}: {text}")
        self.status = status
        self.text = text

def normalize_tag(tag: str) -> str:
    # '#abc o123 ' -> 'ABCO123' (O -> 0) and without leading '#'
    return tag.strip().lstrip("#").upper().replace("O", "0")

def _qs(params: Optional[Mapping[str, Any]]) -> str:
    if not params:
        return ""
    # drop None values
    filtered = {k: v for k, v in params.items() if v is not None}
    if not filtered:
        return ""
    return "?" + urlencode(filtered)

class BrawlStarsAPI:
    """
    Async client with timeouts, retries, 429 backoff, TTL cache,
    paging helpers, and typed endpoint methods.
    """
    def __init__(self, token: str, *, timeout: float = 15.0):
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, tuple[float, Any]] = {}

    async def _session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {self._token}"}
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # --------- caching ----------
    def _cache_get(self, key: str) -> Optional[Any]:
        hit = self._cache.get(key)
        if not hit:
            return None
        exp, data = hit
        if exp < time.time():
            self._cache.pop(key, None)
            return None
        return data

    def _cache_set(self, key: str, data: Any, ttl: float = 30.0):
        self._cache[key] = (time.time() + ttl, data)

    # --------- low-level GET with retries ----------
    async def _get(self, path: str, *, params: Optional[Mapping[str, Any]] = None,
                   cache_ttl: float = 0.0) -> Any:
        url = f"{API_BASE}{path}{_qs(params)}"
        if cache_ttl > 0:
            cached = self._cache_get(url)
            if cached is not None:
                return cached

        s = await self._session()
        attempts = 0
        backoff = 0.75
        while True:
            attempts += 1
            async with s.get(url) as r:
                if r.status == 200:
                    data = await r.json()
                    if cache_ttl > 0:
                        self._cache_set(url, data, ttl=cache_ttl)
                    return data
                if r.status == 429:
                    retry = r.headers.get("Retry-After")
                    delay = float(retry) if retry else backoff
                    await asyncio.sleep(delay)
                    backoff = min(backoff * 2, 8)
                    continue
                if r.status >= 500 and attempts < 4:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 6)
                    continue
                txt = await r.text()
                raise BSAPIError(r.status, txt)

    # --------- generic helper (future-proof) ----------
    async def get(self, path: str, *, params: Optional[Mapping[str, Any]] = None,
                  cache_ttl: float = 0.0) -> Any:
        """
        Perform a raw GET to /v1/<path> (path must start with '/').
        Use this when Supercell adds new endpoints you haven't wrapped yet.
        """
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")
        return await self._get(path, params=params, cache_ttl=cache_ttl)

    # --------- PLAYERS ----------
    async def get_player(self, tag: str) -> Any:
        tag = normalize_tag(tag)
        return await self._get(f"/players/%23{tag}", cache_ttl=5.0)

    async def get_player_battlelog(self, tag: str) -> Any:
        tag = normalize_tag(tag)
        # battlelog is sensitive to rate limits; keep a short cache to reduce spam
        return await self._get(f"/players/%23{tag}/battlelog", cache_ttl=3.0)

    # --------- CLUBS ----------
    async def get_club(self, tag: str) -> Any:
        tag = normalize_tag(tag)
        return await self._get(f"/clubs/%23{tag}", cache_ttl=10.0)

    async def get_club_members(self, tag: str, *, limit: int = 50,
                               after: str | None = None, before: str | None = None) -> Any:
        tag = normalize_tag(tag)
        params = {"limit": limit, "after": after, "before": before}
        return await self._get(f"/clubs/%23{tag}/members", params=params, cache_ttl=5.0)

    async def search_clubs(self, *, name: str, limit: int = 10, after: str | None = None,
                           before: str | None = None) -> Any:
        params = {"name": name, "limit": limit, "after": after, "before": before}
        return await self._get("/clubs", params=params, cache_ttl=5.0)

    # --------- BRAWLERS ----------
    async def list_brawlers(self, *, limit: int = 50, after: str | None = None,
                            before: str | None = None) -> Any:
        params = {"limit": limit, "after": after, "before": before}
        return await self._get("/brawlers", params=params, cache_ttl=3600.0)

    async def get_brawler(self, brawler_id: int) -> Any:
        return await self._get(f"/brawlers/{int(brawler_id)}", cache_ttl=3600.0)

    # --------- RANKINGS (use 'global' or ISO country code like 'US', 'GB') ----------
    async def rankings_players(self, country_code: str = "global", *, limit: int = 50,
                               after: str | None = None, before: str | None = None) -> Any:
        params = {"limit": limit, "after": after, "before": before}
        return await self._get(f"/rankings/{country_code}/players", params=params, cache_ttl=30.0)

    async def rankings_clubs(self, country_code: str = "global", *, limit: int = 50,
                             after: str | None = None, before: str | None = None) -> Any:
        params = {"limit": limit, "after": after, "before": before}
        return await self._get(f"/rankings/{country_code}/clubs", params=params, cache_ttl=30.0)

    async def rankings_brawlers(self, country_code: str, brawler_id: int, *, limit: int = 50,
                                after: str | None = None, before: str | None = None) -> Any:
        params = {"limit": limit, "after": after, "before": before}
        return await self._get(
            f"/rankings/{country_code}/brawlers/{int(brawler_id)}",
            params=params, cache_ttl=30.0
        )

    # --------- EVENTS ----------
    async def events_rotation(self) -> Any:
        return await self._get("/events/rotation", cache_ttl=30.0)
