"""MCP tool handlers for Odoo operations.

This module implements MCP tools for performing operations on Odoo data.
Tools are different from resources - they can have side effects and perform
actions like creating, updating, or deleting records.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .access_control import AccessControlError, AccessController
from .config import OdooConfig
from .connection_protocol import OdooConnectionProtocol
from .error_handling import (
    NotFoundError,
    ValidationError,
)
from .error_sanitizer import ErrorSanitizer
from .logging_config import get_logger, perf_logger
from .odoo_connection import OdooConnectionError
from .schemas import (
    CreateResult,
    DeleteResult,
    FieldSelectionMetadata,
    ModelsResult,
    RecordResult,
    ResourceTemplatesResult,
    SearchResult,
    ServerInfoResult,
    UpdateResult,
)

if TYPE_CHECKING:
    from .registry import ConnectionRegistry

logger = get_logger(__name__)


class OdooToolHandler:
    """Handles MCP tool requests for Odoo operations."""

    def __init__(
        self,
        app: FastMCP,
        connection: Optional[OdooConnectionProtocol] = None,
        access_controller: Optional[AccessController] = None,
        config: Optional[OdooConfig] = None,
        registry: Optional[ConnectionRegistry] = None,
    ):
        """Initialize tool handler.

        Supports two modes:
        - Registry mode (HTTP/multi-tenant): pass registry, connection per-request via auth context
        - Fallback mode (stdio/single-tenant): pass connection + access_controller directly

        Args:
            app: FastMCP application instance
            connection: Fallback Odoo connection for stdio mode
            access_controller: Fallback access controller for stdio mode
            config: Odoo configuration instance
            registry: ConnectionRegistry for multi-tenant lookups (HTTP mode)
        """
        self.app = app
        self.registry = registry
        self._fallback_connection = connection
        self._fallback_access_controller = access_controller
        self.config = config

        # Register tools
        self._register_tools()

    async def _get_user_context(self) -> Tuple[OdooConnectionProtocol, AccessController]:
        """Get connection and access controller for the current request.

        In HTTP mode with OAuth, reads the authenticated user's subject ID
        from the auth context and resolves the connection via the registry.
        In stdio mode, returns the fallback connection.

        Returns:
            Tuple of (connection, access_controller)

        Raises:
            ValidationError: If no connection is available
        """
        if self.registry is not None:
            try:
                from mcp.server.auth.middleware.auth_context import get_access_token

                access_token = get_access_token()
                if access_token is not None:
                    # Parse sub:org_id format packed by ZitadelTokenVerifier
                    client_id = access_token.client_id
                    if ":" in client_id:
                        sub, org_id = client_id.split(":", 1)
                    else:
                        sub, org_id = client_id, ""
                    cached = await self.registry.get_connection(sub, org_id)
                    return cached.connection, cached.access_controller
            except Exception:
                # Fall through to fallback
                pass

        # Fallback for stdio mode or when no auth context is available
        if self._fallback_connection is not None and self._fallback_access_controller is not None:
            return self._fallback_connection, self._fallback_access_controller

        raise ValidationError("No Odoo connection available")

    # Convenience properties for backward compatibility in non-async helpers
    @property
    def connection(self) -> OdooConnectionProtocol:
        """Fallback connection for sync helpers. Use _get_user_context() in async handlers."""
        return self._fallback_connection

    @property
    def access_controller(self) -> AccessController:
        """Fallback access controller for sync helpers. Use _get_user_context() in async handlers."""
        return self._fallback_access_controller

    def _format_datetime(self, value: str) -> str:
        """Format datetime values to ISO 8601 with timezone."""
        if not value or not isinstance(value, str):
            return value

        # Handle Odoo's compact datetime format (YYYYMMDDTHH:MM:SS)
        if len(value) == 17 and "T" in value and "-" not in value:
            try:
                dt = datetime.strptime(value, "%Y%m%dT%H:%M:%S")
                return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            except ValueError:
                pass

        # Handle standard Odoo datetime format (YYYY-MM-DD HH:MM:SS)
        if " " in value and len(value) == 19:
            try:
                dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            except ValueError:
                pass

        return value

    def _process_record_dates(
        self,
        record: Dict[str, Any],
        model: str,
        connection: Optional[OdooConnectionProtocol] = None,
    ) -> Dict[str, Any]:
        """Process datetime fields in a record to ensure proper formatting."""
        conn = connection or self._fallback_connection
        # Common datetime field names in Odoo
        known_datetime_fields = {
            "create_date",
            "write_date",
            "date",
            "datetime",
            "date_start",
            "date_end",
            "date_from",
            "date_to",
            "date_order",
            "date_invoice",
            "date_due",
            "last_update",
            "last_activity",
            "activity_date_deadline",
        }

        # First try to get field metadata
        fields_info = None
        try:
            fields_info = conn.fields_get(model)
        except Exception:
            # Field metadata unavailable, will use fallback detection
            pass

        # Process each field in the record
        for field_name, field_value in record.items():
            if not isinstance(field_value, str):
                continue

            should_format = False

            # Check if field is identified as datetime from metadata
            if fields_info and isinstance(fields_info, dict) and field_name in fields_info:
                field_type = fields_info[field_name].get("type")
                if field_type == "datetime":
                    should_format = True

            # Check if field name suggests it's a datetime field
            if not should_format and field_name in known_datetime_fields:
                should_format = True

            # Check if field name ends with common datetime suffixes
            if not should_format and any(
                field_name.endswith(suffix) for suffix in ["_date", "_datetime", "_time"]
            ):
                should_format = True

            # Pattern-based detection for datetime-like strings
            if not should_format and (
                (
                    len(field_value) == 17 and "T" in field_value and "-" not in field_value
                )  # 20250607T21:55:52
                or (
                    len(field_value) == 19 and " " in field_value and field_value.count("-") == 2
                )  # 2025-06-07 21:55:52
            ):
                should_format = True

            # Apply formatting if needed
            if should_format:
                formatted = self._format_datetime(field_value)
                if formatted != field_value:
                    record[field_name] = formatted

        return record

    def _should_include_field_by_default(self, field_name: str, field_info: Dict[str, Any]) -> bool:
        """Determine if a field should be included in default response.

        Args:
            field_name: Name of the field
            field_info: Field metadata from fields_get()

        Returns:
            True if field should be included in default response
        """
        # Always include essential fields
        always_include = {"id", "name", "display_name", "active"}
        if field_name in always_include:
            return True

        # Exclude system/technical fields by prefix
        exclude_prefixes = ("_", "message_", "activity_", "website_message_")
        if field_name.startswith(exclude_prefixes):
            return False

        # Exclude specific technical fields
        exclude_fields = {
            "write_date",
            "create_date",
            "write_uid",
            "create_uid",
            "__last_update",
            "access_token",
            "access_warning",
            "access_url",
        }
        if field_name in exclude_fields:
            return False

        # Get field type
        field_type = field_info.get("type", "")

        # Exclude binary and large fields
        if field_type in ("binary", "image", "html"):
            return False

        # Exclude expensive computed fields (non-stored)
        if field_info.get("compute") and not field_info.get("store", True):
            return False

        # Exclude one2many and many2many fields (can be large)
        if field_type in ("one2many", "many2many"):
            return False

        # Include required fields
        if field_info.get("required"):
            return True

        # Include simple stored fields that are searchable
        if field_info.get("store", True) and field_info.get("searchable", True):
            if field_type in (
                "char",
                "text",
                "boolean",
                "integer",
                "float",
                "date",
                "datetime",
                "selection",
                "many2one",
            ):
                return True

        return False

    def _score_field_importance(self, field_name: str, field_info: Dict[str, Any]) -> int:
        """Score field importance for smart default selection.

        Args:
            field_name: Name of the field
            field_info: Field metadata from fields_get()

        Returns:
            Importance score (higher = more important)
        """
        # Tier 1: Essential fields (always included)
        if field_name in {"id", "name", "display_name", "active"}:
            return 1000

        # Exclude system/technical fields by prefix
        exclude_prefixes = ("_", "message_", "activity_", "website_message_")
        if field_name.startswith(exclude_prefixes):
            return 0

        # Exclude specific technical fields
        exclude_fields = {
            "write_date",
            "create_date",
            "write_uid",
            "create_uid",
            "__last_update",
            "access_token",
            "access_warning",
            "access_url",
        }
        if field_name in exclude_fields:
            return 0

        score = 0

        # Tier 2: Required fields are very important
        if field_info.get("required"):
            score += 500

        # Tier 3: Field type importance
        field_type = field_info.get("type", "")
        type_scores = {
            "char": 200,
            "boolean": 180,
            "selection": 170,
            "integer": 160,
            "float": 160,
            "monetary": 140,
            "date": 150,
            "datetime": 150,
            "many2one": 120,  # Relations useful but not primary
            "text": 80,
            "one2many": 40,
            "many2many": 40,  # Heavy relations
            "binary": 10,
            "html": 10,
            "image": 10,  # Heavy content
        }
        score += type_scores.get(field_type, 50)

        # Tier 4: Storage and searchability bonuses
        if field_info.get("store", True):
            score += 80
        if field_info.get("searchable", True):
            score += 40

        # Tier 5: Business-relevant field patterns (bonus)
        business_patterns = [
            "state",
            "status",
            "stage",
            "priority",
            "company",
            "currency",
            "amount",
            "total",
            "date",
            "user",
            "partner",
            "email",
            "phone",
            "address",
            "street",
            "city",
            "country",
            "code",
            "ref",
            "number",
        ]
        if any(pattern in field_name.lower() for pattern in business_patterns):
            score += 60

        # Exclude expensive computed fields (non-stored)
        if field_info.get("compute") and not field_info.get("store", True):
            score = min(score, 30)  # Cap computed fields at low score

        # Exclude large field types completely
        if field_type in ("binary", "image", "html"):
            return 0

        # Exclude one2many and many2many fields (can be large)
        if field_type in ("one2many", "many2many"):
            return 0

        return max(score, 0)

    def _get_smart_default_fields(
        self, model: str, connection: Optional[OdooConnectionProtocol] = None
    ) -> Optional[List[str]]:
        """Get smart default fields for a model using field importance scoring.

        Args:
            model: The Odoo model name
            connection: Odoo connection to use (falls back to self._fallback_connection)

        Returns:
            List of field names to include by default, or None if unable to determine
        """
        conn = connection or self._fallback_connection
        try:
            # Get all field definitions
            fields_info = conn.fields_get(model)

            # Score all fields by importance
            field_scores = []
            for field_name, field_info in fields_info.items():
                score = self._score_field_importance(field_name, field_info)
                if score > 0:  # Only include fields with positive scores
                    field_scores.append((field_name, score))

            # Sort by score (highest first)
            field_scores.sort(key=lambda x: x[1], reverse=True)

            # Select top N fields based on configuration
            max_fields = self.config.max_smart_fields
            selected_fields = [field_name for field_name, _ in field_scores[:max_fields]]

            # Ensure essential fields are always included
            essential_fields = ["id", "name", "display_name", "active"]
            for field in essential_fields:
                if field in fields_info and field not in selected_fields:
                    selected_fields.append(field)

            # Remove duplicates while preserving order
            final_fields = []
            seen = set()
            for field in selected_fields:
                if field not in seen:
                    final_fields.append(field)
                    seen.add(field)

            # Ensure we have at least essential fields
            if not final_fields:
                final_fields = [f for f in essential_fields if f in fields_info]

            logger.debug(
                f"Smart default fields for {model}: {len(final_fields)} of {len(fields_info)} fields "
                f"(max configured: {max_fields})"
            )
            return final_fields

        except Exception as e:
            logger.warning(f"Could not determine default fields for {model}: {e}")
            # Return None to indicate we should get all fields
            return None

    def _register_tools(self):
        """Register all tool handlers with FastMCP."""

        @self.app.tool(
            title="Search Records",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def search_records(
            model: str,
            domain: Optional[Any] = None,
            fields: Optional[Any] = None,
            limit: int = 10,
            offset: int = 0,
            order: Optional[str] = None,
        ) -> SearchResult:
            """Search for records in an Odoo model.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                domain: Odoo domain filter - can be:
                    - A list: [['is_company', '=', True]]
                    - A JSON string: "[['is_company', '=', true]]"
                    - None: returns all records (default)
                fields: Field selection options - can be:
                    - None (default): Returns smart selection of common fields
                    - A list: ["field1", "field2", ...] - Returns only specified fields
                    - A JSON string: '["field1", "field2"]' - Parsed to list
                    - ["__all__"] or '["__all__"]': Returns ALL fields (warning: may cause serialization errors)
                limit: Maximum number of records to return
                offset: Number of records to skip
                order: Sort order (e.g., 'name asc')

            Returns:
                Search results with records, total count, and pagination info
            """
            result = await self._handle_search_tool(model, domain, fields, limit, offset, order)
            return SearchResult(**result)

        @self.app.tool(
            title="Get Record",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def get_record(
            model: str,
            record_id: int,
            fields: Optional[List[str]] = None,
        ) -> RecordResult:
            """Get a specific record by ID with smart field selection.

            This tool supports selective field retrieval to optimize performance and response size.
            By default, returns a smart selection of commonly-used fields based on the model's field metadata.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                record_id: The record ID
                fields: Field selection options:
                    - None (default): Returns smart selection of common fields
                    - ["field1", "field2", ...]: Returns only specified fields
                    - ["__all__"]: Returns ALL fields (warning: can be very large)

            Workflow for field discovery:
            1. To see all available fields for a model, use the resource:
               read("odoo://res.partner/fields")
            2. Then request specific fields:
               get_record("res.partner", 1, fields=["name", "email", "phone"])

            Examples:
                # Get smart defaults (recommended)
                get_record("res.partner", 1)

                # Get specific fields only
                get_record("res.partner", 1, fields=["name", "email", "phone"])

                # Get ALL fields (use with caution)
                get_record("res.partner", 1, fields=["__all__"])

            Returns:
                Record data with requested fields. When using smart defaults,
                includes metadata with field statistics.
            """
            return await self._handle_get_record_tool(model, record_id, fields)

        @self.app.tool(
            title="List Models",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def list_models() -> ModelsResult:
            """List all models enabled for MCP access with their allowed operations.

            Returns:
                List of models with their technical names, display names,
                and allowed operations (read, write, create, unlink).
            """
            result = await self._handle_list_models_tool()
            return ModelsResult(**result)

        @self.app.tool(
            title="List Resource Templates",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def list_resource_templates() -> ResourceTemplatesResult:
            """List available resource URI templates.

            Since MCP resources with parameters are registered as templates,
            they don't appear in the standard resource list. This tool provides
            information about available resource patterns you can use.

            Returns:
                Resource template definitions with examples and enabled models.
            """
            result = await self._handle_list_resource_templates_tool()
            return ResourceTemplatesResult(**result)

        @self.app.tool(
            title="Server Info",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def server_info() -> ServerInfoResult:
            """Get MCP server version and connection status.

            Returns:
                Server version, git commit, API version, and Odoo connection status.
            """
            from .server import GIT_COMMIT, SERVER_VERSION

            try:
                connection, _ac = await self._get_user_context()
                is_connected = (
                    connection.is_authenticated
                    if hasattr(connection, "is_authenticated")
                    else False
                )
                api_version = self.config.api_version if self.config else "json2"
                # Use the connection's actual URL (tenant URL), not the global config
                odoo_url = getattr(connection, "_base_url", None) or (
                    self.config.url if self.config else "multi-tenant"
                )
            except Exception:
                is_connected = False
                api_version = self.config.api_version if self.config else "unknown"
                odoo_url = "not connected"

            return ServerInfoResult(
                version=SERVER_VERSION,
                git_commit=GIT_COMMIT,
                api_version=api_version,
                odoo_url=odoo_url,
                connected=is_connected,
            )

        @self.app.tool(
            title="Create Record",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def create_record(
            model: str,
            values: Dict[str, Any],
        ) -> CreateResult:
            """Create a new record in an Odoo model.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                values: Field values for the new record

            Returns:
                Created record details with ID, URL, and confirmation.
            """
            result = await self._handle_create_record_tool(model, values)
            return CreateResult(**result)

        @self.app.tool(
            title="Update Record",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def update_record(
            model: str,
            record_id: int,
            values: Dict[str, Any],
        ) -> UpdateResult:
            """Update an existing record.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                record_id: The record ID to update
                values: Field values to update

            Returns:
                Updated record details with confirmation.
            """
            result = await self._handle_update_record_tool(model, record_id, values)
            return UpdateResult(**result)

        @self.app.tool(
            title="Delete Record",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=False,
            ),
        )
        async def delete_record(
            model: str,
            record_id: int,
        ) -> DeleteResult:
            """Delete a record.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                record_id: The record ID to delete

            Returns:
                Deletion confirmation with the deleted record's name and ID.
            """
            result = await self._handle_delete_record_tool(model, record_id)
            return DeleteResult(**result)

    async def _handle_search_tool(
        self,
        model: str,
        domain: Optional[Any],
        fields: Optional[Any],
        limit: int,
        offset: int,
        order: Optional[str],
    ) -> Dict[str, Any]:
        """Handle search tool request."""
        try:
            connection, access_controller = await self._get_user_context()
            with perf_logger.track_operation("tool_search", model=model):
                # Check model access
                access_controller.validate_model_access(model, "read")

                # Ensure we're connected
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                # Handle domain parameter - can be string or list
                parsed_domain = []
                if domain is not None:
                    if isinstance(domain, str):
                        # Parse string to list
                        try:
                            # First try standard JSON parsing
                            parsed_domain = json.loads(domain)
                        except json.JSONDecodeError:
                            # If that fails, try converting single quotes to double quotes
                            # This handles Python-style domain strings
                            try:
                                # Replace single quotes with double quotes for valid JSON
                                # But be careful not to replace quotes inside string values
                                json_domain = domain.replace("'", '"')
                                # Also need to ensure Python True/False are lowercase for JSON
                                json_domain = json_domain.replace("True", "true").replace(
                                    "False", "false"
                                )
                                parsed_domain = json.loads(json_domain)
                            except json.JSONDecodeError as e:
                                raise ValidationError(
                                    f"Invalid domain parameter. Expected JSON array, got: {domain[:100]}..."
                                ) from e

                        if not isinstance(parsed_domain, list):
                            raise ValidationError(
                                f"Domain must be a list, got {type(parsed_domain).__name__}"
                            )
                        logger.debug(f"Parsed domain from string: {parsed_domain}")
                    else:
                        # Already a list
                        parsed_domain = domain

                # Handle fields parameter - can be string or list
                parsed_fields = fields
                if fields is not None and isinstance(fields, str):
                    # Parse string to list
                    try:
                        parsed_fields = json.loads(fields)
                        if not isinstance(parsed_fields, list):
                            raise ValidationError(
                                f"Fields must be a list, got {type(parsed_fields).__name__}"
                            )
                    except json.JSONDecodeError as e:
                        raise ValidationError(
                            f"Invalid fields parameter. Expected JSON array, got: {fields[:100]}..."
                        ) from e

                # Set defaults
                if limit <= 0 or limit > self.config.max_limit:
                    limit = self.config.default_limit

                # Get total count
                total_count = connection.search_count(model, parsed_domain)

                # Search for records
                record_ids = connection.search(
                    model, parsed_domain, limit=limit, offset=offset, order=order
                )

                # Determine which fields to fetch
                fields_to_fetch = parsed_fields
                if parsed_fields is None:
                    # Use smart field selection to avoid serialization issues
                    fields_to_fetch = self._get_smart_default_fields(model, connection)
                    logger.debug(
                        f"Using smart defaults for {model} search: {len(fields_to_fetch) if fields_to_fetch else 'all'} fields"
                    )
                elif parsed_fields == ["__all__"]:
                    # Explicit request for all fields
                    fields_to_fetch = None  # Odoo interprets None as all fields
                    logger.debug(f"Fetching all fields for {model} search")

                # Read records
                records = []
                if record_ids:
                    records = connection.read(model, record_ids, fields_to_fetch)
                    # Process datetime fields in each record
                    records = [
                        self._process_record_dates(record, model, connection) for record in records
                    ]

                return {
                    "records": records,
                    "total": total_count,
                    "limit": limit,
                    "offset": offset,
                    "model": model,
                }

        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in search_records tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Search failed: {sanitized_msg}") from e

    async def _handle_get_record_tool(
        self,
        model: str,
        record_id: int,
        fields: Optional[List[str]],
    ) -> RecordResult:
        """Handle get record tool request."""
        try:
            connection, access_controller = await self._get_user_context()
            with perf_logger.track_operation("tool_get_record", model=model):
                # Check model access
                access_controller.validate_model_access(model, "read")

                # Ensure we're connected
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                # Determine which fields to fetch
                fields_to_fetch = fields
                use_smart_defaults = False
                total_fields = None
                field_selection_method = "explicit"

                if fields is None:
                    # Use smart field selection
                    fields_to_fetch = self._get_smart_default_fields(model, connection)
                    use_smart_defaults = True
                    field_selection_method = "smart_defaults"
                    logger.debug(
                        f"Using smart defaults for {model}: {len(fields_to_fetch) if fields_to_fetch else 'all'} fields"
                    )
                elif fields == ["__all__"]:
                    # Explicit request for all fields
                    fields_to_fetch = None  # Odoo interprets None as all fields
                    field_selection_method = "all"
                    logger.debug(f"Fetching all fields for {model}")
                else:
                    # Specific fields requested
                    logger.debug(f"Fetching specific fields for {model}: {fields}")

                # Read the record
                records = connection.read(model, [record_id], fields_to_fetch)

                if not records:
                    raise ValidationError(f"Record not found: {model} with ID {record_id}")

                # Process datetime fields in the record
                record = self._process_record_dates(records[0], model, connection)

                # Build metadata when using smart defaults
                metadata = None
                if use_smart_defaults:
                    try:
                        all_fields_info = connection.fields_get(model)
                        total_fields = len(all_fields_info)
                    except Exception:
                        pass

                    metadata = FieldSelectionMetadata(
                        fields_returned=len(record),
                        field_selection_method=field_selection_method,
                        total_fields_available=total_fields,
                        note=f"Limited fields returned for performance. Use fields=['__all__'] for all fields or see odoo://{model}/fields for available fields.",
                    )

                return RecordResult(record=record, metadata=metadata)

        except ValidationError:
            raise
        except NotFoundError as e:
            raise ValidationError(str(e)) from e
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in get_record tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to get record: {sanitized_msg}") from e

    async def _handle_list_models_tool(self) -> Dict[str, Any]:
        """Handle list models tool request with permissions."""
        try:
            _connection, access_controller = await self._get_user_context()
            with perf_logger.track_operation("tool_list_models"):
                # Get models from MCP access controller
                models = access_controller.get_enabled_models()

                # Enrich with permissions for each model
                enriched_models = []
                for model_info in models:
                    model_name = model_info["model"]
                    try:
                        # Get permissions for this model
                        permissions = access_controller.get_model_permissions(model_name)
                        enriched_model = {
                            "model": model_name,
                            "name": model_info["name"],
                            "operations": {
                                "read": permissions.can_read,
                                "write": permissions.can_write,
                                "create": permissions.can_create,
                                "unlink": permissions.can_unlink,
                            },
                        }
                        enriched_models.append(enriched_model)
                    except Exception as e:
                        # If we can't get permissions for a model, include it with all operations false
                        logger.warning(f"Failed to get permissions for {model_name}: {e}")
                        enriched_model = {
                            "model": model_name,
                            "name": model_info["name"],
                            "operations": {
                                "read": False,
                                "write": False,
                                "create": False,
                                "unlink": False,
                            },
                        }
                        enriched_models.append(enriched_model)

                # Return proper JSON structure with enriched models array
                return {"models": enriched_models}
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Error in list_models tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to list models: {sanitized_msg}") from e

    async def _handle_list_resource_templates_tool(self) -> Dict[str, Any]:
        """Handle list resource templates tool request."""
        try:
            _connection, access_controller = await self._get_user_context()
            # Get list of enabled models that can be used with resources
            enabled_models = access_controller.get_enabled_models()
            model_names = [m["model"] for m in enabled_models if m.get("read", True)]

            # Define the resource templates
            templates = [
                {
                    "uri_template": "odoo://{model}/record/{record_id}",
                    "description": "Get a specific record by ID",
                    "parameters": {
                        "model": "Odoo model name (e.g., res.partner)",
                        "record_id": "Record ID (e.g., 10)",
                    },
                    "example": "odoo://res.partner/record/10",
                },
                {
                    "uri_template": "odoo://{model}/search",
                    "description": "Basic search returning first 10 records",
                    "parameters": {
                        "model": "Odoo model name",
                    },
                    "example": "odoo://res.partner/search",
                    "note": "Query parameters are not supported. Use search_records tool for advanced queries.",
                },
                {
                    "uri_template": "odoo://{model}/count",
                    "description": "Count all records in a model",
                    "parameters": {
                        "model": "Odoo model name",
                    },
                    "example": "odoo://res.partner/count",
                    "note": "Query parameters are not supported. Use search_records tool for filtered counts.",
                },
                {
                    "uri_template": "odoo://{model}/fields",
                    "description": "Get field definitions for a model",
                    "parameters": {"model": "Odoo model name"},
                    "example": "odoo://res.partner/fields",
                },
            ]

            # Return the resource template information
            return {
                "templates": templates,
                "enabled_models": model_names[:10],  # Show first 10 as examples
                "total_models": len(model_names),
                "note": "Resource URIs do not support query parameters. Use tools (search_records, get_record) for advanced operations with filtering, pagination, and field selection.",
            }

        except Exception as e:
            logger.error(f"Error in list_resource_templates tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to list resource templates: {sanitized_msg}") from e

    async def _handle_create_record_tool(
        self,
        model: str,
        values: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle create record tool request."""
        try:
            connection, access_controller = await self._get_user_context()
            with perf_logger.track_operation("tool_create_record", model=model):
                # Check model access
                access_controller.validate_model_access(model, "create")

                # Ensure we're connected
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                # Validate required fields
                if not values:
                    raise ValidationError("No values provided for record creation")

                # Create the record
                record_id = connection.create(model, values)

                # Return only essential fields to minimize context usage
                # Users can use get_record if they need more fields
                essential_fields = ["id", "name", "display_name"]

                # Filter to fields that actually exist on this model
                try:
                    model_fields = connection.fields_get(model, ["string", "type"])
                    essential_fields = [f for f in essential_fields if f in model_fields]
                    if "id" not in essential_fields:
                        essential_fields.insert(0, "id")
                except Exception:
                    essential_fields = ["id"]

                # Read only the essential fields
                records = connection.read(model, [record_id], essential_fields)
                if not records:
                    raise ValidationError(
                        f"Failed to read created record: {model} with ID {record_id}"
                    )

                # Process dates in the minimal record
                record = self._process_record_dates(records[0], model, connection)

                # Generate direct URL to the record in Odoo
                base_url = (
                    getattr(connection, "_base_url", None)
                    or (self.config.url if self.config else "")
                ).rstrip("/")
                record_url = f"{base_url}/web#id={record_id}&model={model}&view_type=form"

                return {
                    "success": True,
                    "record": record,
                    "url": record_url,
                    "message": f"Successfully created {model} record with ID {record_id}",
                }

        except ValidationError:
            raise
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in create_record tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to create record: {sanitized_msg}") from e

    async def _handle_update_record_tool(
        self,
        model: str,
        record_id: int,
        values: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle update record tool request."""
        try:
            connection, access_controller = await self._get_user_context()
            with perf_logger.track_operation("tool_update_record", model=model):
                # Check model access
                access_controller.validate_model_access(model, "write")

                # Ensure we're connected
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                # Validate input
                if not values:
                    raise ValidationError("No values provided for record update")

                # Check if record exists (only fetch ID to verify existence)
                existing = connection.read(model, [record_id], ["id"])
                if not existing:
                    raise NotFoundError(f"Record not found: {model} with ID {record_id}")

                # Update the record
                success = connection.write(model, [record_id], values)

                # Return only essential fields to minimize context usage
                # Users can use get_record if they need more fields
                essential_fields = ["id", "name", "display_name"]

                # Filter to fields that actually exist on this model
                try:
                    model_fields = connection.fields_get(model, ["string", "type"])
                    essential_fields = [f for f in essential_fields if f in model_fields]
                    if "id" not in essential_fields:
                        essential_fields.insert(0, "id")
                except Exception:
                    essential_fields = ["id"]

                # Read only the essential fields
                records = connection.read(model, [record_id], essential_fields)
                if not records:
                    raise ValidationError(
                        f"Failed to read updated record: {model} with ID {record_id}"
                    )

                # Process dates in the minimal record
                record = self._process_record_dates(records[0], model, connection)

                # Generate direct URL to the record in Odoo
                base_url = (
                    getattr(connection, "_base_url", None)
                    or (self.config.url if self.config else "")
                ).rstrip("/")
                record_url = f"{base_url}/web#id={record_id}&model={model}&view_type=form"

                return {
                    "success": success,
                    "record": record,
                    "url": record_url,
                    "message": f"Successfully updated {model} record with ID {record_id}",
                }

        except ValidationError:
            raise
        except NotFoundError as e:
            raise ValidationError(str(e)) from e
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in update_record tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to update record: {sanitized_msg}") from e

    async def _handle_delete_record_tool(
        self,
        model: str,
        record_id: int,
    ) -> Dict[str, Any]:
        """Handle delete record tool request."""
        try:
            connection, access_controller = await self._get_user_context()
            with perf_logger.track_operation("tool_delete_record", model=model):
                # Check model access
                access_controller.validate_model_access(model, "unlink")

                # Ensure we're connected
                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                # Check if record exists
                existing = connection.read(model, [record_id])
                if not existing:
                    raise NotFoundError(f"Record not found: {model} with ID {record_id}")

                # Store some info about the record before deletion
                record_name = existing[0].get(
                    "name", existing[0].get("display_name", f"ID {record_id}")
                )

                # Delete the record
                success = connection.unlink(model, [record_id])

                return {
                    "success": success,
                    "deleted_id": record_id,
                    "deleted_name": record_name,
                    "message": f"Successfully deleted {model} record '{record_name}' (ID: {record_id})",
                }

        except ValidationError:
            raise
        except NotFoundError as e:
            raise ValidationError(str(e)) from e
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in delete_record tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to delete record: {sanitized_msg}") from e


def register_tools(
    app: FastMCP,
    connection: Optional[OdooConnectionProtocol] = None,
    access_controller: Optional[AccessController] = None,
    config: Optional[OdooConfig] = None,
    registry: Optional[ConnectionRegistry] = None,
) -> OdooToolHandler:
    """Register all Odoo tools with the FastMCP app.

    Args:
        app: FastMCP application instance
        connection: Odoo connection instance (stdio/single-tenant mode)
        access_controller: Access control instance (stdio/single-tenant mode)
        config: Odoo configuration instance
        registry: ConnectionRegistry for multi-tenant mode (HTTP)

    Returns:
        The tool handler instance
    """
    handler = OdooToolHandler(
        app,
        registry=registry,
        connection=connection,
        access_controller=access_controller,
        config=config,
    )
    logger.info("Registered Odoo MCP tools")
    return handler
