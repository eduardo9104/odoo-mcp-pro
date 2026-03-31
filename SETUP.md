# Setup Guide -- odoo-mcp-pro

## Choose your path

| You want | Setup | Time |
|----------|-------|------|
| Use it now, no install | [Hosted (recommended)](#hosted-recommended) | 2 min |
| Run locally with Claude Code / Desktop | [Local setup](#local-setup) | 5 min |
| Deploy your own multi-tenant server | [Self-hosted deployment](#self-hosted-deployment) | 1-2 hrs |

---

## Hosted (recommended)

We run the server for you. Works on your phone, laptop, and any browser.

1. **Sign up** at [pantalytics.com/en/apps/odoo-mcp-server](https://pantalytics.com/en/apps/odoo-mcp-server)
2. Log in and enter your **Odoo URL** + **API key** ([how to generate one](#generating-an-odoo-api-key))
3. Connect your AI tool to `https://mcp.pantalytics.com/mcp/`
4. Start asking questions

Works with Claude (mobile, desktop, web, Code), ChatGPT, and any MCP-compatible tool. Supports Odoo 14-19+. Free during beta.

---

## Local setup

Run locally without Postgres or Zitadel. For personal use with Claude Code or Claude Desktop. Supports Odoo 14-19+ (the server auto-detects JSON/2 for Odoo 19+ or XML-RPC for older versions).

### Prerequisites

- Python 3.10+
- Odoo 14+ instance with an [API key](#generating-an-odoo-api-key)

### Install

```bash
git clone https://github.com/pantalytics/odoo-mcp-pro.git
cd odoo-mcp-pro
uv venv --python 3.10 && source .venv/bin/activate
uv pip install -e .
```

### Claude Code

```bash
claude mcp add -s user \
  -e ODOO_URL=https://your-odoo.com \
  -e ODOO_API_KEY=your_api_key \
  -- odoo python -m mcp_server_odoo
```

Note: `ODOO_DB` is only needed for self-hosted Odoo with multiple databases. Odoo.sh and Odoo Online don't need it.

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "python",
      "args": ["-m", "mcp_server_odoo"],
      "cwd": "/path/to/odoo-mcp-pro",
      "env": {
        "ODOO_URL": "https://your-odoo.com",
        "ODOO_API_KEY": "your_api_key"
      }
    }
  }
}
```

### Verify it works

Ask Claude: *"List the models available in Odoo"* or *"Search for contacts in res.partner"*.

---

## Self-hosted deployment

Deploy your own multi-tenant MCP server with Docker Compose, Postgres, Zitadel, and Caddy. Users sign up and manage their own Odoo connections via a self-service admin panel.

### Architecture

```
AI tool -> OAuth 2.1 -> Caddy (TLS) -> MCP Server -> Odoo (per user)
                                           |
                                       Postgres (user connections, usage tracking)
                                           |
                                       Zitadel Cloud (identity)
```

### Prerequisites

- A VPS (Hetzner CX22 or similar, ~4.50 EUR/month)
- A domain with DNS access (one subdomain, e.g., `mcp.example.com`)
- A [Zitadel Cloud](https://zitadel.cloud) account
- Docker and Docker Compose on the VPS

### 1. Zitadel Cloud setup

Create a Zitadel Cloud instance and set up two applications:

**App 1: OIDC Web Application** (for user login + Claude.ai auth)
- Type: Web (OIDC)
- Auth method: PKCE (no client secret)
- Redirect URIs:
  - `https://claude.ai/api/mcp/auth_callback`
  - `https://mcp.example.com/admin/callback`
- Note the **Client ID** -- you'll need it for both `MCP_OIDC_CLIENT_ID` and `ADMIN_OAUTH_CLIENT_ID`

**App 2: API Application** (for server-side token introspection)
- Type: API
- Auth method: Basic (client_id + client_secret)
- Note the **Client ID** (`ZITADEL_CLIENT_ID`) and **Client Secret** (`ZITADEL_CLIENT_SECRET`)

Enable self-registration in Login Settings so users can create their own accounts.

### 2. Server setup

```bash
# On the VPS
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh
apt install -y docker-compose-plugin git

# Clone the repo
cd /opt
git clone https://github.com/pantalytics/odoo-mcp-pro.git
cd odoo-mcp-pro/deploy
```

### 3. Configure environment

```bash
cp .env.example .env
nano .env
```

Fill in all values. See [Environment variables](#environment-variables) for the full reference.

Generate an encryption key for API keys at rest:

```bash
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

### 4. Configure Caddy

Edit `deploy/Caddyfile.multi-tenant` -- the file is pre-configured, but you need to update the `ZITADEL_HOST` env var to match your Zitadel instance hostname (e.g., `your-instance.zitadel.cloud`). Set this in your `.env` file:

```bash
ZITADEL_HOST=your-instance.zitadel.cloud
```

The Caddyfile handles:
- TLS termination (automatic via Let's Encrypt)
- Admin panel at `/admin/*`
- MCP endpoint at `/mcp`
- OAuth proxy routes (`/authorize`, `/token`, `/oauth/v2/*`) -- these are needed because Claude.ai sends auth requests relative to the server root
- DCR endpoint at `/register` -- allows Claude.ai to auto-configure without manual client ID entry

### 5. DNS

Add one A record pointing to your VPS:

| Type | Name | Value |
|------|------|-------|
| A | `mcp` | `<VPS IP>` |

### 6. Deploy

```bash
cd /opt/odoo-mcp-pro/deploy
docker compose -f docker-compose.multi-tenant.yml up -d --build
```

Verify:
- `curl -s https://mcp.example.com/mcp` returns 401 (OAuth protecting the endpoint -- correct)
- `https://mcp.example.com/admin/setup` redirects to Zitadel login

### 7. Bootstrap admin

The first admin is created automatically from env vars:
- `ADMIN_BOOTSTRAP_SUB` -- your Zitadel subject ID (find it in Zitadel Console > Users)
- `ADMIN_BOOTSTRAP_EMAIL` -- your email

After first login at `/admin/setup`, you'll see the admin panel.

---

## User onboarding (self-service)

Users manage their own connections. No admin action needed.

1. User visits `https://mcp.example.com/admin/setup`
2. Redirected to Zitadel to log in or create an account
3. Enters their **Odoo URL** and **API key**
4. Connects their AI tool to `https://mcp.example.com/mcp/`

That's it. Each user's API key is encrypted at rest. Their Odoo permissions apply -- they can only see and do what their Odoo role allows.

---

## Environment variables

### Odoo connection (local/single-tenant mode only)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ODOO_URL` | Yes* | -- | Odoo server URL (e.g., `https://mycompany.odoo.com`) |
| `ODOO_API_KEY` | Yes* | -- | Odoo API key (preferred over password) |
| `ODOO_USER` | -- | -- | Odoo username (fallback if no API key) |
| `ODOO_PASSWORD` | -- | -- | Odoo password (required with username) |
| `ODOO_DB` | No | auto | Database name (only needed for self-hosted multi-db) |

*Not needed in multi-tenant mode (`DATABASE_URL` set).

### MCP server

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ODOO_MCP_TRANSPORT` | No | `stdio` | `stdio` or `streamable-http` |
| `ODOO_MCP_HOST` | No | `localhost` | Bind address for HTTP mode |
| `ODOO_MCP_PORT` | No | `8000` | Port for HTTP mode |
| `ODOO_MCP_LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `ODOO_MCP_DEFAULT_LIMIT` | No | `10` | Default search result limit |
| `ODOO_MCP_MAX_LIMIT` | No | `100` | Maximum search result limit |
| `ODOO_MCP_MAX_SMART_FIELDS` | No | `15` | Max fields in smart selection |

### Multi-tenant mode

Setting `DATABASE_URL` enables multi-tenant mode: Postgres-backed user connections, admin panel, usage tracking. Odoo connection env vars are ignored.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | -- | Postgres connection string |
| `API_KEY_ENCRYPTION_KEY` | Yes | -- | Fernet key for encrypting API keys at rest |

### Admin panel

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ADMIN_SESSION_SECRET` | Yes | -- | Secret for signing session cookies (32+ chars) |
| `ADMIN_OAUTH_CLIENT_ID` | Yes | -- | Zitadel OIDC app client ID for admin login |
| `ADMIN_BASE_URL` | Yes | -- | Public URL (e.g., `https://mcp.example.com`) |
| `ADMIN_BOOTSTRAP_SUB` | Yes | -- | Zitadel subject ID for first admin |
| `ADMIN_BOOTSTRAP_EMAIL` | No | -- | Email for first admin |
| `ADMIN_COOKIE_SECURE` | No | `true` | Set to `false` for HTTP development |

### OAuth (MCP endpoint authentication)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OAUTH_ISSUER_URL` | Yes | -- | Zitadel issuer URL |
| `OAUTH_RESOURCE_SERVER_URL` | Yes | -- | Public MCP endpoint URL |
| `ZITADEL_INTROSPECTION_URL` | Yes | -- | Zitadel token introspection endpoint |
| `ZITADEL_CLIENT_ID` | Yes | -- | API app client ID (for introspection) |
| `ZITADEL_CLIENT_SECRET` | Yes | -- | API app client secret |
| `MCP_OIDC_CLIENT_ID` | No | -- | Returned by DCR `/register` endpoint |

### Caddy

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DOMAIN` | Yes | `mcp.example.com` | Your domain |
| `ZITADEL_HOST` | Yes | -- | Zitadel instance hostname for OAuth proxy |

---

## Generating an Odoo API key

1. Log in to your Odoo instance
2. Click your avatar (top right) > My Profile
3. Scroll to Account Security
4. Click New API Key
5. Description: `MCP Server`
6. Click Generate Key -- copy immediately (shown only once)

**Tip**: Create a dedicated Odoo user (e.g., `mcp@yourcompany.com`) with appropriate permissions rather than using your admin account.

### Do I need ODOO_DB?

- **Odoo.sh / Odoo Online**: No -- the hostname determines the database
- **Self-hosted with one database**: No -- auto-detected
- **Self-hosted with multiple databases**: Yes -- set `ODOO_DB` to the database name

---

## Day-to-day operations

### View logs

```bash
cd /opt/odoo-mcp-pro/deploy
docker compose -f docker-compose.multi-tenant.yml logs -f --tail=50
```

### Restart

```bash
docker compose -f docker-compose.multi-tenant.yml restart mcp-server
```

### Update to new version

```bash
cd /opt/odoo-mcp-pro
git pull origin main
cd deploy
docker compose -f docker-compose.multi-tenant.yml build --no-cache mcp-server
docker compose -f docker-compose.multi-tenant.yml up -d --force-recreate mcp-server
```

### Check usage

```bash
docker compose -f docker-compose.multi-tenant.yml exec postgres \
  psql -U mcp -d mcp_admin -c "SELECT uc.email, ud.day, ud.call_count FROM usage_daily ud JOIN user_connections uc ON ud.zitadel_sub = uc.zitadel_sub ORDER BY ud.day DESC, ud.call_count DESC;"
```

---

## Troubleshooting

### MCP endpoint returns 401

This is correct -- OAuth is protecting the endpoint. Users authenticate via their AI tool (Claude.ai, etc.).

### "No Odoo connection configured"

The user logged in but hasn't set up their Odoo connection yet. Direct them to `/admin/setup`.

### Token introspection fails

```bash
source .env
curl -s $ZITADEL_INTROSPECTION_URL \
  --user "$ZITADEL_CLIENT_ID:$ZITADEL_CLIENT_SECRET" \
  -d "token=fake"
# Should return {"active":false}
```

### API keys stored in plaintext

Check if `API_KEY_ENCRYPTION_KEY` is set. Generate one:

```bash
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Add to `.env` and restart. Existing plaintext keys will be decrypted transparently; re-saving a connection encrypts the key.

### Caddy TLS errors

- Verify DNS A record: `dig +short mcp.example.com`
- Ensure ports 80 and 443 are open
- Cloudflare users: set proxy to DNS-only (grey cloud)

### Version detection fails for Odoo.sh

Odoo.sh SaaS versions use strings like `saas~19`. This is handled automatically since v1.0.1.

---

## Microsoft Entra ID federation (optional)

Allow users to log in with Microsoft work accounts. Configure in Zitadel:

1. **Azure**: Create App Registration with redirect URI `https://<zitadel-instance>/idps/callback`
2. **Zitadel**: Settings > Identity Providers > New > Microsoft / Azure AD template
3. Fill in Client ID, Client Secret, and Tenant ID from Azure
4. Enable in Login Settings

The MCP server needs no code changes -- it validates all tokens via Zitadel introspection.

---

## Need help?

Open an issue on [GitHub](https://github.com/pantalytics/odoo-mcp-pro/issues).

Built by [Pantalytics](https://pantalytics.com) -- Odoo implementation partner in Utrecht, Netherlands.
