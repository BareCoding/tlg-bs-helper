# bsPlayers/api.py
from __future__ import annotations

import asyncio
import time
from typing import Optional, Any, Mapping, Dict, Tuple

import aiohttp
from urllib.parse import urlencode

API_BASE = "https://api.brawlstars.com/v1"


# =========================
# Exceptions & helpers
# =========================

class BSAPIError(RuntimeError):
    """Raised when the Brawl Stars API returns a non-OK response."""
    def __init__(self, status: int, text: str):
        super().__init__(f"Brawl Stars API {status}: {text}")
        self.status = status
        self.text = text


def normalize_tag(tag: str) -> str:
    """
    Normalize a Brawl Stars tag:
    - strip whitespace
    - remove leading '#'
    - uppercase
    - convert letter 'O' to zero '0' (common user typo)
    """
    return tag.strip().lstrip("#").upper().replace("O", "0")


def _qs(params: Optional[Mapping[str, Any]]) -> str:
    if not params:
        return ""
    filtered = {k: v for k, v in params.items() if v is not None}
    return "?" + urlencode(filtered) if filtered else ""


# =========================
# Token helpers (Red)
# =========================

async def get_token(bot) -> Optional[str]:
    """
    Read the Brawl Stars API token from Red's shared API tokens.
    Set it with: [p]set api brawlstars api_key,YOURLONGTOKEN
    """
    keys = await bot.get_shared_api_tokens("brawlstars")
    return keys.get("api_key") if keys else None


async def set_token(bot, token: str) -> None:
    """Convenience helper if you want to set it programmatically."""
    await bot.set_shared_api_tokens("brawlstars", api_key=token)


# =========================
# Client
# =========================

