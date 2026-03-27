"""Connection registry for multi-tenant MCP server.

Maps authenticated users (Zitadel subject IDs) to their Odoo connections.
Connections are lazily created and cached with a configurable TTL.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict

from .access_control import AccessController
from .config import OdooConfig
from .connection_protocol import OdooConnectionProtocol
from .exceptions import OdooConnectionError
from .odoo_connection import OdooConnection
from .odoo_json2_connection import OdooJSON2Connection
from .performance import PerformanceManager
from .version_detect import detect_api_version

logger = logging.getLogger(__name__)

# Default connection idle TTL: 30 minutes
DEFAULT_TTL = 1800


@dataclass
class CachedConnection:
    """A cached Odoo connection with metadata."""

    connection: OdooConnectionProtocol
    access_controller: AccessController
    config: OdooConfig
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)

    def touch(self):
        self.last_used = time.time()

    def is_expired(self, ttl: int) -> bool:
        return (time.time() - self.last_used) > ttl


class ConnectionRegistry:
    """Maps authenticated users to their Odoo connections.

    Each user may have access to one or more Odoo tenants.
    Connections are created on first use and cached.
    """

    def __init__(self, db_manager, ttl: int = DEFAULT_TTL):
        """Initialize registry.

        Args:
            db_manager: DatabaseManager instance for looking up user configs
            ttl: Connection idle TTL in seconds
        """
        self.db_manager = db_manager
        self.ttl = ttl
        self._connections: Dict[str, CachedConnection] = {}

    async def get_connection(self, zitadel_sub: str) -> CachedConnection:
        """Get or create an Odoo connection for an authenticated user.

        Looks up the user's self-service Odoo connection from user_connections.

        Args:
            zitadel_sub: Zitadel subject ID of the authenticated user

        Returns:
            CachedConnection with connection and access controller

        Raises:
            OdooConnectionError: If user has no connection or connection fails
        """
        # Check cache
        cached = self._connections.get(zitadel_sub)
        if cached and not cached.is_expired(self.ttl):
            cached.touch()
            return cached

        # Remove expired entry if present
        if cached:
            self._close_connection(zitadel_sub)

        # Look up user's connection
        user_conn = await self.db_manager.get_user_connection_by_sub(zitadel_sub)
        if not user_conn or not user_conn.is_active:
            setup_url = os.getenv("ADMIN_BASE_URL", "").rstrip("/")
            if setup_url:
                raise OdooConnectionError(
                    f"No Odoo connection configured. "
                    f"Set up your connection at {setup_url}/admin/setup"
                )
            raise OdooConnectionError(
                f"User {zitadel_sub} has no Odoo connection configured. "
                "Set up your connection first."
            )

        # Auto-detect API version from Odoo server
        api_version, server_version = detect_api_version(user_conn.odoo_url)
        logger.info(
            f"Auto-detected api_version={api_version} for {user_conn.odoo_url}"
            f" (Odoo {server_version or 'unknown'})"
        )

        # Create connection with detected API version
        config = OdooConfig(
            url=user_conn.odoo_url,
            database="",
            api_key=user_conn.odoo_api_key,
            api_version=api_version,
        )

        try:
            conn: OdooConnectionProtocol
            if api_version == "json2":
                conn = OdooJSON2Connection(config)
            else:
                conn = OdooConnection(
                    config, performance_manager=PerformanceManager(config)
                )
            conn.connect()
            conn.authenticate()
        except Exception as e:
            raise OdooConnectionError(f"Failed to connect to {user_conn.odoo_url}: {e}") from e

        access_controller = AccessController(config, connection=conn)

        cached = CachedConnection(
            connection=conn,
            access_controller=access_controller,
            config=config,
        )
        self._connections[zitadel_sub] = cached

        logger.info(f"Created connection for user {zitadel_sub} to {user_conn.odoo_url}")
        return cached

    def _close_connection(self, key: str):
        """Close and remove a cached connection."""
        cached = self._connections.pop(key, None)
        if cached:
            try:
                cached.connection.disconnect(suppress_logging=True)
            except Exception:
                pass

    def revoke_user(self, zitadel_sub: str):
        """Close and remove all connections for a user."""
        keys_to_remove = [k for k in self._connections if k == zitadel_sub]
        for key in keys_to_remove:
            self._close_connection(key)
        if keys_to_remove:
            logger.info(f"Revoked {len(keys_to_remove)} connection(s) for user {zitadel_sub}")

    async def cleanup_expired(self):
        """Remove expired connections. Call periodically."""
        expired = [key for key, cached in self._connections.items() if cached.is_expired(self.ttl)]
        for key in expired:
            self._close_connection(key)
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired connection(s)")

    def close_all(self):
        """Close all connections. Called on shutdown."""
        for key in list(self._connections):
            self._close_connection(key)
        logger.info("Closed all connections")

    @property
    def active_count(self) -> int:
        return len(self._connections)
