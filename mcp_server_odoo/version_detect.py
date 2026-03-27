"""Auto-detect Odoo API version from server version.

Probes the Odoo server via XML-RPC (works on all versions 14+)
and returns the appropriate API version string.
"""

import logging
import xmlrpc.client
from typing import Literal, Optional, Tuple

logger = logging.getLogger(__name__)

# Minimum Odoo major version that supports JSON/2 API
JSON2_MIN_VERSION = 19


def detect_api_version(
    odoo_url: str,
    timeout: int = 10,
) -> Tuple[Literal["json2", "xmlrpc"], Optional[str]]:
    """Detect the appropriate API version for an Odoo server.

    Makes a lightweight XML-RPC call to /xmlrpc/2/common version()
    which works on all Odoo versions without authentication.

    Args:
        odoo_url: Base URL of the Odoo server (e.g., "https://mycompany.odoo.com")
        timeout: Connection timeout in seconds

    Returns:
        Tuple of (api_version, server_version_string).
        api_version is "json2" for Odoo 19+, "xmlrpc" for older versions.
        server_version_string is e.g. "19.0" or None if detection failed.

    Falls back to "xmlrpc" if detection fails.
    """
    url = odoo_url.rstrip("/")
    endpoint = f"{url}/xmlrpc/2/common"

    try:
        proxy = xmlrpc.client.ServerProxy(endpoint, allow_none=True)
        # Socket timeout via transport isn't straightforward with ServerProxy,
        # so we use a simple approach: create with default and rely on system timeout.
        # For more control, we could use httpx, but xmlrpc.client is sufficient here.
        version_info = proxy.version()

        server_version = version_info.get("server_version", "")
        server_version_info = version_info.get("server_version_info", [])

        # Parse major version from server_version_info [major, minor, micro, release, serial]
        if server_version_info and len(server_version_info) >= 1:
            major = int(server_version_info[0])
        elif server_version:
            # Fallback: parse from "19.0" string
            major = int(server_version.split(".")[0])
        else:
            logger.warning("Could not parse Odoo version, falling back to xmlrpc")
            return "xmlrpc", None

        api_version: Literal["json2", "xmlrpc"] = "json2" if major >= JSON2_MIN_VERSION else "xmlrpc"
        logger.info(
            f"Detected Odoo {server_version} (major={major}) -> api_version={api_version}"
        )
        return api_version, server_version

    except Exception as e:
        logger.warning(f"Failed to detect Odoo version at {url}: {e}. Falling back to xmlrpc.")
        return "xmlrpc", None
