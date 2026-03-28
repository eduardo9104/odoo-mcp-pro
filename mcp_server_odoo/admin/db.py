"""PostgreSQL database manager for admin panel.

Manages user connections and admin users.
Uses asyncpg for async PostgreSQL access.

Terminology:
- UserConnection: a user's Odoo API key (self-service, one per user)
- Admin: a Zitadel subject with admin privileges
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import asyncpg

from .encryption import decrypt_api_key, encrypt_api_key

logger = logging.getLogger(__name__)

# Schema version for migrations
SCHEMA_VERSION = 4

SCHEMA_SQL = """
-- Admin users (Zitadel subjects)
CREATE TABLE IF NOT EXISTS admins (
    id          SERIAL PRIMARY KEY,
    zitadel_sub TEXT NOT NULL UNIQUE,
    email       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- User connections: self-service, each user manages their own Odoo connection
-- v3: one connection per user (no tenant dependency)
CREATE TABLE IF NOT EXISTS user_connections (
    id           SERIAL PRIMARY KEY,
    zitadel_sub  TEXT NOT NULL UNIQUE,
    email        TEXT,
    odoo_url     TEXT NOT NULL,
    odoo_api_key TEXT NOT NULL,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at TIMESTAMPTZ DEFAULT NOW()
);

-- v4: Usage tracking and rate limiting
CREATE TABLE IF NOT EXISTS usage_plans (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    daily_limit INTEGER NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO usage_plans (name, daily_limit) VALUES ('free', 1000)
ON CONFLICT (name) DO NOTHING;

ALTER TABLE user_connections ADD COLUMN IF NOT EXISTS plan_id INTEGER REFERENCES usage_plans(id);

CREATE TABLE IF NOT EXISTS usage_log (
    id           BIGSERIAL PRIMARY KEY,
    zitadel_sub  TEXT NOT NULL,
    tool_name    TEXT NOT NULL,
    called_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_ms  INTEGER,
    error        BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_usage_log_sub_called ON usage_log (zitadel_sub, called_at);

CREATE TABLE IF NOT EXISTS usage_daily (
    id           BIGSERIAL PRIMARY KEY,
    zitadel_sub  TEXT NOT NULL,
    day          DATE NOT NULL,
    call_count   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (zitadel_sub, day)
);
"""


@dataclass
class UserConnection:
    id: int
    zitadel_sub: str
    email: Optional[str]
    odoo_url: str
    odoo_api_key: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    plan_id: Optional[int] = None


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
        """Initialize database schema (v3, no migrations)."""
        async with self._pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)
            row = await conn.fetchrow(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            )
            if not row:
                await conn.execute(
                    "INSERT INTO schema_version (version) VALUES ($1)", SCHEMA_VERSION
                )

        # Bootstrap admin if configured
        bootstrap_sub = os.getenv("ADMIN_BOOTSTRAP_SUB", "").strip()
        bootstrap_email = os.getenv("ADMIN_BOOTSTRAP_EMAIL", "").strip()
        if bootstrap_sub:
            await self.ensure_admin(bootstrap_sub, bootstrap_email or None)

    # --- User Connections (self-service, one per user) ---

    async def get_user_connection_by_sub(self, zitadel_sub: str) -> Optional[UserConnection]:
        """Get a user's Odoo connection by their Zitadel subject ID."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM user_connections WHERE zitadel_sub = $1",
                zitadel_sub,
            )
            if not row:
                return None
            uc = UserConnection(**dict(row))
            uc.odoo_api_key = decrypt_api_key(uc.odoo_api_key)
            return uc

    async def upsert_user_connection(
        self,
        zitadel_sub: str,
        odoo_url: str,
        odoo_api_key: str,
        email: Optional[str] = None,
    ) -> UserConnection:
        """Create or update a user's Odoo connection."""
        encrypted_key = encrypt_api_key(odoo_api_key)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO user_connections (zitadel_sub, email, odoo_url, odoo_api_key)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (zitadel_sub) DO UPDATE SET
                       email = COALESCE($2, user_connections.email),
                       odoo_url = $3,
                       odoo_api_key = $4,
                       is_active = TRUE,
                       updated_at = NOW()
                   RETURNING *""",
                zitadel_sub,
                email,
                odoo_url,
                encrypted_key,
            )
            uc = UserConnection(**dict(row))
            # Decrypt for the returned object so callers get the plaintext key
            uc.odoo_api_key = decrypt_api_key(uc.odoo_api_key)
            logger.info(f"Upserted user connection: {zitadel_sub} -> {odoo_url}")
            return uc

    async def list_all_connections(self) -> list:
        """List all user connections (for admin dashboard)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM user_connections ORDER BY created_at DESC")
            connections = [UserConnection(**dict(row)) for row in rows]
            for uc in connections:
                uc.odoo_api_key = decrypt_api_key(uc.odoo_api_key)
            return connections

    async def delete_user_connection(self, connection_id: int) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM user_connections WHERE id = $1", connection_id)
            return result == "DELETE 1"

    async def delete_user_connection_by_sub(self, zitadel_sub: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM user_connections WHERE zitadel_sub = $1", zitadel_sub
            )
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
