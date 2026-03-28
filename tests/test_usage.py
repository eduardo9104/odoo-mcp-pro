"""Tests for usage tracking and rate limiting."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_server_odoo.usage import (
    DEFAULT_DAILY_LIMIT,
    RateLimitExceeded,
    UsageTracker,
)


class _AsyncContextManager:
    """Helper to create an object usable as `async with`."""

    def __init__(self, return_value=None):
        self._return_value = return_value

    async def __aenter__(self):
        return self._return_value

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def mock_pool():
    """Create a mock asyncpg pool."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock()
    conn.execute = AsyncMock()
    conn.transaction.return_value = _AsyncContextManager()

    pool = MagicMock()
    pool.acquire.return_value = _AsyncContextManager(conn)

    return pool, conn


@pytest.fixture
def tracker(mock_pool):
    """Create a UsageTracker with mocked pool."""
    pool, _ = mock_pool
    return UsageTracker(pool)


class TestRateLimitExceeded:
    def test_is_validation_error(self):
        """RateLimitExceeded should be a ValidationError subclass."""
        from mcp_server_odoo.error_handling import ValidationError

        exc = RateLimitExceeded(100, 100)
        assert isinstance(exc, ValidationError)

    def test_message_includes_counts(self):
        exc = RateLimitExceeded(100, 150)
        assert "150/100" in str(exc)
        assert "midnight UTC" in str(exc)

    def test_attributes(self):
        exc = RateLimitExceeded(50, 50)
        assert exc.limit == 50
        assert exc.used == 50


class TestCheckRateLimit:
    @pytest.mark.asyncio
    async def test_allows_when_under_limit(self, tracker, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"call_count": 5, "daily_limit": 100}

        # Should not raise
        await tracker.check_rate_limit("user-123")

    @pytest.mark.asyncio
    async def test_raises_when_at_limit(self, tracker, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"call_count": 100, "daily_limit": 100}

        with pytest.raises(RateLimitExceeded) as exc_info:
            await tracker.check_rate_limit("user-123")
        assert exc_info.value.limit == 100
        assert exc_info.value.used == 100

    @pytest.mark.asyncio
    async def test_raises_when_over_limit(self, tracker, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"call_count": 150, "daily_limit": 100}

        with pytest.raises(RateLimitExceeded):
            await tracker.check_rate_limit("user-123")

    @pytest.mark.asyncio
    async def test_allows_unknown_user(self, tracker, mock_pool):
        """Unknown user should pass (will fail later in tool handler)."""
        _, conn = mock_pool
        conn.fetchrow.return_value = None

        # Should not raise
        await tracker.check_rate_limit("unknown-user")

    @pytest.mark.asyncio
    async def test_unlimited_plan(self, tracker, mock_pool):
        """daily_limit=0 means unlimited."""
        _, conn = mock_pool
        conn.fetchrow.return_value = {"call_count": 99999, "daily_limit": 0}

        # Should not raise
        await tracker.check_rate_limit("user-123")

    @pytest.mark.asyncio
    async def test_uses_cache_on_second_call(self, tracker, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"call_count": 5, "daily_limit": 100}

        await tracker.check_rate_limit("user-123")
        await tracker.check_rate_limit("user-123")

        # Should only query DB once (second call uses cache)
        assert conn.fetchrow.call_count == 1

    @pytest.mark.asyncio
    async def test_cache_invalidates_on_new_day(self, tracker, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"call_count": 5, "daily_limit": 100}

        # Warm cache
        await tracker.check_rate_limit("user-123")

        # Simulate next day by setting stale cache
        yesterday = date(2020, 1, 1)
        tracker._cache["user-123"] = (yesterday, 5, 100)

        await tracker.check_rate_limit("user-123")

        # Should query DB again
        assert conn.fetchrow.call_count == 2

    @pytest.mark.asyncio
    async def test_default_daily_limit_is_1000(self):
        """Default daily limit should be 1000."""
        assert DEFAULT_DAILY_LIMIT == 1000

    @pytest.mark.asyncio
    async def test_default_daily_limit_used(self, tracker, mock_pool):
        """When no plan assigned, default limit should be used."""
        _, conn = mock_pool
        conn.fetchrow.return_value = {
            "call_count": DEFAULT_DAILY_LIMIT,
            "daily_limit": DEFAULT_DAILY_LIMIT,
        }

        with pytest.raises(RateLimitExceeded) as exc_info:
            await tracker.check_rate_limit("user-123")
        assert exc_info.value.limit == DEFAULT_DAILY_LIMIT


