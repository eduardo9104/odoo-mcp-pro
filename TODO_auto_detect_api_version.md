# Feature Request: Auto-detect Odoo API version

## Problem

Currently the API version (`json2` or `xmlrpc`) must be manually configured:
- Self-hosted: via `ODOO_API_VERSION` env var
- Multi-tenant: per tenant in Postgres (`api_version` column)

This is an unnecessary setup step and a source of misconfiguration.

## Proposed solution

Auto-detect the Odoo version on first connection and select the right API client automatically:
- Odoo 19+: use `json2` (JSON/2 API)
- Odoo 14-18: use `xmlrpc` (XML-RPC)

## How it could work

1. Both `OdooConnection` and `OdooJSON2Connection` already call `get_server_version()` on connect
2. The version response includes `server_version` (e.g. `"19.0"`) and `server_version_info` (e.g. `[19, 0, 0, "final", 0]`)
3. Add a factory step that:
   - Connects with a lightweight version check (XML-RPC `/xmlrpc/2/common` `version()` works on all Odoo versions, or try the JSON/2 endpoint first)
   - Parses the major version number
   - Instantiates `OdooJSON2Connection` for >= 19, `OdooConnection` for < 19
4. The `api_version` config becomes optional -- if omitted, auto-detect; if set, use it as an override

## Files to change

- `config.py`: make `api_version` optional (default `"auto"` instead of `"xmlrpc"`)
- `server.py`: factory logic to detect and instantiate the right client
- `registry.py`: same for multi-tenant connection creation
- `admin/db.py`: tenant `api_version` column could default to `"auto"`
- Tests: add test cases for auto-detection logic

## Considerations

- The version check adds one extra HTTP call on first connection (can be cached)
- Should gracefully fall back to xmlrpc if detection fails
- Odoo.sh hostnames already determine the database -- version detection should work the same way
