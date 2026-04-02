"""Self-service setup routes for user Odoo connections.

All routes require login authentication via the session cookie.
"""

import logging
import os

from fastapi import Request
from fastapi.responses import RedirectResponse

from .auth import (
    generate_csrf_token,
    require_login,
    validate_csrf_token,
)

logger = logging.getLogger(__name__)


def register_admin_routes(app, db_manager):
    """Register self-service setup routes.

    Args:
        app: FastAPI app instance
        db_manager: DatabaseManager for user connection operations
    """

    # --- Self-service setup ---

    @app.get("/")
    @require_login
    async def index(request: Request):
        """Redirect root to setup page."""
        return RedirectResponse(url="/admin/setup", status_code=302)

    @app.get("/setup")
    @require_login
    async def setup_page(request: Request):
        """Self-service setup page: user manages their own Odoo connection."""
        user = request.state.user
        connection = await db_manager.get_user_connection_by_sub(user["sub"])

        mcp_server_url = _get_mcp_server_url()
        templates = request.app.state.templates

        # Pass last 3 chars of API key for display (masked)
        api_key_suffix = ""
        if connection and connection.odoo_api_key:
            api_key_suffix = connection.odoo_api_key[-3:]

        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "user": user,
                "connection": connection,
                "api_key_suffix": api_key_suffix,
                "mcp_server_url": mcp_server_url,
                "csrf_token": generate_csrf_token(user),
            },
        )

    def _get_mcp_server_url() -> str:
        """Get the public MCP server URL for connection instructions."""
        url = os.getenv("OAUTH_RESOURCE_SERVER_URL", "").strip().rstrip("/")
        if not url:
            url = os.getenv("ADMIN_BASE_URL", "http://localhost:8000").strip().rstrip("/")
        return url

    @app.post("/setup")
    @require_login
    async def setup_save(request: Request):
        """Save the user's Odoo connection (URL + API key)."""
        user = request.state.user
        form = await request.form()

        csrf_token = form.get("csrf_token", "")
        if not validate_csrf_token(csrf_token):
            return RedirectResponse(url="/admin/setup", status_code=302)

        odoo_url = form.get("odoo_url", "").strip()
        odoo_api_key = form.get("odoo_api_key", "").strip()
        odoo_db = form.get("odoo_db", "").strip()

        # For existing connections: fill in missing fields from current values
        existing = await db_manager.get_user_connection_by_sub(user["sub"])
        if existing:
            odoo_url = odoo_url or existing.odoo_url
            odoo_api_key = odoo_api_key or existing.odoo_api_key
            # odoo_db can be intentionally empty (cleared), so only fall back
            # if the form field was not present at all
            if "odoo_db" not in form:
                odoo_db = existing.odoo_db or ""
        else:
            # New connection: URL and API key are required
            if not odoo_url or not odoo_api_key:
                return RedirectResponse(url="/admin/setup", status_code=302)

        try:
            await db_manager.upsert_user_connection(
                zitadel_sub=user["sub"],
                odoo_url=odoo_url,
                odoo_api_key=odoo_api_key,
                email=user.get("email"),
                odoo_db=odoo_db or None,
            )
            logger.info(f"User {user['email']} saved connection to {odoo_url}")
            return RedirectResponse(url="/admin/setup", status_code=302)
        except Exception as e:
            logger.error(f"Failed to save connection for {user['email']}: {e}")
            return RedirectResponse(url="/admin/setup", status_code=302)

    @app.post("/setup/delete")
    @require_login
    async def setup_delete(request: Request):
        """Delete the user's own connection."""
        user = request.state.user
        form = await request.form()

        csrf_token = form.get("csrf_token", "")
        if not validate_csrf_token(csrf_token):
            return RedirectResponse(url="/admin/setup", status_code=302)

        await db_manager.delete_user_connection_by_sub(user["sub"])
        logger.info(f"User {user['email']} removed their connection")
        return RedirectResponse(url="/admin/setup", status_code=302)

    @app.post("/setup/verify")
    @require_login
    async def setup_verify(request: Request):
        """Verify the user's Odoo connection and store debug info."""
        from ..version_detect import detect_api_version
        from ..config import OdooConfig
        from ..odoo_connection import OdooConnection
        from ..odoo_json2_connection import OdooJSON2Connection
        from ..performance import PerformanceManager

        user = request.state.user
        form = await request.form()

        csrf_token = form.get("csrf_token", "")
        if not validate_csrf_token(csrf_token):
            return RedirectResponse(url="/admin/setup", status_code=302)

        connection = await db_manager.get_user_connection_by_sub(user["sub"])
        if not connection:
            return RedirectResponse(url="/admin/setup", status_code=302)

        odoo_version = None
        odoo_hosting = None
        error_msg = None

        # Step 1: Check URL — can we reach the server?
        try:
            api_version, server_version = detect_api_version(connection.odoo_url)
            odoo_version = server_version
        except Exception as e:
            error_msg = f"URL check failed: Cannot reach {connection.odoo_url}. Is the URL correct and the server online?"
            logger.warning(f"Verify URL failed for {user['email']}: {e}")
            await db_manager.update_verification(
                zitadel_sub=user["sub"],
                odoo_version=None,
                odoo_hosting=None,
                last_error=error_msg,
            )
            return RedirectResponse(url="/admin/setup", status_code=302)

        # Step 2: Determine hosting type
        url_lower = connection.odoo_url.lower()
        if ".odoo.com" in url_lower:
            odoo_hosting = "odoo.sh"
        else:
            odoo_hosting = "self-hosted"

        # Step 3: Try to connect and authenticate
        try:
            config = OdooConfig(
                url=connection.odoo_url,
                database=connection.odoo_db or "",
                api_key=connection.odoo_api_key,
                username=connection.email if api_version == "xmlrpc" else None,
                api_version=api_version,
            )

            if api_version == "json2":
                conn = OdooJSON2Connection(config)
            else:
                conn = OdooConnection(config, performance_manager=PerformanceManager(config))

            conn.connect()
            conn.authenticate()

            if conn.is_authenticated:
                logger.info(f"Verify OK for {user['email']}: {odoo_version} ({odoo_hosting}), UID={conn.uid}")
            else:
                if api_version == "xmlrpc":
                    error_msg = (
                        f"Authentication failed. Checked: URL OK ({odoo_version}), "
                        f"username '{connection.email}', "
                        f"database '{connection.odoo_db or 'auto-detect'}'. "
                        f"Please verify: (1) your API key is valid, "
                        f"(2) your Odoo login matches '{connection.email}', "
                        f"(3) the database name is correct."
                    )
                else:
                    error_msg = (
                        f"Authentication failed. URL OK ({odoo_version}). "
                        f"Please check that your API key is valid and not expired."
                    )

            conn.disconnect()

        except Exception as e:
            err = str(e)
            if api_version == "xmlrpc" and "database" in err.lower():
                error_msg = (
                    f"Database error. URL OK ({odoo_version}), but the database "
                    f"'{connection.odoo_db or 'auto-detect'}' could not be found. "
                    f"Please set the correct database name in Advanced settings."
                )
            elif "Authentication failed" in err:
                if api_version == "xmlrpc":
                    error_msg = (
                        f"Authentication failed. URL OK ({odoo_version}). "
                        f"Tried username '{connection.email}' with your API key "
                        f"on database '{connection.odoo_db or 'auto-detect'}'. "
                        f"Check all three values."
                    )
                else:
                    error_msg = (
                        f"Authentication failed. URL OK ({odoo_version}). "
                        f"Your API key appears to be invalid or expired."
                    )
            else:
                error_msg = f"Connection failed: {err[:300]}"
            logger.warning(f"Verify failed for {user['email']}: {error_msg}")

        # Store result
        await db_manager.update_verification(
            zitadel_sub=user["sub"],
            odoo_version=odoo_version,
            odoo_hosting=odoo_hosting,
            last_error=error_msg,
        )

        return RedirectResponse(url="/admin/setup", status_code=302)