class TestRecordUsage:
    @pytest.mark.asyncio
    async def test_inserts_log_and_upserts_daily(self, tracker, mock_pool):
        _, conn = mock_pool

        await tracker.record_usage("user-123", "search_records")

        # Should execute 2 statements (INSERT + UPSERT)
        assert conn.execute.call_count == 2
        # First call: INSERT into usage_log
        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert "usage_log" in first_call_sql
        # Second call: UPSERT into usage_daily
        second_call_sql = conn.execute.call_args_list[1][0][0]
        assert "usage_daily" in second_call_sql

    @pytest.mark.asyncio
    async def test_updates_cache_after_recording(self, tracker, mock_pool):
        _, conn = mock_pool
        today = date.today()

        # Pre-populate cache
        tracker._cache["user-123"] = (today, 5, 100)

        await tracker.record_usage("user-123", "search_records")

        # Cache should be incremented
        assert tracker._cache["user-123"] == (today, 6, 100)

    @pytest.mark.asyncio
    async def test_does_not_raise_on_db_error(self, tracker, mock_pool):
        """Recording should never raise — it's fire-and-forget."""
        _, conn = mock_pool
        conn.execute.side_effect = Exception("DB down")

        # Should not raise
        await tracker.record_usage("user-123", "search_records")

    @pytest.mark.asyncio
    async def test_records_error_flag(self, tracker, mock_pool):
        _, conn = mock_pool

        await tracker.record_usage("user-123", "search_records", error=True)

        first_call_args = conn.execute.call_args_list[0][0]
        # error=True should be passed as 3rd positional arg
        assert first_call_args[3] is True

    @pytest.mark.asyncio
    async def test_records_duration(self, tracker, mock_pool):
        _, conn = mock_pool

        await tracker.record_usage("user-123", "search_records", duration_ms=42)

        first_call_args = conn.execute.call_args_list[0][0]
        # duration_ms should be passed as 4th positional arg
        assert first_call_args[4] == 42


class TestFireAndForget:
    def test_creates_task(self, tracker):
        """fire_and_forget should create an asyncio task."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch.object(tracker, "record_usage", new_callable=AsyncMock) as mock_record:
                tracker.record_usage_fire_and_forget("user-123", "search_records")
                # Let the task run
                loop.run_until_complete(asyncio.sleep(0.01))
                mock_record.assert_called_once_with("user-123", "search_records", False, None)
        finally:
            loop.close()
            asyncio.set_event_loop(None)


class TestToolHandlerTracking:
    """Test that OdooToolHandler integrates usage tracking."""

    def test_track_usage_calls_fire_and_forget(self):
        from mcp_server_odoo.tools import OdooToolHandler

        mock_tracker = MagicMock()
        handler = OdooToolHandler(
            app=MagicMock(),
            usage_tracker=mock_tracker,
        )

        handler._track_usage("user-123", "search_records")
        mock_tracker.record_usage_fire_and_forget.assert_called_once_with(
            "user-123", "search_records"
        )

    def test_track_usage_skips_stdio(self):
        from mcp_server_odoo.tools import OdooToolHandler

        mock_tracker = MagicMock()
        handler = OdooToolHandler(
            app=MagicMock(),
            usage_tracker=mock_tracker,
        )

        handler._track_usage("stdio", "search_records")
        mock_tracker.record_usage_fire_and_forget.assert_not_called()

    def test_track_usage_noop_without_tracker(self):
        from mcp_server_odoo.tools import OdooToolHandler

        handler = OdooToolHandler(app=MagicMock())

        # Should not raise
        handler._track_usage("user-123", "search_records")
