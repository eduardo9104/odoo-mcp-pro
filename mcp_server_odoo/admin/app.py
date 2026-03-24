"""FastAPI app factory for the admin panel.

Creates a standalone FastAPI app for managing Odoo databases and users.
This is mounted separately from the MCP server routes.
"""

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .auth import register_auth_routes
from .routes import register_admin_routes

logger = logging.getLogger(__name__)

# Templates directory
TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_admin_app(db_manager, registry=None, zitadel_issuer_url: str = ""):
    """Create the admin panel FastAPI app.

    Args:
        db_manager: DatabaseManager instance for CRUD operations
        registry: Optional ConnectionRegistry (for cache invalidation)
        zitadel_issuer_url: Zitadel issuer URL for OAuth login

    Returns:
        FastAPI app instance
    """
    if not zitadel_issuer_url:
        zitadel_issuer_url = os.getenv("OAUTH_ISSUER_URL", "").strip()

    app = FastAPI(
        title="Odoo MCP Admin",
        docs_url=None,
        redoc_url=None,
    )

    # Set up Jinja2 templates with auto-escaping
    templates_env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )

    # Store templates in app state for access in route handlers
    class TemplateRenderer:
        """Wrapper to match Starlette's Jinja2Templates interface."""

        def __init__(self, env: Environment):
            self.env = env

        def TemplateResponse(self, name: str, context: dict, status_code: int = 200):  # noqa: N802
            from starlette.responses import HTMLResponse

            template = self.env.get_template(name)
            html = template.render(**context)
            return HTMLResponse(content=html, status_code=status_code)

    app.state.templates = TemplateRenderer(templates_env)
    app.state.db_manager = db_manager
    app.state.registry = registry

    # Register auth routes (login, callback, logout)
    if zitadel_issuer_url:
        register_auth_routes(app, db_manager, zitadel_issuer_url)
        logger.info(f"Admin OAuth enabled (issuer: {zitadel_issuer_url})")

    if os.getenv("ADMIN_DEV_LOGIN", "").lower() in ("true", "1"):
        # Dev-only login: supports ?role=admin|orgadmin|user for testing all roles
        from .auth import set_session

        @app.get("/login")
        async def dev_login(request: Request):
            from starlette.responses import RedirectResponse

            role = request.query_params.get("role", "admin")

            if role == "orgadmin":
                sub = "orgadmin-test-user"
                email = "orgadmin@company-a.com"
                org_id = "org-company-a"
                org_name = "Company A"
                is_admin = False
                redirect_url = "/admin/setup"
            elif role == "user":
                sub = "regular-test-user"
                email = "user@company-a.com"
                org_id = "org-company-a"
                org_name = "Company A"
                is_admin = False
                redirect_url = "/admin/setup"
            else:
                sub = os.getenv("ADMIN_BOOTSTRAP_SUB", "dev-admin")
                email = os.getenv("ADMIN_BOOTSTRAP_EMAIL", "dev@localhost")
                org_id = ""
                org_name = ""
                is_admin = True
                redirect_url = "/admin/"

            resp = RedirectResponse(url=redirect_url, status_code=302)
            set_session(
                resp,
                {
                    "sub": sub,
                    "email": email,
                    "org_id": org_id,
                    "org_name": org_name,
                    "is_admin": is_admin,
                },
            )
            return resp

        logger.warning("DEV LOGIN enabled at /admin/login — not for production!")
    elif not zitadel_issuer_url:
        logger.warning("No OAUTH_ISSUER_URL set — admin panel login will not work")

    # Register CRUD routes
    register_admin_routes(app, db_manager)

    logger.info("Admin panel app created")
    return app
