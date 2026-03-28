"""Usage tracking and rate limiting for MCP tool calls.

Tracks per-user tool usage in Postgres and enforces daily rate limits.
Uses an in-memory cache to avoid a DB round-trip on every call.
Recording is fire-and-forget so tool calls are never slowed by tracking.
"""

import asyncio
import logging
from datetime import date
from typing import Optional

import asyncpg

from .error_handling import ValidationError

logger = logging.getLogger(__name__)

# Default daily limit when user has no plan assigned
DEFAULT_DAILY_LIMIT = 1000


class RateLimitExceeded(ValidationError):
    """Raised when a user exceeds their daily call limit."""

    def __init__(self, limit: int, used: int):
        self.limit = limit
        self.used = used
        super().__init__(
            f"Daily rate limit exceeded: {used}/{limit} calls used today. "
            f"Resets at midnight UTC."
        )


class UsageTracker:
    """Tracks MCP tool usage and enforces rate limits.

    Uses the existing asyncpg pool from DatabaseManager.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        # In-memory cache: {zitadel_sub: (day, count, limit)}
        self._cache: dict[str, tuple[date, int, int]] = {}

    async def check_rate_limit(self, zitadel_sub: str) -> None:
        """Check if user is within their daily rate limit.

        Raises RateLimitExceeded if limit is exceeded.
        """
        today = date.today()

        # Check in-memory cache first
        cached = self._cache.get(zitadel_sub)
        if cached and cached[0] == today:
            _, count, limit = cached
            if limit > 0 and count >= limit:
                raise RateLimitExceeded(limit, count)
            return

        # Cache miss or stale day — query Postgres
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COALESCE(ud.call_count, 0) AS call_count,
                    COALESCE(up.daily_limit, $3) AS daily_limit
                FROM user_connections uc
                LEFT JOIN usage_plans up ON uc.plan_id = up.id
                LEFT JOIN usage_daily ud
                    ON ud.zitadel_sub = uc.zitadel_sub AND ud.day = $2
                WHERE uc.zitadel_sub = $1
                """,
                zitadel_sub,
                today,
                DEFAULT_DAILY_LIMIT,
            )

        if row is None:
            # User not found — let it pass, tool handler will fail with "no connection"
            return

        count = row["call_count"]
        limit = row["daily_limit"]
        self._cache[zitadel_sub] = (today, count, limit)

        if limit > 0 and count >= limit:
            raise RateLimitExceeded(limit, count)

    async def record_usage(
        self,
        zitadel_sub: str,
        tool_name: str,
        error: bool = False,
        duration_ms: Optional[int] = None,
    ) -> None:
        """Record a tool call. Safe to call fire-and-forget."""
        today = date.today()
        try:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO usage_log (zitadel_sub, tool_name, error, duration_ms)
                        VALUES ($1, $2, $3, $4)
                        """,
                        zitadel_sub,
                        tool_name,
                        error,
                        duration_ms,
                    )
                    await conn.execute(
                        """
                        INSERT INTO usage_daily (zitadel_sub, day, call_count)
                        VALUES ($1, $2, 1)
                        ON CONFLICT (zitadel_sub, day) DO UPDATE
                        SET call_count = usage_daily.call_count + 1
                        """,
                        zitadel_sub,
                        today,
                    )

            # Update in-memory cache
            cached = self._cache.get(zitadel_sub)
            if cached and cached[0] == today:
                self._cache[zitadel_sub] = (today, cached[1] + 1, cached[2])
            else:
                self._cache.pop(zitadel_sub, None)

        except Exception:
            logger.exception(f"Failed to record usage for {zitadel_sub}")

    def record_usage_fire_and_forget(
        self,
        zitadel_sub: str,
        tool_name: str,
        error: bool = False,
        duration_ms: Optional[int] = None,
    ) -> None:
        """Schedule usage recording without awaiting. Non-blocking."""
        try:
            asyncio.get_event_loop().create_task(
                self.record_usage(zitadel_sub, tool_name, error, duration_ms)
            )
        except RuntimeError:
            logger.debug("No event loop — skipping usage recording")
