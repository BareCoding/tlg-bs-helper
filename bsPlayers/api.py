# bsPlayers/api.py
import asyncio, time
import aiohttp
from typing import Optional, Any, Mapping, Dict
from redbot.core import Config

API_BASE = "https://api.brawlstars.com/v1"
CONF_ID = 0xB51A00  # Shared across all cogs

class BSAPIError(RuntimeError):
    def __init__(self, status: int, text: str):
        super().__init__(f"Brawl Stars API {status}: {text}")
        self.status = status
        self.text = text

def normalize_tag(tag: str) -> str:
    return tag.strip().lstrip("#").upper().replace("O", "0")

# Config for storing one global token
_global_conf = Config.get_conf(object(), identifier=CONF_ID, force_registration=True)
_global_conf.register_global(bs_token=None)

async def get_token() -> Optional[str]:
    return await _global_conf.bs_token()

async def set_token(token: str) -> None:
    await _global_conf.bs_token.set(token.strip())

def _qs(params: Optional[Mapping[str, Any]]) -> str:
    if not params: return ""
    from urllib.parse import urlencode
    return "?" + urlencode({k: v for k, v in params.items() if v is not None})

class BrawlStarsAPI:
    def __init__(self, token: str, *, timeout: float = 15.0):
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, tuple[float, Any]] = {}

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {self._token}"}
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _get_cache(self, key: str):
        hit = self._cache.get(key)
        if hit and hit[0] >= time.time():
            return hit[1]

    def _set_cache(self, key: str, data: Any, ttl: float):
        self._cache[key] = (time.time() + ttl, data)

    async def _get(self, path: str, *, params: Mapping[str, Any] | None = None, cache_ttl: float = 0.0):
        url = f"{API_BASE}{path}{_qs(params)}"
        if cache_ttl > 0:
            cached = self._get_cache(url)
            if cached: return cached

        s = await self._get_session()
        attempts, backoff = 0, 0.75
        while True:
            attempts += 1
            async with s.get(url) as r:
                if r.status == 200:
                    data = await r.json()
                    if cache_ttl > 0:
                        self._set_cache(url, data, cache_ttl)
                    return data
                if r.status == 429:
                    await asyncio.sleep(float(r.headers.get("Retry-After", backoff)))
                    backoff = min(backoff * 2, 8)
                    continue
                if r.status >= 500 and attempts < 4:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 6)
                    continue
                raise BSAPIError(r.status, await r.text())

    # --- API endpoints ---
    async def get_player(self, tag: str): 
        tag = normalize_tag(tag)
        return await self._get(f"/players/%23{tag}", cache_ttl=10)

    async def get_player_battlelog(self, tag: str):
        tag = normalize_tag(tag)
        return await self._get(f"/players/%23{tag}/battlelog", cache_ttl=5)

    async def list_brawlers(self): 
        return await self._get("/brawlers", cache_ttl=3600)

    async def get_club(self, tag: str):
        tag = normalize_tag(tag)
        return await self._get(f"/clubs/%23{tag}", cache_ttl=30)

    async def get_club_members(self, tag: str):
        tag = normalize_tag(tag)
        return await self._get(f"/clubs/%23{tag}/members", cache_ttl=30)

    # generic
    async def get(self, path: str, params=None, cache_ttl=0.0):
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")
        return await self._get(path, params=params, cache_ttl=cache_ttl)
