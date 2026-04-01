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

        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "user": user,
                "connection": connection,
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
