# Architecture

Technical architecture of odoo-mcp-pro. For a quick overview, see the [README](README.md).

## Overview

odoo-mcp-pro is a multi-tenant MCP server that connects Claude AI to Odoo ERP instances. It supports two deployment modes: managed SaaS (multi-tenant with Postgres and Zitadel) and self-hosted (single-tenant, stdio).

```
+-----------+     OAuth 2.1    +--------------+    JSON/2     +----------+
| Claude.ai |---------------->| MCP Server   |-------------->| Odoo     |
| (browser) |                 | (Hetzner)    |               | (cust A) |
+-----------+                 +------+-------+               +----------+
                                     |
                              +------+-------+
                              |  Postgres    |
                              |  tenants +   |
                              |  api_keys    |
                              +------+-------+
                                     |
                              +------+-------+
                              |  Zitadel     |
                              |  Cloud       |
                              |  (identity)  |
                              +--------------+
```

---

## Components

### MCP Server

Built on [FastMCP](https://github.com/modelcontextprotocol/python-sdk). Exposes 6 tools and 4 resources for Odoo data access. In multi-tenant mode, routes each authenticated user to the correct Odoo instance based on their Zitadel organization.

### Postgres

Stores three tables (see [Data model](#data-model)). Only used in multi-tenant mode. In self-hosted/stdio mode, no database is needed.

### Zitadel Cloud

Managed identity provider. Handles:
- User authentication (OAuth 2.1 + PKCE)
- Organization management (one org per customer)
- Token issuance and introspection
- Optional federation (Microsoft Entra ID)

### Caddy

Reverse proxy with automatic TLS. Sits in front of the MCP server and admin panel.

### Admin Panel

FastAPI web app mounted at `/admin`. Provides:
- **Admin dashboard**: manage tenants, view user connections
- **Self-service setup** (`/admin/setup`): users enter their Odoo API key
- OAuth login via Zitadel (OIDC Authorization Code + PKCE)

---

## Data model

### `tenants`

Each tenant represents one Odoo instance linked to a Zitadel organization.

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `name` | TEXT | Display name |
| `slug` | TEXT | URL-friendly identifier (unique) |
| `zitadel_org_id` | TEXT | Zitadel organization ID (unique) |
| `odoo_url` | TEXT | Odoo instance URL |
| `odoo_db` | TEXT | Odoo database name (empty for Odoo.sh) |
| `api_version` | TEXT | Always `json2` for new deployments |
| `is_active` | BOOLEAN | Soft delete flag |

### `user_connections`

Each row maps a Zitadel user to a tenant with their Odoo API key.

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `zitadel_sub` | TEXT | Zitadel subject ID |
| `email` | TEXT | User email (informational) |
| `tenant_id` | INTEGER | FK to tenants |
| `odoo_api_key` | TEXT | User's personal Odoo API key |
| `is_active` | BOOLEAN | Soft delete flag |

Unique constraint on `(zitadel_sub, tenant_id)`.

### `admins`

Super admins (Pantalytics) who can manage all tenants.

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `zitadel_sub` | TEXT | Zitadel subject ID (unique) |
| `email` | TEXT | Admin email |

---

## Auth flow

### MCP tool call (multi-tenant)

```
Claude.ai
    |
    | 1. POST /mcp (tool call)
    |    Authorization: Bearer <user_token>
    v
FastMCP (BearerAuthBackend)
    |
    | 2. Token introspection -> Zitadel
    |    POST /oauth/v2/introspect (Basic Auth: service_client)
    |    Response includes: sub, org_id, active=true
    |
    | 3. ConnectionRegistry.get_connection(sub, org_id)
    |    - Look up tenant by org_id
    |    - Look up user_connection (sub + tenant_id)
    |    - Create/cache OdooJSON2Connection with user's API key
    |
    | 4. Execute tool (search_records, get_record, etc.)
    |    POST /json/2/{model}/{method}
    |    Authorization: Bearer <user_api_key>
    v
Odoo instance
```

### Admin panel login

```
User -> /admin/login -> Zitadel (OIDC + PKCE) -> /admin/callback
    |
    | Extract from userinfo:
    |   sub, email, org_id, org_name
    |
    | Check admins table:
    |   admin? -> /admin/ (dashboard)
    |   user?  -> /admin/setup (self-service)
```

---

## User onboarding flow

1. **Admin** creates a Zitadel organization for the customer
2. **Admin** creates a tenant in the admin panel (name, Odoo URL, org ID)
3. **Admin** shares the setup link with the customer
4. **User** opens setup link, logs in with their company account
5. **User** enters their Odoo API key
6. **User** adds the MCP server URL to Claude.ai
7. Claude authenticates via OAuth, MCP server routes to the right Odoo

---

## Deployment modes

### Managed SaaS (multi-tenant)

Used for production. Single MCP server serves all customers.

| Component | Role |
|-----------|------|
| MCP Server | FastMCP + admin panel, runs in Docker |
| Postgres | Tenant config, user connections, admins |
| Zitadel Cloud | Identity, organizations, OAuth |
| Caddy | Reverse proxy, TLS |

Environment: `DATABASE_URL` set, `OAUTH_ISSUER_URL` set.

### Self-hosted (stdio)

For personal use. No Postgres, no Zitadel, no Docker.

```
Claude Code --stdio--> odoo-mcp-pro (local process) --> Odoo
```

Environment: `ODOO_URL`, `ODOO_API_KEY`, `ODOO_API_VERSION=json2`.

---

## Connection layer

Abstracted behind `OdooConnectionProtocol`. Factory pattern in `server.py`:

```
ODOO_API_VERSION=json2   ->  OdooJSON2Connection   (Odoo 19+, recommended)
ODOO_API_VERSION=xmlrpc  ->  OdooConnection        (Odoo 14-18, legacy)
```

In multi-tenant mode, `ConnectionRegistry` creates and caches connections per user. Connections are evicted after 30 minutes of inactivity.

### JSON/2 API

| Aspect | Details |
|--------|---------|
| Endpoint | `POST /json/2/{model}/{method}` |
| Auth | `Authorization: Bearer <api_key>` |
| Database | `X-Odoo-Database: <db>` header |
| Body | Flat JSON with named args |
| Create/write | Use `vals` (not `values`) |
| Response | Raw JSON (no RPC envelope) |
| Errors | HTTP status codes (401, 403, 404, 422, 500) |

---

## Access control

In JSON/2 mode, the MCP server checks Odoo ACLs before sending requests:

```
POST /json/2/{model}/check_access_rights
{"operation": "read", "raise_exception": false}
-> true / false
```

Results are cached per model for 5 minutes. This prevents unexpected 403s and gives clear error messages.

---

## Key design decisions

- **1 Zitadel org = 1 Odoo instance.** The org_id from the OAuth token determines which tenant to use.
- **API keys = Odoo permissions.** Each user has their own Odoo API key. Odoo enforces ACLs and record rules.
- **Zitadel for identity only.** Zitadel handles authentication and organization structure. It does not store Odoo credentials.
- **Stateless proxy.** The MCP server does not store or cache Odoo data. Connections are cached for performance but contain no business data.
- **Self-service setup.** Users enter their own API key. Admins only need to create the tenant and share the link.

---

## Key files

| File | Role |
|------|------|
| `server.py` | Entry point, factory pattern, OAuth wiring, FastMCP setup |
| `registry.py` | ConnectionRegistry -- maps users to Odoo connections |
| `admin/app.py` | Admin panel FastAPI app factory |
| `admin/routes.py` | Admin CRUD routes + self-service setup |
| `admin/db.py` | Postgres database manager (tenants, users, admins) |
| `admin/auth.py` | OAuth login flow, session management, CSRF |
| `odoo_json2_connection.py` | JSON/2 client (httpx, Odoo 19+) |
| `odoo_connection.py` | XML-RPC client (stdlib, Odoo 14-18) |
| `connection_protocol.py` | Protocol class defining the connection interface |
| `oauth.py` | ZitadelTokenVerifier -- token validation via introspection |
| `config.py` | OdooConfig dataclass, loaded from env vars |
| `tools.py` | 6 MCP tools with smart field selection |
| `resources.py` | 4 MCP resources (URI-based read access) |
| `access_control.py` | JSON/2 access control via check_access_rights |

All source files live in `mcp_server_odoo/`. Tests in `tests/`.
