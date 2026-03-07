import redis.asyncio as redis
from app.config.settings import settings


class RedisService:
    def __init__(self):
        # Initialize the redis client using the URL from settings.
        # decode_responses=True will return strings instead of bytes.
        self._client = redis.from_url(settings.REDIS_URL, decode_responses=True)

    async def blacklist_token(self, token: str, expiry_seconds: int = 604800) -> None:
        """
        Blacklist a token by storing it in Redis with an optional expiry.
        Default expiry is 7 days (matching REFERSH_TOKEN_EXPIRE_DAYS).
        """
        await self._client.setex(f"blacklist:{token}", expiry_seconds, "1")

    async def is_token_blacklisted(self, token: str) -> bool:
        """
        Check if a token exists in the blacklist.
        """
        return await self._client.exists(f"blacklist:{token}") > 0
