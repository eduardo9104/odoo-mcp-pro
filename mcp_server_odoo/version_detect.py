"""Auto-detect Odoo API version from server version.

Probes the Odoo server via XML-RPC (works on all versions 14+)
and returns the appropriate API version string.
"""

import logging
import re
import xmlrpc.client
from typing import Literal, Optional, Tuple

logger = logging.getLogger(__name__)

# Minimum Odoo major version that supports JSON/2 API
JSON2_MIN_VERSION = 19

# Regex to extract the major version number from strings like "saas~19", "19", "19.0"
_MAJOR_VERSION_RE = re.compile(r"(\d+)")


def _parse_major(value) -> int:
    """Extract major version number from version identifier.

    Handles: 19 (int), "19", "19.0", "saas~19", "saas~19.2+e"
    """
    if isinstance(value, int):
        return value
    match = _MAJOR_VERSION_RE.search(str(value))
    if match:
        return int(match.group(1))
    raise ValueError(f"Cannot parse major version from {value!r}")


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
        # Odoo.sh SaaS versions use strings like "saas~19" instead of int 19
        if server_version_info and len(server_version_info) >= 1:
            major = _parse_major(server_version_info[0])
        elif server_version:
            major = _parse_major(server_version.split(".")[0])
        else:
            logger.warning("Could not parse Odoo version, falling back to xmlrpc")
            return "xmlrpc", None

        api_version: Literal["json2", "xmlrpc"] = (
            "json2" if major >= JSON2_MIN_VERSION else "xmlrpc"
        )
        logger.info(f"Detected Odoo {server_version} (major={major}) -> api_version={api_version}")
        return api_version, server_version

    except Exception as e:
        logger.warning(f"Failed to detect Odoo version at {url}: {e}. Falling back to xmlrpc.")
        return "xmlrpc", None
