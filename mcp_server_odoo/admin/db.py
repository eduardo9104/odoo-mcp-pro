"""PostgreSQL database manager for admin panel.

Manages tenant (Odoo instance) configurations and user-connection mappings.
Uses asyncpg for async PostgreSQL access.

Terminology:
- Tenant: an Odoo instance linked to a Zitadel organization
- UserConnection: a user's API key for a specific tenant
"""

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import asyncpg

logger = logging.getLogger(__name__)

# Schema version for migrations
SCHEMA_VERSION = 2

SCHEMA_SQL = """
-- Tenants: Odoo instances linked to Zitadel organizations
CREATE TABLE IF NOT EXISTS tenants (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL UNIQUE,
    zitadel_org_id  TEXT UNIQUE,
    odoo_url        TEXT NOT NULL,
    odoo_db         TEXT NOT NULL DEFAULT '',
    api_version     TEXT NOT NULL DEFAULT 'json2',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Admin users (Zitadel subjects)
CREATE TABLE IF NOT EXISTS admins (
    id          SERIAL PRIMARY KEY,
    zitadel_sub TEXT NOT NULL UNIQUE,
    email       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- User connections: each user has their own Odoo API key per tenant
CREATE TABLE IF NOT EXISTS user_connections (
    id           SERIAL PRIMARY KEY,
    zitadel_sub  TEXT NOT NULL,
    email        TEXT,
    tenant_id    INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    odoo_api_key TEXT NOT NULL,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(zitadel_sub, tenant_id)
);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at TIMESTAMPTZ DEFAULT NOW()
);
"""

# Migration from v1 (odoo_databases/user_databases) to v2 (tenants/user_connections)
MIGRATION_V1_TO_V2 = """
-- Rename odoo_databases -> tenants (add zitadel_org_id column)
ALTER TABLE IF EXISTS odoo_databases RENAME TO tenants;
ALTER TABLE IF EXISTS tenants ADD COLUMN IF NOT EXISTS zitadel_org_id TEXT UNIQUE;

-- Rename user_databases -> user_connections (rename odoo_database_id -> tenant_id)
ALTER TABLE IF EXISTS user_databases RENAME TO user_connections;
ALTER TABLE IF EXISTS user_connections RENAME COLUMN odoo_database_id TO tenant_id;

-- Update schema version
INSERT INTO schema_version (version) VALUES (2);
"""


@dataclass
class Tenant:
    id: int
    name: str
    slug: str
    zitadel_org_id: Optional[str]
    odoo_url: str
    odoo_db: str
    api_version: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass
class UserConnection:
    id: int
    zitadel_sub: str
    email: Optional[str]
    tenant_id: int
    odoo_api_key: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass
class Admin:
    id: int
    zitadel_sub: str
    email: Optional[str]
    created_at: datetime


def get_database_url() -> str:
    """Get PostgreSQL connection URL from environment."""
    return os.getenv(
        "DATABASE_URL",
        "postgresql://mcp:mcp@localhost:5432/mcp_admin",
    )


