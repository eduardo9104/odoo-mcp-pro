# Setup Guide -- odoo-mcp-pro

Step-by-step guide to deploy and configure odoo-mcp-pro.

## Choose your path

| You want | Setup | Time |
|----------|-------|------|
| Personal use (Claude Code / Desktop) | [Local setup](#local-setup) | 5 min |
| Multi-tenant SaaS (Claude.ai, team access) | [Multi-tenant deployment](#multi-tenant-deployment) | 1-2 hrs |

---

## Local setup

Run locally without Postgres or Zitadel. For personal use with Claude Code or Claude Desktop.

### Prerequisites

- Python 3.10+
- Odoo 19+ instance with an [API key](#generating-an-odoo-api-key)

### Claude Code

```bash
claude mcp add -s user \
  -e ODOO_URL=https://your-odoo.com \
  -e ODOO_DB=your_database \
  -e ODOO_API_KEY=your_api_key \
  -e ODOO_API_VERSION=json2 \
  -- odoo python -m mcp_server_odoo
```

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
        "ODOO_DB": "your_database",
        "ODOO_API_KEY": "your_api_key",
        "ODOO_API_VERSION": "json2"
      }
    }
  }
}
```

### From source

```bash
git clone https://github.com/pantalytics/odoo-mcp-pro.git
cd odoo-mcp-pro
uv venv --python 3.10 && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env   # edit with your Odoo credentials
python -m mcp_server_odoo
```

---

## Multi-tenant deployment

Deploy as a managed SaaS: one MCP server serving multiple customers via Claude.ai.

### Architecture

```
Claude.ai -> OAuth 2.1 -> Caddy (TLS) -> MCP Server -> Odoo (customer)
                                             |
                                         Postgres
                                             |
                                         Zitadel Cloud
```

### Prerequisites

- A VPS (Hetzner CX22 or similar, ~4.50 EUR/month)
- A domain with DNS access (two subdomains: `mcp.example.com`, `admin.example.com`)
- A [Zitadel Cloud](https://zitadel.cloud) account
- Docker and Docker Compose on the VPS

### 1. Zitadel Cloud setup

1. Create a Zitadel Cloud instance at https://zitadel.cloud
2. Create a project (e.g., "MCP Server")
3. Create two applications in the project:

**App 1: OIDC Web Application** (for Claude.ai and admin panel login)
- Type: Web (OIDC)
- Auth method: PKCE (no client secret)
- Redirect URIs:
  - `https://claude.ai/api/mcp/auth_callback`
  - `https://mcp.example.com/admin/callback`
- Note the **Client ID** -- this is the `MCP_OIDC_CLIENT_ID` that users enter in Claude.ai's Advanced settings

**App 2: API Application** (for token introspection)
- Type: API
- Auth method: Basic (client_id + client_secret)
- Note the **Client ID** and **Client Secret**

4. Create an organization for each customer (e.g., "Acme Corp")
5. Note the organization ID (visible in Zitadel console URL)

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

Required env vars:

```bash
# Postgres
POSTGRES_PASSWORD=<strong random password>

# Admin panel
ADMIN_SESSION_SECRET=<random 32+ char string>
ADMIN_OAUTH_CLIENT_ID=<Zitadel OIDC app client ID>
ADMIN_BASE_URL=https://mcp.example.com
ADMIN_BOOTSTRAP_SUB=<your Zitadel subject ID>
ADMIN_BOOTSTRAP_EMAIL=<your email>

# OAuth (MCP endpoint)
OAUTH_ISSUER_URL=https://your-instance.zitadel.cloud
OAUTH_RESOURCE_SERVER_URL=https://mcp.example.com/mcp
MCP_OIDC_CLIENT_ID=<OIDC app client ID from App 1>
ZITADEL_INTROSPECTION_URL=https://your-instance.zitadel.cloud/oauth/v2/introspect
ZITADEL_CLIENT_ID=<API app client ID>
ZITADEL_CLIENT_SECRET=<API app client secret>
```

