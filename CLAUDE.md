# CLAUDE.md -- Project context for Claude Code

## What this project is

**odoo-mcp-pro** -- a B2B SaaS MCP server connecting Claude AI to Odoo ERP. Runs as a
multi-tenant managed service (Postgres + Zitadel Cloud + Docker) or locally via stdio
for personal use.

Originally forked from [ivnvxd/mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo) (MPL-2.0),
now a standalone product with multi-tenant architecture, admin panel, JSON/2 client, and OAuth 2.1.

## Current state

- Multi-tenant SaaS: one server, many customers (Zitadel orgs -> Postgres tenants -> Odoo instances)
- Admin panel for tenant management and user self-service setup
- JSON/2 client for Odoo 19+ (recommended), XML-RPC for Odoo 14-18 (legacy)
- OAuth 2.1 via Zitadel Cloud for managed deployments
- Stdio mode still works for local/personal use (no Postgres needed)
- 35+ test files, 437+ unit tests, all mocked

## Architecture

### Multi-tenant (managed SaaS)

```
Claude.ai -> OAuth 2.1 -> MCP Server -> Odoo (customer A)
                              |
                          Postgres (tenants, user_connections, admins)
                              |
                          Zitadel Cloud (identity, orgs)
```

- 1 Zitadel org = 1 Odoo instance (mapped via tenants table)
- Each user has their own Odoo API key (stored in user_connections)
- ConnectionRegistry caches connections per user (30 min TTL)
- Admin panel at /admin for tenant CRUD and user self-service

### Single-tenant (stdio)

```
Claude Code --stdio--> odoo-mcp-pro (local process) --> Odoo
```

No Postgres, no Zitadel. Connection config from env vars.

### Connection factory

```
ODOO_API_VERSION=json2   ->  OdooJSON2Connection   (Odoo 19+)
ODOO_API_VERSION=xmlrpc  ->  OdooConnection        (Odoo 14-18)
```

## Key files

| File | Role |
|------|------|
| `server.py` | Factory pattern, OAuth wiring, FastMCP setup, multi-tenant routing |
| `registry.py` | ConnectionRegistry -- maps authenticated users to Odoo connections |
| `admin/app.py` | Admin panel FastAPI app factory |
| `admin/routes.py` | Admin CRUD routes + self-service setup (/admin/setup) |
| `admin/db.py` | Postgres DatabaseManager (tenants, user_connections, admins) |
| `admin/auth.py` | OAuth login flow, session cookies, CSRF tokens |
| `admin/templates/` | Jinja2 HTML templates for admin panel |
| `connection_protocol.py` | Protocol class defining the connection interface |
| `odoo_json2_connection.py` | JSON/2 client using httpx |
| `odoo_connection.py` | XML-RPC client (Odoo 14-18) |
| `oauth.py` | ZitadelTokenVerifier -- token validation via introspection |
| `config.py` | OdooConfig with api_version field |
| `tools.py` | 6 MCP tools with smart field selection |
| `resources.py` | 4 MCP resources (URI-based) |
| `access_control.py` | JSON/2 access control via check_access_rights |

## JSON/2 API key points

- Endpoint: `POST /json/2/{model}/{method}`
- Auth: `Authorization: Bearer <api_key>` header
- Database: `X-Odoo-Database: <db>` header
- Body: flat JSON with named args, `ids` and `context` are top-level keys
- Create/write use `vals` (not `values`)
- Responses are raw JSON (no RPC envelope)
- Errors return proper HTTP status codes (401, 403, 404, 422, 500)

## Config

### Odoo connection (single-tenant / stdio)

| Env var | Values | Default |
|---------|--------|---------|
| `ODOO_API_VERSION` | `json2`, `xmlrpc` | `xmlrpc` |
| `ODOO_URL` | URL | required |
| `ODOO_DB` | database name | required for json2 |
| `ODOO_API_KEY` | API key | required for json2 |
| `ODOO_MCP_TRANSPORT` | `stdio`, `streamable-http` | `stdio` |
| `ODOO_MCP_HOST` | bind address | `localhost` |
| `ODOO_MCP_PORT` | port | `8000` |

### Multi-tenant (managed SaaS)

| Env var | Description |
|---------|-------------|
| `DATABASE_URL` | Postgres connection string (enables multi-tenant mode) |
| `ADMIN_SESSION_SECRET` | Secret for session cookie signing |
| `ADMIN_BOOTSTRAP_SUB` | Zitadel subject ID of initial admin user |
| `ADMIN_BOOTSTRAP_EMAIL` | Email of initial admin user |
| `ADMIN_BASE_URL` | Public URL of admin panel (for redirect URIs) |
| `ADMIN_OAUTH_CLIENT_ID` | Zitadel OIDC client ID for admin login |
| `ADMIN_DEV_LOGIN` | Set to `true` for dev login without Zitadel |

### OAuth 2.1 (MCP token validation)

| Env var | Description |
|---------|-------------|
| `OAUTH_ISSUER_URL` | Zitadel instance URL (enables OAuth when set) |
| `ZITADEL_INTROSPECTION_URL` | Token introspection endpoint |
| `ZITADEL_CLIENT_ID` | Service user client ID (for introspection) |
| `ZITADEL_CLIENT_SECRET` | Service user client secret |
| `OAUTH_RESOURCE_SERVER_URL` | Public URL of this MCP server (for RFC 9728) |
| `OAUTH_EXPECTED_AUDIENCE` | Optional: Zitadel app/project ID |

## Development setup

```bash
uv venv --python 3.10
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Testing

```bash
pytest tests/               # unit tests (mocked)
pytest tests/ -x -q         # quick run, stop on first failure
```

## Conventions

- Follow existing code style (ruff configured in pyproject.toml)
- Keep JSON/2 client in separate file -- do not modify odoo_connection.py
- Both connection classes must satisfy OdooConnectionProtocol
- Shared exceptions live in `exceptions.py`
- No new dependencies without discussion (httpx already available)
- Admin panel uses Jinja2 templates with Tailwind CSS (via CDN)
- Tenant = Odoo instance linked to Zitadel org
- UserConnection = user's API key for a specific tenant

## Deployment

Multi-tenant deployment uses docker-compose with:
- MCP server container (FastMCP + admin panel)
- Postgres container
- Caddy container (TLS reverse proxy)
- Zitadel Cloud for identity (external, not in docker-compose)

See [SETUP.md](SETUP.md) for deployment instructions.
