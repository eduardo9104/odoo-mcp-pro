<p align="center">
  <a href="https://www.odoo.com"><img src="assets/odoo-logo.svg" alt="Odoo" height="60"/></a>
  &nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://modelcontextprotocol.io"><img src="assets/mcp-logo.svg" alt="Model Context Protocol" height="60"/></a>
</p>

<h1 align="center">odoo-mcp-pro</h1>

<p align="center">
  Managed MCP server connecting Claude AI to Odoo ERP.<br/>
  Search, create, update, and manage records using natural language.
</p>

<p align="center">
  <a href="https://github.com/pantalytics/odoo-mcp-pro/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MPL%202.0-blue.svg" alt="License: MPL 2.0"/></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+"/></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-compatible-green.svg" alt="MCP Compatible"/></a>
  <a href="https://www.odoo.com/documentation/19.0/developer/reference/external_api.html"><img src="https://img.shields.io/badge/Odoo-19%2B%20JSON%2F2-714b67.svg" alt="Odoo 19+ JSON/2"/></a>
  <a href="https://oauth.net/2.1/"><img src="https://img.shields.io/badge/OAuth-2.1-orange.svg" alt="OAuth 2.1"/></a>
</p>

---

## What it is

odoo-mcp-pro is a B2B SaaS MCP server that connects Claude AI to your Odoo ERP. It runs as a managed service: one server handles multiple customers, each with their own Odoo instance, users, and permissions.

> **"Show me all unpaid invoices over 5,000 EUR from Q4"** -- Claude queries your Odoo instance directly and returns the results.

<p align="center">
  <img src="docs/demo.gif" alt="Demo of odoo-mcp-pro" width="800"/>
</p>

## How it works

```
                                 +--------------+
  +-----------+     OAuth 2.1    | MCP Server   |    JSON/2     +--------+
  | Claude.ai |---------------->| (Hetzner)    |-------------->| Odoo   |
  | (browser) |                 |              |               | (cust) |
  +-----------+                 +------+-------+               +--------+
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

- **Zitadel Cloud** handles identity, organizations, and login (OAuth 2.1 + PKCE)
- **Postgres** stores tenant config (which org maps to which Odoo) and user API keys
- **The MCP server** routes each authenticated user to the right Odoo instance using their own API key
- **Odoo** enforces permissions server-side -- the MCP server is a stateless proxy

## For users

Your admin gives you a setup link. Then:

1. Open the link and log in with your company account
2. Enter your Odoo API key (see [how to generate one](SETUP.md#generating-an-odoo-api-key))
3. Add the MCP server to Claude.ai: **Settings > Integrations > Add MCP Server**
4. Ask Claude anything about your Odoo data

## For admins (Pantalytics)

To onboard a new customer:

1. Create a Zitadel organization for the customer
2. Create a tenant in the admin panel: name, Odoo URL, Zitadel org ID
3. Share the setup link with the customer's users
4. Users self-service: log in, enter their Odoo API key, connect Claude

See [SETUP.md](SETUP.md) for the full deployment and onboarding guide.

## Self-hosted (local mode)

For personal use, run locally without Postgres or Zitadel:

```bash
claude mcp add -s user \
  -e ODOO_URL=https://your-odoo.com \
  -e ODOO_DB=your_database \
  -e ODOO_API_KEY=your_api_key \
  -e ODOO_API_VERSION=json2 \
  -- odoo python -m mcp_server_odoo
```

<details>
<summary><b>Claude Desktop</b> -- add to claude_desktop_config.json</summary>

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

</details>

## What you can do

| Tool | What it does |
|------|-------------|
| `search_records` | Search any model with domain filters, sorting, pagination |
| `get_record` | Fetch a specific record by ID with smart field selection |
| `list_models` | Discover available Odoo models |
| `create_record` | Create a new record in any model |
| `update_record` | Update fields on an existing record |
| `delete_record` | Delete a record |

Plus 4 MCP resources for URI-based access to records, search results, field definitions, and record counts.

**Example questions:**
- *"Find all contacts in Amsterdam with open quotations"*
- *"Create a lead for Acme Corp, expected revenue 50k EUR"*
- *"Which sales orders from last month don't have a delivery yet?"*
- *"What fields does the sale.order model have?"*

## Security

- **User data stays in Odoo.** The MCP server is a stateless proxy -- nothing is stored or cached beyond the session.
- **API keys stay server-side.** Users authenticate via OAuth tokens. The Odoo API key never leaves the server.
- **Odoo enforces permissions.** Each user's API key determines what they can see and do. ACLs and record rules apply as normal.
- **Per-user isolation.** Each user gets their own OAuth token and their own Odoo API key.

See [architecture.md](architecture.md) for the full security model.

## Development

```bash
uv venv --python 3.10
source .venv/bin/activate
uv pip install -e ".[dev]"
pytest tests/ -q            # 437 tests, all mocked
ruff check . && ruff format .
```

See [CLAUDE.md](CLAUDE.md) for architecture details and coding conventions.

## Contributing

Contributions are welcome. Fork the repo, create a feature branch, run `pytest tests/` and `ruff check .`, then open a PR.

## License

[Mozilla Public License 2.0](LICENSE) -- the same license as Odoo Community.

## Built by Pantalytics

**odoo-mcp-pro** is built and maintained by [Pantalytics](https://pantalytics.com), an Odoo implementation partner based in Utrecht, Netherlands.

Originally forked from [mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo) by Andrey Ivanov (MPL-2.0). Since expanded with JSON/2 client, multi-tenant SaaS architecture, OAuth 2.1, admin panel, and comprehensive test suite.

---

<sub>Odoo is a registered trademark of <a href="https://www.odoo.com">Odoo S.A.</a> The MCP logo is used under the <a href="https://github.com/modelcontextprotocol/modelcontextprotocol">MIT License</a>. This project is not affiliated with or endorsed by Odoo S.A. or Anthropic.</sub>