### 4. Configure Caddy

Create `deploy/Caddyfile.multi-tenant`:

```
mcp.example.com {
    handle /admin/* {
        reverse_proxy mcp-server:8000
    }
    handle {
        reverse_proxy mcp-server:8000
    }
}
```

### 5. Deploy

```bash
cd /opt/odoo-mcp-pro/deploy
docker compose -f docker-compose.multi-tenant.yml up -d --build
```

Verify:
- `curl -s https://mcp.example.com/mcp/` returns 401 (OAuth protecting -- correct)
- `https://mcp.example.com/admin/login` shows the login page

### 6. DNS

Add A records pointing to your VPS IP:

| Type | Name | Value |
|------|------|-------|
| A | `mcp` | `<VPS IP>` |

---

## Onboarding a new customer

1. **Create Zitadel organization**: In Zitadel Cloud console, create an org for the customer. Note the org ID.

2. **Create tenant**: Log in to `/admin/`, click "Add Tenant":
   - Name: customer name
   - Odoo URL: customer's Odoo instance URL
   - Zitadel Org ID: the org ID from step 1

3. **Create Zitadel users**: Add users to the customer's org in Zitadel (or enable Microsoft Entra ID federation for SSO).

4. **Share setup link**: Send users to `https://mcp.example.com/admin/setup`. They:
   - Log in with their company account
   - Enter their Odoo API key
   - Get instructions for connecting Claude.ai

5. **Connect Claude.ai**: Users add the MCP server in Claude.ai:
   - Settings > Integrations > Add MCP Server
   - URL: `https://mcp.example.com/mcp/`
   - Click **Advanced** and enter the **Client ID** (the `MCP_OIDC_CLIENT_ID` from the OIDC app in Zitadel). Claude.ai does not support dynamic client registration, so this must be entered manually.

> **Note**: Claude.ai can only have one active Odoo connector per browser session, because Zitadel reuses the existing session. Super admins who need to access multiple orgs should use separate Zitadel accounts per org.

---

## Generating an Odoo API key

1. Log in to your Odoo instance
2. Click your avatar (top right) > My Profile
3. Scroll to Account Security
4. Click New API Key
5. Description: `MCP Server`
6. Click Generate Key -- copy immediately (shown only once)

**Tip**: Create a dedicated Odoo user (e.g., `mcp@yourcompany.com`) with appropriate permissions rather than using your admin account.

### Finding your database name

- **Odoo.sh**: Check branch name in dashboard (e.g., `mycompany-main-4829371`)
- **Self-hosted**: Go to `https://your-odoo.com/web/database/manager`
- **Odoo.sh hosted**: Leave `ODOO_DB` empty -- hostname selects the database

---

## Day-to-day operations

### View logs

```bash
cd /opt/odoo-mcp-pro/deploy
docker compose -f docker-compose.multi-tenant.yml logs -f --tail=50
```

### Restart

```bash
docker compose -f docker-compose.multi-tenant.yml restart
```

### Update to new version

```bash
cd /opt/odoo-mcp-pro
git pull origin main
cd deploy
docker compose -f docker-compose.multi-tenant.yml up -d --build
```

---

## Troubleshooting

### MCP server returns 401

This is correct -- OAuth is protecting the endpoint. Users need to authenticate via Claude.ai.

### "No Odoo connection configured"

The user has logged in but hasn't set up their API key yet. Direct them to `/admin/setup`.

### Token introspection fails

```bash
# Test from the server
source .env
curl -s $ZITADEL_INTROSPECTION_URL \
  --user "$ZITADEL_CLIENT_ID:$ZITADEL_CLIENT_SECRET" \
  -d "token=fake"
# Should return {"active":false}
```

### Caddy TLS errors

- Verify DNS A records point to VPS IP: `dig +short mcp.example.com`
- Ensure ports 80 and 443 are open
- Cloudflare users: set proxy to DNS-only (grey cloud)

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
