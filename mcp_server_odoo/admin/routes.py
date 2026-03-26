"""Admin CRUD routes for managing tenants and users.

All routes require admin authentication via the session cookie.
"""

import logging
import os
import re

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .auth import (
    generate_csrf_token,
    require_admin,
    require_login,
    validate_csrf_token,
)

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Generate a URL-safe slug from a name."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug.strip("-")


def register_admin_routes(app, db_manager):
    """Register admin CRUD routes.

    Args:
        app: FastAPI app instance
        db_manager: DatabaseManager for tenant operations
    """

    # --- Dashboard ---

    @app.get("/")
    @require_admin
    async def admin_dashboard(request: Request):
        """Dashboard: list all tenants with user counts."""
        tenants = await db_manager.list_tenants(active_only=False)

        # Get user counts per tenant
        tenant_stats = []
        for tenant in tenants:
            users = await db_manager.list_users_for_tenant(tenant.id)
            active_users = sum(1 for u in users if u.is_active)
            tenant_stats.append(
                {
                    "tenant": tenant,
                    "user_count": len(users),
                    "active_user_count": active_users,
                }
            )

        templates = request.app.state.templates
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "admin": request.state.admin,
                "tenant_stats": tenant_stats,
                "csrf_token": generate_csrf_token(request.state.admin),
            },
        )

    # --- Tenant CRUD ---

    @app.get("/tenants/new")
    @require_admin
    async def new_tenant_form(request: Request):
        """Show form to add a new tenant."""
        templates = request.app.state.templates
        return templates.TemplateResponse(
            "tenant_form.html",
            {
                "request": request,
                "admin": request.state.admin,
                "edit_mode": False,
                "tenant": None,
                "csrf_token": generate_csrf_token(request.state.admin),
            },
        )

    @app.post("/tenants")
    @require_admin
    async def create_tenant(request: Request):
        """Create a new tenant."""
        form = await request.form()

        # CSRF validation
        csrf_token = form.get("csrf_token", "")
        if not validate_csrf_token(csrf_token):
            return RedirectResponse(url="/admin/tenants/new", status_code=302)

        name = form.get("name", "").strip()
        slug = form.get("slug", "").strip() or _slugify(name)
        odoo_url = form.get("odoo_url", "").strip()
        api_version = form.get("api_version", "json2").strip()
        zitadel_org_id = form.get("zitadel_org_id", "").strip() or None

        if not name or not odoo_url:
            # Re-show form with error
            templates = request.app.state.templates
            return templates.TemplateResponse(
                "tenant_form.html",
                {
                    "request": request,
                    "admin": request.state.admin,
                    "edit_mode": False,
                    "tenant": None,
                    "error": "Name and Odoo URL are required.",
                    "form_data": {
                        "name": name,
                        "slug": slug,
                        "odoo_url": odoo_url,
                        "api_version": api_version,
                        "zitadel_org_id": zitadel_org_id or "",
                    },
                    "csrf_token": generate_csrf_token(request.state.admin),
                },
            )

        try:
            tenant = await db_manager.create_tenant(
                name=name,
                slug=slug,
                odoo_url=odoo_url,
                odoo_db="",
                api_version=api_version,
                zitadel_org_id=zitadel_org_id,
            )
            logger.info(f"Admin {request.state.admin['email']} created tenant: {name}")
            return RedirectResponse(url=f"/admin/tenants/{tenant.id}", status_code=302)
        except Exception as e:
            logger.error(f"Failed to create tenant: {e}")
            templates = request.app.state.templates
            return templates.TemplateResponse(
                "tenant_form.html",
                {
                    "request": request,
                    "admin": request.state.admin,
                    "edit_mode": False,
                    "tenant": None,
                    "error": f"Failed to create tenant: {e}",
                    "form_data": {
                        "name": name,
                        "slug": slug,
                        "odoo_url": odoo_url,
                        "api_version": api_version,
                        "zitadel_org_id": zitadel_org_id or "",
                    },
                    "csrf_token": generate_csrf_token(request.state.admin),
                },
            )

    @app.get("/tenants/{tenant_id}")
    @require_admin
    async def tenant_detail(request: Request, tenant_id: int):
        """Show tenant detail with user list."""
        tenant = await db_manager.get_tenant(tenant_id)
        if not tenant:
            return RedirectResponse(url="/admin/", status_code=302)

        users = await db_manager.list_users_for_tenant(tenant_id)

        templates = request.app.state.templates
        return templates.TemplateResponse(
            "tenant_detail.html",
            {
                "request": request,
                "admin": request.state.admin,
                "tenant": tenant,
                "users": users,
                "csrf_token": generate_csrf_token(request.state.admin),
            },
        )

    @app.get("/tenants/{tenant_id}/edit")
    @require_admin
    async def edit_tenant_form(request: Request, tenant_id: int):
        """Show form to edit a tenant."""
        tenant = await db_manager.get_tenant(tenant_id)
        if not tenant:
            return RedirectResponse(url="/admin/", status_code=302)

        templates = request.app.state.templates
        return templates.TemplateResponse(
            "tenant_form.html",
            {
                "request": request,
                "admin": request.state.admin,
                "edit_mode": True,
                "tenant": tenant,
                "csrf_token": generate_csrf_token(request.state.admin),
            },
        )

    @app.post("/tenants/{tenant_id}")
    @require_admin
    async def update_tenant(request: Request, tenant_id: int):
        """Update a tenant."""
        form = await request.form()

        csrf_token = form.get("csrf_token", "")
        if not validate_csrf_token(csrf_token):
            return RedirectResponse(url=f"/admin/tenants/{tenant_id}/edit", status_code=302)

        name = form.get("name", "").strip()
        slug = form.get("slug", "").strip()
        odoo_url = form.get("odoo_url", "").strip()
        api_version = form.get("api_version", "json2").strip()
        zitadel_org_id = form.get("zitadel_org_id", "").strip() or None
        is_active = form.get("is_active") == "on"

        try:
            await db_manager.update_tenant(
                tenant_id,
                name=name,
                slug=slug,
                odoo_url=odoo_url,
                odoo_db="",
                api_version=api_version,
                zitadel_org_id=zitadel_org_id,
                is_active=is_active,
            )
            logger.info(f"Admin {request.state.admin['email']} updated tenant {tenant_id}")
            return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)
        except Exception as e:
            logger.error(f"Failed to update tenant {tenant_id}: {e}")
            tenant = await db_manager.get_tenant(tenant_id)
            templates = request.app.state.templates
            return templates.TemplateResponse(
                "tenant_form.html",
                {
                    "request": request,
                    "admin": request.state.admin,
                    "edit_mode": True,
                    "tenant": tenant,
                    "error": f"Failed to update tenant: {e}",
                    "csrf_token": generate_csrf_token(request.state.admin),
                },
            )

    @app.post("/tenants/{tenant_id}/delete")
    @require_admin
    async def delete_tenant(request: Request, tenant_id: int):
        """Delete a tenant."""
        form = await request.form()
        csrf_token = form.get("csrf_token", "")
        if not validate_csrf_token(csrf_token):
            return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)

        tenant = await db_manager.get_tenant(tenant_id)
        if tenant:
            await db_manager.delete_tenant(tenant_id)
            logger.info(f"Admin {request.state.admin['email']} deleted tenant: {tenant.name}")

        return RedirectResponse(url="/admin/", status_code=302)

    # --- User management ---

    @app.get("/tenants/{tenant_id}/users/new")
    @require_admin
    async def new_user_form(request: Request, tenant_id: int):
        """Show form to add a user to a tenant."""
        tenant = await db_manager.get_tenant(tenant_id)
        if not tenant:
            return RedirectResponse(url="/admin/", status_code=302)

        templates = request.app.state.templates
        return templates.TemplateResponse(
            "user_form.html",
            {
                "request": request,
                "admin": request.state.admin,
                "tenant": tenant,
                "csrf_token": generate_csrf_token(request.state.admin),
            },
        )

    @app.post("/tenants/{tenant_id}/users")
    @require_admin
    async def create_user(request: Request, tenant_id: int):
        """Create a user connection for a tenant."""
        form = await request.form()

        csrf_token = form.get("csrf_token", "")
        if not validate_csrf_token(csrf_token):
            return RedirectResponse(url=f"/admin/tenants/{tenant_id}/users/new", status_code=302)

        tenant = await db_manager.get_tenant(tenant_id)
        if not tenant:
            return RedirectResponse(url="/admin/", status_code=302)

        zitadel_sub = form.get("zitadel_sub", "").strip()
        email = form.get("email", "").strip() or None

        if not zitadel_sub:
            templates = request.app.state.templates
            return templates.TemplateResponse(
                "user_form.html",
                {
                    "request": request,
                    "admin": request.state.admin,
                    "tenant": tenant,
                    "error": "Zitadel Subject ID is required.",
                    "form_data": {
                        "zitadel_sub": zitadel_sub,
                        "email": email or "",
                    },
                    "csrf_token": generate_csrf_token(request.state.admin),
                },
            )

        # User will set their own API key via self-service setup
        odoo_api_key = "PENDING_USER_SETUP"

        try:
            await db_manager.create_user_connection(
                zitadel_sub=zitadel_sub,
                tenant_id=tenant_id,
                odoo_api_key=odoo_api_key,
                email=email,
            )
            logger.info(
                f"Admin {request.state.admin['email']} added user {zitadel_sub} to tenant {tenant.name}"
            )
            return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)
        except Exception as e:
            logger.error(f"Failed to create user connection: {e}")
            templates = request.app.state.templates
            return templates.TemplateResponse(
                "user_form.html",
                {
                    "request": request,
                    "admin": request.state.admin,
                    "tenant": tenant,
                    "error": f"Failed to add user: {e}",
                    "form_data": {
                        "zitadel_sub": zitadel_sub,
                        "email": email or "",
                        "odoo_api_key": odoo_api_key,
                    },
                    "csrf_token": generate_csrf_token(request.state.admin),
                },
            )

    @app.post("/tenants/{tenant_id}/users/{user_id}/delete")
    @require_admin
    async def delete_user(request: Request, tenant_id: int, user_id: int):
        """Remove a user connection."""
        form = await request.form()
        csrf_token = form.get("csrf_token", "")
        if not validate_csrf_token(csrf_token):
            return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)

        await db_manager.delete_user_connection(user_id)
        logger.info(
            f"Admin {request.state.admin['email']} deleted user connection {user_id} from tenant {tenant_id}"
        )

        # If htmx request, return empty content (row removed)
        if request.headers.get("HX-Request"):
            return HTMLResponse("")

        return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)

    @app.post("/tenants/{tenant_id}/users/{user_id}/toggle")
    @require_admin
    async def toggle_user(request: Request, tenant_id: int, user_id: int):
        """Toggle user active/inactive status."""
        form = await request.form()
        csrf_token = form.get("csrf_token", "")
        if not validate_csrf_token(csrf_token):
            return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)

        # Get current user connection to find current status
        users = await db_manager.list_users_for_tenant(tenant_id)
        user = next((u for u in users if u.id == user_id), None)
        if not user:
            return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)

        new_status = not user.is_active
        await db_manager.update_user_connection(user_id, is_active=new_status)
        status_str = "activated" if new_status else "deactivated"
        logger.info(
            f"Admin {request.state.admin['email']} {status_str} user {user_id} in tenant {tenant_id}"
        )

        # If htmx request, return the updated toggle button
        if request.headers.get("HX-Request"):
            # Re-fetch to get current state
            users = await db_manager.list_users_for_tenant(tenant_id)
            user = next((u for u in users if u.id == user_id), None)
            if user:
                csrf = generate_csrf_token(request.state.admin)
                active_class = (
                    "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300"
                    if user.is_active
                    else "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300"
                )
                active_text = "Active" if user.is_active else "Inactive"
                toggle_text = "Deactivate" if user.is_active else "Activate"
                toggle_class = (
                    "text-yellow-600 hover:text-yellow-900 dark:text-yellow-400"
                    if user.is_active
                    else "text-green-600 hover:text-green-900 dark:text-green-400"
                )
                html = f"""<tr id="user-row-{user.id}">
                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">{user.email or "-"}</td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400 font-mono text-xs">{user.zitadel_sub}</td>
                    <td class="px-6 py-4 whitespace-nowrap">
                        <span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full {active_class}">{active_text}</span>
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm space-x-3">
                        <form method="post" action="/admin/tenants/{tenant_id}/users/{user.id}/toggle"
                              hx-post="/admin/tenants/{tenant_id}/users/{user.id}/toggle"
                              hx-target="#user-row-{user.id}" hx-swap="outerHTML"
                              class="inline">
                            <input type="hidden" name="csrf_token" value="{csrf}">
                            <button type="submit" class="{toggle_class}">{toggle_text}</button>
                        </form>
                        <form method="post" action="/admin/tenants/{tenant_id}/users/{user.id}/delete"
                              hx-post="/admin/tenants/{tenant_id}/users/{user.id}/delete"
                              hx-target="#user-row-{user.id}" hx-swap="outerHTML"
                              hx-confirm="Are you sure you want to remove this user?"
                              class="inline">
                            <input type="hidden" name="csrf_token" value="{csrf}">
                            <button type="submit" class="text-red-600 hover:text-red-900 dark:text-red-400">Remove</button>
                        </form>
                    </td>
                </tr>"""
                return HTMLResponse(html)

        return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)

    # --- Self-service setup (any logged-in user) ---

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

        if not odoo_url or not odoo_api_key:
            return RedirectResponse(url="/admin/setup", status_code=302)

        try:
            await db_manager.upsert_user_connection(
                zitadel_sub=user["sub"],
                odoo_url=odoo_url,
                odoo_api_key=odoo_api_key,
                email=user.get("email"),
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
        logger.info(f"User {user['email']} deleted their connection")

        return RedirectResponse(url="/admin/setup", status_code=302)