class DatabaseManager:
    """Async PostgreSQL database manager."""

    def __init__(self, database_url: Optional[str] = None):
        self._database_url = database_url or get_database_url()
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """Create connection pool and initialize schema."""
        self._pool = await asyncpg.create_pool(self._database_url, min_size=2, max_size=10)
        await self._init_schema()
        logger.info("Database connected and schema initialized")

    async def close(self):
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def _init_schema(self):
        """Initialize database schema."""
        async with self._pool.acquire() as conn:
            # Check if we need to migrate from v1
            try:
                row = await conn.fetchrow(
                    "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
                )
                current_version = row["version"] if row else 0
            except Exception:
                current_version = 0

            if current_version == 0:
                # Fresh install: create v2 schema directly
                await conn.execute(SCHEMA_SQL)
                row = await conn.fetchrow(
                    "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
                )
                if not row:
                    await conn.execute(
                        "INSERT INTO schema_version (version) VALUES ($1)", SCHEMA_VERSION
                    )
            elif current_version < 2:
                # Migrate from v1 to v2
                try:
                    await conn.execute(MIGRATION_V1_TO_V2)
                    logger.info("Migrated database schema from v1 to v2")
                except Exception as e:
                    logger.warning(f"Migration v1->v2 skipped (may already be done): {e}")

        # Bootstrap admin if configured
        bootstrap_sub = os.getenv("ADMIN_BOOTSTRAP_SUB", "").strip()
        bootstrap_email = os.getenv("ADMIN_BOOTSTRAP_EMAIL", "").strip()
        if bootstrap_sub:
            await self.ensure_admin(bootstrap_sub, bootstrap_email or None)

    # --- Tenants ---

    async def list_tenants(self, active_only: bool = True) -> List[Tenant]:
        async with self._pool.acquire() as conn:
            query = "SELECT * FROM tenants"
            if active_only:
                query += " WHERE is_active = TRUE"
            query += " ORDER BY name"
            rows = await conn.fetch(query)
            return [Tenant(**dict(r)) for r in rows]

    async def get_tenant(self, tenant_id: int) -> Optional[Tenant]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tenants WHERE id = $1", tenant_id)
            return Tenant(**dict(row)) if row else None

    async def get_tenant_by_slug(self, slug: str) -> Optional[Tenant]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tenants WHERE slug = $1", slug)
            return Tenant(**dict(row)) if row else None

    async def get_tenant_by_org_id(self, org_id: str) -> Optional[Tenant]:
        """Find a tenant by its Zitadel organization ID."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tenants WHERE zitadel_org_id = $1", org_id)
            return Tenant(**dict(row)) if row else None

    async def create_tenant(
        self,
        name: str,
        slug: str,
        odoo_url: str,
        odoo_db: str = "",
        api_version: str = "json2",
        zitadel_org_id: Optional[str] = None,
    ) -> Tenant:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO tenants (name, slug, odoo_url, odoo_db, api_version, zitadel_org_id)
                   VALUES ($1, $2, $3, $4, $5, $6) RETURNING *""",
                name,
                slug,
                odoo_url,
                odoo_db,
                api_version,
                zitadel_org_id,
            )
            tenant = Tenant(**dict(row))
            logger.info(f"Created tenant: {name} ({odoo_url})")
            return tenant

    async def update_tenant(self, tenant_id: int, **kwargs) -> Optional[Tenant]:
        allowed = {
            "name",
            "slug",
            "odoo_url",
            "odoo_db",
            "api_version",
            "is_active",
            "zitadel_org_id",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return await self.get_tenant(tenant_id)

        sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(updates))
        sets += ", updated_at = NOW()"
        values = [tenant_id] + list(updates.values())

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE tenants SET {sets} WHERE id = $1 RETURNING *", *values
            )
            return Tenant(**dict(row)) if row else None

    async def get_tenant_by_url(self, odoo_url: str) -> Optional[Tenant]:
        """Find a tenant by its Odoo URL."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tenants WHERE odoo_url = $1", odoo_url)
            return Tenant(**dict(row)) if row else None

    async def get_or_create_tenant_by_url(
        self, odoo_url: str, odoo_db: str = "", api_version: str = "json2"
    ) -> Tenant:
        """Find existing tenant by URL or create a new one.

        Auto-generates name and slug from the hostname.
        """
        existing = await self.get_tenant_by_url(odoo_url)
        if existing:
            return existing

        # Generate name and slug from hostname
        from urllib.parse import urlparse

        parsed = urlparse(odoo_url)
        hostname = parsed.hostname or "odoo"
        # Use first part of hostname as name (e.g. "mycompany" from "mycompany.odoo.com")
        name = hostname.split(".")[0]
        slug = re.sub(r"[^\w-]", "", name.lower())

        # Ensure uniqueness by appending a number if needed
        base_name = name
        base_slug = slug
        counter = 1
        while True:
            try:
                return await self.create_tenant(
                    name=name,
                    slug=slug,
                    odoo_url=odoo_url,
                    odoo_db=odoo_db,
                    api_version=api_version,
                )
            except Exception:
                counter += 1
                name = f"{base_name}-{counter}"
                slug = f"{base_slug}-{counter}"
                if counter > 100:
                    raise

    async def delete_tenant(self, tenant_id: int) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
            return result == "DELETE 1"

    # --- User Connections ---

    async def list_users_for_tenant(self, tenant_id: int) -> List[UserConnection]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM user_connections WHERE tenant_id = $1 ORDER BY email, zitadel_sub",
                tenant_id,
            )
            return [UserConnection(**dict(r)) for r in rows]

    async def get_user_connection(
        self, zitadel_sub: str, tenant_id: int
    ) -> Optional[UserConnection]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM user_connections WHERE zitadel_sub = $1 AND tenant_id = $2",
                zitadel_sub,
                tenant_id,
            )
            return UserConnection(**dict(row)) if row else None

    async def get_user_connections_with_info(self, zitadel_sub: str) -> list:
        """Get all connection mappings for a user, including tenant details."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT uc.*, t.name as db_name, t.odoo_url, t.odoo_db,
                          t.is_active as db_is_active
                   FROM user_connections uc
                   JOIN tenants t ON uc.tenant_id = t.id
                   WHERE uc.zitadel_sub = $1
                   ORDER BY t.name""",
                zitadel_sub,
            )
            return [dict(r) for r in rows]

    async def get_user_connections(self, zitadel_sub: str) -> List[UserConnection]:
        """Get all active connection mappings for a user."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT uc.* FROM user_connections uc
                   JOIN tenants t ON uc.tenant_id = t.id
                   WHERE uc.zitadel_sub = $1 AND uc.is_active = TRUE AND t.is_active = TRUE
                   ORDER BY t.name""",
                zitadel_sub,
            )
            return [UserConnection(**dict(r)) for r in rows]

    async def create_user_connection(
        self, zitadel_sub: str, tenant_id: int, odoo_api_key: str, email: Optional[str] = None
    ) -> UserConnection:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO user_connections (zitadel_sub, email, tenant_id, odoo_api_key)
                   VALUES ($1, $2, $3, $4) RETURNING *""",
                zitadel_sub,
                email,
                tenant_id,
                odoo_api_key,
            )
            mapping = UserConnection(**dict(row))
            logger.info(f"Created user connection: {zitadel_sub} -> tenant {tenant_id}")
            return mapping

    async def update_user_connection(
        self, connection_id: int, **kwargs
    ) -> Optional[UserConnection]:
        allowed = {"odoo_api_key", "email", "is_active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return None

        sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(updates))
        sets += ", updated_at = NOW()"
        values = [connection_id] + list(updates.values())

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE user_connections SET {sets} WHERE id = $1 RETURNING *", *values
            )
            return UserConnection(**dict(row)) if row else None

    async def delete_user_connection(self, connection_id: int) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM user_connections WHERE id = $1", connection_id)
            return result == "DELETE 1"

    # --- Admins ---

    async def is_admin(self, zitadel_sub: str) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id FROM admins WHERE zitadel_sub = $1", zitadel_sub)
            return row is not None

    async def ensure_admin(self, zitadel_sub: str, email: Optional[str] = None) -> Admin:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO admins (zitadel_sub, email)
                   VALUES ($1, $2)
                   ON CONFLICT (zitadel_sub) DO UPDATE SET email = COALESCE($2, admins.email)
                   RETURNING *""",
                zitadel_sub,
                email,
            )
            admin = Admin(**dict(row))
            logger.info(f"Ensured admin: {zitadel_sub} ({email})")
            return admin

    async def list_admins(self) -> List[Admin]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM admins ORDER BY email, zitadel_sub")
            return [Admin(**dict(r)) for r in rows]

    async def remove_admin(self, admin_id: int) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM admins WHERE id = $1", admin_id)
            return result == "DELETE 1"
