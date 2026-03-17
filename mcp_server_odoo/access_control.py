"""Access control integration with Odoo MCP module.

This module provides integration with the Odoo MCP module's access control
system via REST API endpoints.
"""

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .config import OdooConfig

logger = logging.getLogger(__name__)


class AccessControlError(Exception):
    """Exception for access control failures."""

    pass


@dataclass
class ModelPermissions:
    """Permissions for a specific model."""

    model: str
    enabled: bool
    can_read: bool = False
    can_write: bool = False
    can_create: bool = False
    can_unlink: bool = False

    def can_perform(self, operation: str) -> bool:
        """Check if a specific operation is allowed."""
        operation_map = {
            "read": self.can_read,
            "write": self.can_write,
            "create": self.can_create,
            "unlink": self.can_unlink,
            "delete": self.can_unlink,  # Alias
        }
        return operation_map.get(operation, False)


@dataclass
class CacheEntry:
    """Cache entry for permission data."""

    data: Any
    timestamp: datetime

    def is_expired(self, ttl_seconds: int) -> bool:
        """Check if cache entry is expired."""
        return datetime.now() - self.timestamp > timedelta(seconds=ttl_seconds)


class AccessController:
    """Controls access to Odoo models via MCP module REST API."""

    # Cache TTL in seconds
    CACHE_TTL = 300  # 5 minutes

    # MCP REST API endpoints
    MODELS_ENDPOINT = "/mcp/models"
    MODEL_ACCESS_ENDPOINT = "/mcp/models/{model}/access"

    def __init__(self, config: OdooConfig, connection: Any = None, cache_ttl: int = CACHE_TTL):
        """Initialize access controller.

        Args:
            config: OdooConfig with connection details and API key
            connection: Optional OdooJSON2Connection — used to fetch real Odoo
                        permissions in JSON/2 mode. When provided, model access
                        reflects the API key user's actual Odoo ACLs.
            cache_ttl: Cache time-to-live in seconds
        """
        self.config = config
        self.connection = connection
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, CacheEntry] = {}

        # Parse base URL
        self.base_url = config.url.rstrip("/")

        # In JSON/2 mode, permissions are fetched lazily from Odoo
        if config.api_version == "json2":
            if connection is not None:
                logger.info(
                    "JSON/2 mode: Permissions will be fetched from Odoo (ir.model.access) "
                    "and cached for %d seconds.",
                    cache_ttl,
                )
            else:
                logger.info(
                    "JSON/2 mode: No connection provided — access control delegated to "
                    "Odoo server (403 on denied operations)."
                )
            return  # No client-side access control needed

        # Validate API key is available for standard mode
        if not config.api_key:
            raise AccessControlError(
                "API key required for access control. Please configure ODOO_API_KEY."
            )

        logger.info(f"Initialized AccessController for {self.base_url}")

    def _make_request(self, endpoint: str, timeout: int = 30) -> Dict[str, Any]:
        """Make authenticated request to MCP REST API.

        Args:
            endpoint: API endpoint path
            timeout: Request timeout in seconds

        Returns:
            Parsed JSON response

        Raises:
            AccessControlError: If request fails
        """
        url = f"{self.base_url}{endpoint}"

        # Create request with API key header
        req = urllib.request.Request(url)
        req.add_header("X-API-Key", self.config.api_key)
        req.add_header("Accept", "application/json")

        try:
            logger.debug(f"Making request to {url}")

            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))

                # Check for API response success
                if not data.get("success", False):
                    error_msg = data.get("error", {}).get("message", "Unknown error")
                    raise AccessControlError(f"API error: {error_msg}")

                return data

        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise AccessControlError("Invalid API key for access control") from e
            elif e.code == 403:
                raise AccessControlError("Access denied to MCP endpoints") from e
            elif e.code == 404:
                raise AccessControlError(f"Endpoint not found: {endpoint}") from e
            else:
                raise AccessControlError(f"HTTP error {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise AccessControlError(f"Connection error: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise AccessControlError(f"Invalid JSON response: {e}") from e
        except Exception as e:
            raise AccessControlError(f"Request failed: {e}") from e

    def _get_from_cache(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        if key in self._cache:
            entry = self._cache[key]
            if not entry.is_expired(self.cache_ttl):
                logger.debug(f"Cache hit for {key}")
                return entry.data
            else:
                logger.debug(f"Cache expired for {key}")
                del self._cache[key]
        return None

    def _set_cache(self, key: str, data: Any) -> None:
        """Set value in cache."""
        self._cache[key] = CacheEntry(data=data, timestamp=datetime.now())
        logger.debug(f"Cached {key}")

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()
        logger.info("Cleared access control cache")

    def _get_json2_model_permissions(self, model: str) -> "ModelPermissions":
        """Fetch permissions for a single model via Odoo's check_access_rights.

        Works for all users — no special admin rights required.
        Calls check_access_rights for each CRUD operation and caches the result.
        """
        if self.connection is None:
            return ModelPermissions(
                model=model, enabled=True,
                can_read=True, can_write=True, can_create=True, can_unlink=True,
            )

        cache_key = f"_j2_{model}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        can_read = self.connection.check_access_rights(model, "read")
        can_write = self.connection.check_access_rights(model, "write")
        can_create = self.connection.check_access_rights(model, "create")
        can_unlink = self.connection.check_access_rights(model, "unlink")

        perms = ModelPermissions(
            model=model,
            enabled=can_read,
            can_read=can_read,
            can_write=can_write,
            can_create=can_create,
            can_unlink=can_unlink,
        )
        self._set_cache(cache_key, perms)
        logger.debug(
            "JSON/2 permissions for %s: read=%s write=%s create=%s unlink=%s",
            model, can_read, can_write, can_create, can_unlink,
        )
        return perms

    def get_enabled_models(self) -> List[Dict[str, str]]:
        """Get list of all MCP-enabled models.

        Returns:
            List of dicts with 'model' and 'name' keys

        Raises:
            AccessControlError: If request fails
        """
        # In JSON/2 mode, all models are accessible (Odoo handles ACLs)
        if self.config.api_version == "json2":
            logger.debug("JSON/2 mode: All models are accessible")
            return []

        cache_key = "enabled_models"

        # Check cache
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        # Make request
        response = self._make_request(self.MODELS_ENDPOINT)
        models = response.get("data", {}).get("models", [])

        # Cache result
        self._set_cache(cache_key, models)

        logger.info(f"Retrieved {len(models)} enabled models")
        return models

    def is_model_enabled(self, model: str) -> bool:
        """Check if a model is MCP-enabled.

        Args:
            model: The Odoo model name (e.g., 'res.partner')

        Returns:
            True if model is enabled, False otherwise
        """
        # In JSON/2 mode, delegate to get_model_permissions
        if self.config.api_version == "json2":
            return self._get_json2_model_permissions(model).enabled

        try:
            enabled_models = self.get_enabled_models()
            return any(m["model"] == model for m in enabled_models)
        except AccessControlError as e:
            logger.error(f"Failed to check if model {model} is enabled: {e}")
            return False

    def get_model_permissions(self, model: str) -> ModelPermissions:
        """Get permissions for a specific model.

        Args:
            model: The Odoo model name

        Returns:
            ModelPermissions object with permission details

        Raises:
            AccessControlError: If request fails
        """
        # In JSON/2 mode: use Odoo's check_access_rights (works for all users)
        if self.config.api_version == "json2":
            return self._get_json2_model_permissions(model)

        cache_key = f"permissions_{model}"

        # Check cache
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        # Make request
        endpoint = self.MODEL_ACCESS_ENDPOINT.format(model=model)
        response = self._make_request(endpoint)
        data = response.get("data", {})

        # Parse permissions
        permissions = ModelPermissions(
            model=data.get("model", model),
            enabled=data.get("enabled", False),
            can_read=data.get("operations", {}).get("read", False),
            can_write=data.get("operations", {}).get("write", False),
            can_create=data.get("operations", {}).get("create", False),
            can_unlink=data.get("operations", {}).get("unlink", False),
        )

        # Cache result
        self._set_cache(cache_key, permissions)

        logger.debug(f"Retrieved permissions for {model}: {permissions}")
        return permissions

    def check_operation_allowed(self, model: str, operation: str) -> Tuple[bool, Optional[str]]:
        """Check if an operation is allowed on a model.

        Args:
            model: The Odoo model name
            operation: The operation to check (read, write, create, unlink)

        Returns:
            Tuple of (allowed, error_message)
        """
        # In JSON/2 mode, check real Odoo permissions via check_access_rights
        if self.config.api_version == "json2":
            permissions = self._get_json2_model_permissions(model)
            if not permissions.can_perform(operation):
                return False, f"Operation '{operation}' not allowed on model '{model}'"
            return True, None

        try:
            # Standard mode: Get model permissions from MCP
            permissions = self.get_model_permissions(model)

            # Check if model is enabled
            if not permissions.enabled:
                return False, f"Model '{model}' is not enabled for MCP access"

            # Check specific operation
            if not permissions.can_perform(operation):
                return False, f"Operation '{operation}' not allowed on model '{model}'"

            return True, None

        except AccessControlError as e:
            logger.error(f"Access control check failed: {e}")
            return False, str(e)

    def validate_model_access(self, model: str, operation: str) -> None:
        """Validate model access, raising exception if denied.

        Args:
            model: The Odoo model name
            operation: The operation to perform

        Raises:
            AccessControlError: If access is denied
        """
        allowed, error_msg = self.check_operation_allowed(model, operation)
        if not allowed:
            raise AccessControlError(error_msg or f"Access denied to {model}.{operation}")

    def filter_enabled_models(self, models: List[str]) -> List[str]:
        """Filter list of models to only include enabled ones.

        Args:
            models: List of model names to filter

        Returns:
            List of enabled model names
        """
        # In JSON/2 mode, filter to models where user has at least read access
        if self.config.api_version == "json2":
            return [m for m in models if self._get_json2_model_permissions(m).can_read]

        try:
            enabled_models = self.get_enabled_models()
            enabled_set = {m["model"] for m in enabled_models}
            return [m for m in models if m in enabled_set]
        except AccessControlError as e:
            logger.error(f"Failed to filter models: {e}")
            return []

    def get_all_permissions(self) -> Dict[str, ModelPermissions]:
        """Get permissions for all enabled models.

        Returns:
            Dict mapping model names to their permissions
        """
        permissions = {}

        try:
            enabled_models = self.get_enabled_models()

            for model_info in enabled_models:
                model = model_info["model"]
                try:
                    permissions[model] = self.get_model_permissions(model)
                except AccessControlError as e:
                    logger.warning(f"Failed to get permissions for {model}: {e}")

        except AccessControlError as e:
            logger.error(f"Failed to get all permissions: {e}")

        return permissions
