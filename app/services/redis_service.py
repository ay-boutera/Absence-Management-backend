import redis.asyncio as redis
from datetime import datetime, timedelta, timezone

from app.config.settings import settings


class RedisService:
    _fallback_store: dict[str, tuple[str, datetime]] = {}

    def __init__(self):
        self._enabled = settings.USE_REDIS
        self._client = None
        if self._enabled:
            # decode_responses=True returns strings instead of bytes.
            self._client = redis.from_url(settings.REDIS_URL, decode_responses=True)

    def _prune_fallback(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [key for key, (_, exp) in self._fallback_store.items() if exp <= now]
        for key in expired:
            self._fallback_store.pop(key, None)

    async def setex(self, key: str, expiry_seconds: int, value: str) -> None:
        if self._enabled and self._client is not None:
            try:
                await self._client.setex(key, expiry_seconds, value)
                return
            except Exception:
                pass
        self._prune_fallback()
        self._fallback_store[key] = (
            value,
            datetime.now(timezone.utc) + timedelta(seconds=expiry_seconds),
        )

    async def get(self, key: str) -> str | None:
        if self._enabled and self._client is not None:
            try:
                return await self._client.get(key)
            except Exception:
                pass
        self._prune_fallback()
        record = self._fallback_store.get(key)
        if record is None:
            return None
        return record[0]

    async def delete(self, key: str) -> None:
        if self._enabled and self._client is not None:
            try:
                await self._client.delete(key)
                return
            except Exception:
                pass
        self._fallback_store.pop(key, None)

    async def exists(self, key: str) -> bool:
        if self._enabled and self._client is not None:
            try:
                return await self._client.exists(key) > 0
            except Exception:
                pass
        self._prune_fallback()
        return key in self._fallback_store

    async def blacklist_token(self, token: str, expiry_seconds: int = 604800) -> None:
        """
        Blacklist a token by storing it in Redis with an optional expiry.
        Default expiry is 7 days (matching REFERSH_TOKEN_EXPIRE_DAYS).
        """
        await self.setex(f"blacklist:{token}", expiry_seconds, "1")

    async def is_token_blacklisted(self, token: str) -> bool:
        """
        Check if a token exists in the blacklist.
        """
        return await self.exists(f"blacklist:{token}")
