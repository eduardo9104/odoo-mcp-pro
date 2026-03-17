How to connect Claude (or any AI) to Odoo 19 via the JSON/2 API -- lessons learned

Tags: odoo19, api, json2, integration

Hi everyone,

Over the past few months I've been working on connecting Claude AI to Odoo 19 via the new JSON/2 API. Wanted to share what I learned -- both about the JSON/2 API itself and about making AI assistants work well with Odoo. Hopefully useful for anyone exploring this direction.


## Why the JSON/2 API matters

If you're still using XML-RPC or JSON-RPC to integrate with Odoo, it's worth knowing that Odoo 19 introduced a new JSON/2 API and that XML-RPC will be removed in Odoo 20. The new API is a big improvement:

```
POST /json/2/res.partner/search_read
Authorization: Bearer <api_key>
Content-Type: application/json

{
  "domain": [["is_company", "=", true]],
  "fields": ["name", "email", "phone"],
  "limit": 10
}
```

Key differences from XML-RPC:

- Clean REST-style endpoints: POST /json/2/{model}/{method}
- Bearer token auth: API key in the Authorization header instead of uid + password in every call
- Flat JSON body: named arguments at the top level, no nested args/kwargs wrapping
- Proper HTTP status codes: 401, 403, 404, 422 instead of everything-is-200-with-an-error-body
- No RPC envelope: response is raw JSON
- No custom module needed: Odoo handles ACLs server-side

A few gotchas I ran into:

- Create and write use "vals" as the parameter name, not "values"
- IDs are a top-level "ids" key, not part of the positional args
- Database selection goes in the X-Odoo-Database header
- On Odoo.sh, the database is determined by the hostname -- you can omit the database header


## Connecting AI assistants via MCP

MCP (Model Context Protocol, https://modelcontextprotocol.io) is an open standard that lets AI assistants call external tools. If you define a set of tools (search, create, update, etc.) and expose them via MCP, Claude can decide which ones to call based on a natural language question.

For example, when you ask "Find all unpaid invoices over 5,000 EUR from Q4", the AI translates that into:

```
search_records(
  model="account.move",
  domain=[["payment_state", "=", "not_paid"], ["amount_total", ">", 5000], ...],
  fields=["name", "partner_id", "amount_total", "invoice_date_due"]
)
```

No copy-pasting, no CSV exports -- you just ask.


## What works well (and what doesn't)

Things that work surprisingly well:

- Exploratory queries: "What fields does sale.order have?" -- the AI calls fields_get and summarizes the results
- Cross-referencing: "Which sales orders from last month don't have a delivery yet?" -- multiple search calls, then comparison
- Data entry: "Create a lead for Acme Corp, expected revenue 50k EUR" -- create_record with the right model and values

Things to watch out for:

- Field overload: Odoo models can have hundreds of fields. If you return all of them, the AI gets confused. I ended up building a smart field selector that ranks fields by business relevance (name, email, phone > internal IDs). That made a huge difference in response quality.
- Domain filter syntax: The AI sometimes gets the Odoo domain syntax wrong (e.g., using "and" instead of "&"). Clear tool descriptions with examples help a lot.
- Write operations: You probably want access control. In our setup, Odoo's own ACLs handle this -- the API key's user permissions determine what's allowed. But think about this before giving an AI write access to production.


## The open-source implementation

We've open-sourced our implementation: odoo-mcp-pro (https://github.com/pantalytics/odoo-mcp-pro), licensed MPL-2.0 (same as Odoo Community). Originally forked from mcp-server-odoo by Andrey Ivanov (https://github.com/ivnvxd/mcp-server-odoo) — thanks for the foundation.

What it includes:

- JSON/2 client for Odoo 19+ (also XML-RPC for Odoo 14-18)
- 6 tools: search, get, create, update, delete, list models
- 4 resources: URI-based access to records, search results, field definitions, counts
- Smart field selection (the field ranking mentioned above)
- OAuth 2.1 support for multi-user cloud deployments (via Zitadel)
- 480+ unit tests

Quick start with Claude Code:

```
git clone https://github.com/pantalytics/odoo-mcp-pro.git
cd odoo-mcp-pro
uv venv && source .venv/bin/activate
uv pip install -e .

claude mcp add -s user \
  -e "ODOO_URL=https://your-odoo.com" \
  -e "ODOO_DB=your_database" \
  -e "ODOO_API_KEY=your_api_key" \
  -e "ODOO_API_VERSION=json2" \
  -e "ODOO_YOLO=true" \
  -- odoo python -m mcp_server_odoo
```

Also works with Claude Desktop -- see the README for config.

For Claude.ai (web) there's a cloud deployment option with Docker + Caddy + OAuth 2.1 so multiple users can connect securely without sharing API keys.


## Tips if you're building your own Odoo-AI integration

Even if you don't use this project, here are some things I wish I knew earlier:

1. Start read-only. Get search and read working first. Write operations are a separate challenge (validation, required fields, relational fields).
2. Limit the fields returned. Don't send all 200 fields of sale.order to the AI. Pick the top 15-20 that matter.
3. Include example domains in your tool descriptions. AI models are much better at generating Odoo domains when they see examples like [["state", "=", "sale"], ["partner_id.country_id.code", "=", "NL"]].
4. Use the API key of a user with realistic permissions. Don't use the admin key in production -- Odoo's ACLs are your safety net.
5. Test with fields_get first. It's the best way to understand what a model expects before writing to it.


## Questions for the community

- Has anyone else been experimenting with AI + Odoo integrations? Curious what approaches others are taking.
- Any thoughts on which Odoo workflows benefit most from natural language access?
- If you try odoo-mcp-pro, let me know what works and what doesn't -- issues and PRs are welcome on GitHub.

Cheers,
Rutger
