# CLAUDE.md -- Instructions for Claude Code

## What this project is

**odoo-mcp-pro** -- a B2B SaaS MCP server connecting Claude AI to Odoo ERP.
Multi-tenant managed service: Postgres + Zitadel Cloud + Docker.

See [architecture.md](architecture.md) for technical details.

## Design principles

1. **Odoo + AI, samen sterker** -- don't replace Odoo, make it more accessible via AI
2. **Use the interface that fits** -- Odoo UI for complex config, Claude for quick queries and data entry
3. **Odoo is the boss** -- all data, permissions, and business logic live in Odoo; MCP server is a stateless proxy
4. **No setup barriers** -- self-service, auto-detection, minimal configuration
5. **Open and transparent** -- open-source (MPL-2.0), standard protocols (MCP, OAuth 2.1)

## Key architecture facts

- 1 Zitadel org = 1 Odoo instance (mapped via tenants table in Postgres)
- Each user has their own Odoo API key (encrypted at rest with Fernet)
- ConnectionRegistry caches connections per user (30 min TTL)
- Admin panel routes mounted directly into MCP SDK's Starlette app (not wrapped separately)
- Connection factory: `OdooJSON2Connection` (Odoo 19+) / `OdooConnection` (Odoo 14-18, XML-RPC)

## JSON/2 API key points

- Endpoint: `POST /json/2/{model}/{method}`
- Auth: `Authorization: Bearer <api_key>` header
- Database: `X-Odoo-Database: <db>` header
- Body: flat JSON with named args, `ids` and `context` are top-level keys
- Create/write use `vals` (not `values`)
- Responses are raw JSON (no RPC envelope)
- Errors return proper HTTP status codes (401, 403, 404, 422, 500)

## Development

```bash
uv venv --python 3.10
source .venv/bin/activate
uv pip install -e ".[dev]"
pytest tests/ -x -q         # unit tests (mocked), stop on first failure
```

## Conventions

- Follow existing code style (ruff configured in pyproject.toml)
- Keep JSON/2 and XML-RPC clients in separate files -- do not merge them
- Both connection classes must satisfy `OdooConnectionProtocol`
- Shared exceptions live in `exceptions.py`
- No new dependencies without discussion (httpx already available)
- Admin panel: Jinja2 templates + Tailwind CSS (via CDN)
- Terminology: Tenant = Odoo instance linked to Zitadel org; UserConnection = user's API key for a tenant

## Key files

| File | Role |
|------|------|
| `server.py` | Factory pattern, OAuth wiring, FastMCP setup |
| `registry.py` | ConnectionRegistry -- maps users to Odoo connections |
| `admin/routes.py` | Self-service setup + admin routes |
| `admin/db.py` | Postgres DatabaseManager |
| `admin/auth.py` | OAuth login flow, session cookies, CSRF |
| `odoo_json2_connection.py` | JSON/2 client (httpx, Odoo 19+) |
| `odoo_connection.py` | XML-RPC client (stdlib, Odoo 14-18) |
| `connection_protocol.py` | Protocol class for connection interface |
| `oauth.py` | ZitadelTokenVerifier -- token introspection |
| `config.py` | OdooConfig dataclass |
| `tools.py` | 6 MCP tools with smart field selection |
| `resources.py` | 4 MCP resources (URI-based) |
| `access_control.py` | Odoo ACL checks via check_access_rights |
