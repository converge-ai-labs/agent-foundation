"""Deployment ceilings shared by Connectivity resources and adapters."""

from a13n_harness.providers.connector.bounds import (
    DISCOVERY_MAX_BYTES as DISCOVERY_MAX_BYTES,
)
from a13n_harness.providers.connector.bounds import (
    DISCOVERY_MAX_PAGES as DISCOVERY_MAX_PAGES,
)
from a13n_harness.providers.connector.bounds import (
    DISCOVERY_MAX_TOOLS as DISCOVERY_MAX_TOOLS,
)

PROVIDER_REQUEST_MAX_BYTES = 8 * 1024 * 1024
TOOL_NAME_MAX_BYTES = 128
TOOL_DESCRIPTION_MAX_BYTES = 16 * 1024
TOOL_SCHEMA_MAX_BYTES = 256 * 1024
JSON_MAX_DEPTH = 64
TOOL_RESULT_MAX_BYTES = 1024 * 1024
MAX_REDIRECTS = 3
