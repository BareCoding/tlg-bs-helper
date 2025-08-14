# bsPlayers/api.py
from __future__ import annotations

import asyncio
import time
from typing import Optional, Any, Mapping, Dict

import aiohttp
from urllib.parse import urlencode

API_BASE = "https://api.brawlstars.com/v1"


# ---------------- Exceptions & helpers ----------------

class BSAPIError(RuntimeError):
    """Raised for non-2xx responses from the Brawl Stars API."""
    def __init__(self, status: int, text: str):
        super().__init__(f"Brawl Stars API {status}: {text}")
        self.status = status
        self.text = text


def normalize_tag(tag: str) -> str:
    """Normalize a Brawl Stars tag: strip #, uppercase, and replace letter O with zero."""
    return tag.strip().lstrip("#").upper().replace("O", "0")


def _qs(params: Optional[Mapping[str, Any]]) -> str:
    if not params:
        return ""
    filtered = {k: v for k, v in params.items() if v is not None}
    return "?" + urlencode(filtered) if filtered else ""


# ---------------- HTTP client ----------------

class BrawlStarsAPI:
    """
    Async client with timeouts, retries, 429 backoff (honors Retry-After),
    and a tiny TTL cache.

    Token is pulled from Red's shared API tokens store:
        [p]set api brawlstars api_key,YOURTOKEN
    """

    def __init__(self, bot, *, timeout: float = 15.0):
        self.bot = bot
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session_obj: Optional[aiohttp.ClientSession] = None
        self._token: Optional[str] = None
        self._cache: Dict[str, tuple[float, Any]] = {}

    # ---- session/token management ----
    async def _ensure_token(self):
        if not self._token:
            keys = await self.bot.get_shared_api_tokens("brawlstars")
            self._token = (keys or {}).get("api_key")
        if not self._token:
            raise BSAPIError(
                401,
                "No Brawl Stars API key set. Use `[p]set api brawlstars api_key,YOURTOKEN`."
            )

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session_obj is None or self._session_obj.closed:
            await self._ensure_token()
            self._session_obj = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                    "User-Agent": "Red-DiscordBot/BSPlayers",
                },
            )
        return self._session_obj

    async def close(self):
        if self._session_obj and not self._session_obj.closed:
            await self._session_obj.close()

    # ---- tiny TTL cache ----
    def _cache_get(self, key: str) -> Optional[Any]:
        hit = self._cache.get(key)
        if not hit:
            return None
        exp, data = hit
        if exp < time.time():
            self._cache.pop(key, None)
            return None
        return data

    def _cache_set(self, key: str, data: Any, ttl: float):
        self._cache[key] = (time.time() + ttl, data)

    # ---- core GET with retries/backoff ----
    async def _get(
        self,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        cache_ttl: float = 0.0,
    ) -> Any:
        url = f"{API_BASE}{path}{_qs(params)}"
        if cache_ttl > 0:
            cached = self._cache_get(url)
            if cached is not None:
                return cached

        session = await self._get_session()
        attempts = 0
        backoff = 1.0

        while True:
            async with session.get(url) as r:
                if r.status == 200:
                    data = await r.json()
                    if cache_ttl > 0:
                        self._cache_set(url, data, cache_ttl)
                    return data

                # Handle transient errors with backoff
                if r.status == 429:
                    ra = r.headers.get("Retry-After")
                    delay = float(ra) if ra else backoff
                elif r.status in {500, 502, 503, 504}:
                    delay = backoff
                else:
                    # Permanent error – include body for context
                    body = await r.text()
                    raise BSAPIError(r.status, body)

                attempts += 1
                if attempts >= 5:
                    body = await r.text()
                    raise BSAPIError(r.status, f"Retry limit reached. Body: {body}")

                await asyncio.sleep(delay)
                backoff = min(backoff * 2, 8.0)

    # ---- API endpoints ----
    async def get_player(self, tag: str):
        """
        Full player object incl. trophies, wins, club, and player's brawlers list.
        """
        return await self._get(f"/players/%23{normalize_tag(tag)}", cache_ttl=30)

    async def get_player_battlelog(self, tag: str):
        """Recent battles for a player."""
        return await self._get(f"/players/%23{normalize_tag(tag)}/battlelog", cache_ttl=10)

    async def list_brawlers(self):
        """
        Global catalog of brawlers (names/ids). Not player-specific.
        A player's brawlers come from `get_player(tag)["brawlers"]`.
        """
        return await self._get("/brawlers", cache_ttl=3600)

    async def get_club(self, tag: str):
        return await self._get(f"/clubs/%23{normalize_tag(tag)}", cache_ttl=60)

    async def get_club_members(self, tag: str):
        return await self._get(f"/clubs/%23{normalize_tag(tag)}/members", cache_ttl=60)

    # Generic escape hatch (future endpoints)
    async def get(self, path: str, params: Optional[Mapping[str, Any]] = None, cache_ttl: float = 0.0):
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")
        return await self._get(path, params=params, cache_ttl=cache_ttl)