class BrawlStarsAPI:
    """
    Async API client with:
      - Proper timeouts
      - Retries + exponential backoff on 429/5xx (honors Retry-After)
      - Tiny in-memory TTL cache for hot endpoints
    Token is pulled lazily from Red's shared API tokens store.
    """

    def __init__(self, bot, *, timeout: float = 15.0):
        self.bot = bot
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._ses: Optional[aiohttp.ClientSession] = None   # avoid name clash with method
        self._token: Optional[str] = None
        self._cache: Dict[str, Tuple[float, Any]] = {}

    # ---- session/token management ----
    async def _ensure_token(self) -> None:
        if not self._token:
            self._token = await get_token(self.bot)
        if not self._token:
            raise BSAPIError(
                401,
                "No Brawl Stars API key set. Use `[p]set api brawlstars api_key,YOURTOKEN`.",
            )

    async def _session(self) -> aiohttp.ClientSession:
        if self._ses is None or self._ses.closed:
            await self._ensure_token()
            self._ses = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        return self._ses

    async def close(self) -> None:
        if self._ses and not self._ses.closed:
            await self._ses.close()

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

    def _cache_set(self, key: str, data: Any, ttl: float) -> None:
        self._cache[key] = (time.time() + ttl, data)

    # ---- core GET with retries/backoff ----
    async def _get(
        self,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        cache_ttl: float = 0.0,
    ) -> Any:
        """
        Perform a GET to /v1{path}?params with optional TTL cache.
        Retries 429/500/502/503/504 up to 5 attempts (exp backoff, honors Retry-After).
        """
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")

        url = f"{API_BASE}{path}{_qs(params)}"

        # cache
        if cache_ttl > 0:
            cached = self._cache_get(url)
            if cached is not None:
                return cached

        ses = await self._session()
        attempts = 0
        backoff = 1.0  # seconds

        while True:
            async with ses.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if cache_ttl > 0:
                        self._cache_set(url, data, cache_ttl)
                    return data

                # retryable statuses
                if resp.status in {429, 500, 502, 503, 504} and attempts < 5:
                    attempts += 1
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        # Retry-After may be seconds or HTTP-date; we handle seconds form.
                        try:
                            delay = max(1.0, float(retry_after))
                        except Exception:
                            delay = backoff
                    else:
                        delay = backoff
                    await asyncio.sleep(delay)
                    # exponential backoff for next round
                    backoff = min(backoff * 2, 16.0)
                    continue

                # non-retryable (or retries exhausted)
                try:
                    text = await resp.text()
                except Exception:
                    text = f"HTTP {resp.status}"
                raise BSAPIError(resp.status, text)

    # =========================
    # High-level endpoints
    # =========================

    # ---- Players ----
    async def get_player(self, tag: str) -> Any:
        """
        Player profile (brawlers, trophies, wins, icon id, club stub).
        GET /players/%23{TAG}
        """
        tag = normalize_tag(tag)
        return await self._get(f"/players/%23{tag}", cache_ttl=10)

    async def get_player_battlelog(self, tag: str) -> Any:
        """
        Recent battles (mode, map, result, trophyChange, teams/players).
        GET /players/%23{TAG}/battlelog
        """
        tag = normalize_tag(tag)
        return await self._get(f"/players/%23{tag}/battlelog", cache_ttl=5)

    # ---- Clubs ----
    async def get_club(self, tag: str) -> Any:
        """
        Club details (name, description, type, membersCount, trophiesRequired, badgeId).
        GET /clubs/%23{TAG}
        """
        tag = normalize_tag(tag)
        return await self._get(f"/clubs/%23{tag}", cache_ttl=30)

    async def get_club_members(self, tag: str) -> Any:
        """
        Club members (name, tag, role, trophies).
        GET /clubs/%23{TAG}/members
        """
        tag = normalize_tag(tag)
        return await self._get(f"/clubs/%23{tag}/members", cache_ttl=30)

    # ---- Brawlers (global catalog) ----
    async def list_brawlers(self) -> Any:
        """
        Global brawler catalog (IDs & names; use IDs to map images in a CDN).
        GET /brawlers
        """
        return await self._get("/brawlers", cache_ttl=3600)

    async def get_brawler(self, brawler_id: int) -> Any:
        """
        Single brawler info.
        GET /brawlers/{id}
        """
        return await self._get(f"/brawlers/{int(brawler_id)}", cache_ttl=3600)

    # ---- Rankings / leaderboards ----
    async def rankings_players(self, country: str = "global", *, limit: int = 25, before: str = None, after: str = None) -> Any:
        """
        Player rankings (country or 'global').
        GET /rankings/{country}/players
        """
        return await self._get(
            f"/rankings/{country}/players",
            params={"limit": limit, "before": before, "after": after},
            cache_ttl=30,
        )

    async def rankings_clubs(self, country: str = "global", *, limit: int = 25, before: str = None, after: str = None) -> Any:
        """
        Club rankings (country or 'global').
        GET /rankings/{country}/clubs
        """
        return await self._get(
            f"/rankings/{country}/clubs",
            params={"limit": limit, "before": before, "after": after},
            cache_ttl=30,
        )

    async def rankings_brawler(self, brawler_id: int, country: str = "global", *, limit: int = 25, before: str = None, after: str = None) -> Any:
        """
        Rankings for a specific brawler (country or 'global').
        GET /rankings/{country}/brawlers/{id}
        """
        return await self._get(
            f"/rankings/{country}/brawlers/{int(brawler_id)}",
            params={"limit": limit, "before": before, "after": after},
            cache_ttl=30,
        )

    # ---- Events rotation ----
    async def events_rotation(self) -> Any:
        """
        Current/next event rotation (maps/modes with time windows).
        GET /events/rotation
        """
        return await self._get("/events/rotation", cache_ttl=30)

    # ---- Generic GET (escape hatch) ----
    async def get(self, path: str, *, params: Optional[Mapping[str, Any]] = None, cache_ttl: float = 0.0) -> Any:
        """
        Generic GET for any /v1 path (must start with '/').
        Example: await api.get('/players/%23XXXXXX', cache_ttl=10)
        """
        return await self._get(path, params=params, cache_ttl=cache_ttl)
