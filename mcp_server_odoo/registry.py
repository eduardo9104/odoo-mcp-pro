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
from .exceptions import OdooConnectionError
from .odoo_json2_connection import OdooJSON2Connection

logger = logging.getLogger(__name__)

# Default connection idle TTL: 30 minutes
DEFAULT_TTL = 1800


@dataclass
class CachedConnection:
    """A cached Odoo connection with metadata."""

    connection: OdooJSON2Connection
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

    def _cache_key(self, zitadel_sub: str, tenant_id: int) -> str:
        return f"{zitadel_sub}:{tenant_id}"

    async def get_connection(self, zitadel_sub: str, org_id: str = "") -> CachedConnection:
        """Get or create an Odoo connection for an authenticated user.

        Resolution order:
        1. If org_id is provided, find the tenant linked to that Zitadel org
        2. Otherwise, use the user's first (or only) active connection

        Args:
            zitadel_sub: Zitadel subject ID of the authenticated user
            org_id: Optional Zitadel organization ID (from token claims)

        Returns:
            CachedConnection with connection and access controller

        Raises:
            OdooConnectionError: If user has no connection or connection fails
        """
        mapping = None

        # If org_id provided, try to find tenant by org_id first
        if org_id:
            tenant = await self.db_manager.get_tenant_by_org_id(org_id)
            if tenant:
                mapping = await self.db_manager.get_user_connection(zitadel_sub, tenant.id)
                if not mapping or not mapping.is_active:
                    setup_url = os.getenv("ADMIN_BASE_URL", "").rstrip("/")
                    if setup_url:
                        raise OdooConnectionError(
                            f"No Odoo connection configured for your organization. "
                            f"Set up your connection at {setup_url}/admin/setup"
                        )
                    raise OdooConnectionError(
                        f"User {zitadel_sub} is not approved for organization {org_id}. "
                        "Contact an administrator to get access."
                    )

        if not mapping:
            # Fall back to user's first active connection
            mappings = await self.db_manager.get_user_connections(zitadel_sub)
            if not mappings:
                setup_url = os.getenv("ADMIN_BASE_URL", "").rstrip("/")
                if setup_url:
                    raise OdooConnectionError(
                        f"No Odoo connection configured. "
                        f"Set up your connection at {setup_url}/admin/setup"
                    )
                raise OdooConnectionError(
                    f"User {zitadel_sub} has no approved tenant access. "
                    "Contact an administrator to get access."
                )
            mapping = mappings[0]

        # Check cache
        key = self._cache_key(zitadel_sub, mapping.tenant_id)
        cached = self._connections.get(key)
        if cached and not cached.is_expired(self.ttl):
            cached.touch()
            return cached

        # Remove expired entry if present
        if cached:
            self._close_connection(key)

        # Look up tenant config
        tenant = await self.db_manager.get_tenant(mapping.tenant_id)
        if not tenant or not tenant.is_active:
            raise OdooConnectionError(f"Tenant {mapping.tenant_id} is not active")

        # Create connection
        config = OdooConfig(
            url=tenant.odoo_url,
            database=tenant.odoo_db,
            api_key=mapping.odoo_api_key,
            api_version=tenant.api_version,
        )

        try:
            conn = OdooJSON2Connection(config)
            conn.connect()
            conn.authenticate()
        except Exception as e:
            raise OdooConnectionError(f"Failed to connect to {tenant.odoo_url}: {e}") from e

        access_controller = AccessController(config, connection=conn)

        cached = CachedConnection(
            connection=conn,
            access_controller=access_controller,
            config=config,
        )
        self._connections[key] = cached

        logger.info(
            f"Created connection for user {zitadel_sub} to {tenant.name} ({tenant.odoo_url})"
        )
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
        keys_to_remove = [k for k in self._connections if k.startswith(f"{zitadel_sub}:")]
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
