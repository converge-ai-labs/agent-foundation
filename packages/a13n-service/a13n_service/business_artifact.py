"""A fixed Distribution ships one matching selection and invocation catalogue."""

from collections.abc import Sequence

from a13n_service.business_tools import Definition, validate_catalog
from a13n_service.infra.platform import PLATFORM_TOOLS


def validate_artifact_catalog(definitions: Sequence[Definition]) -> None:
    """Reject unbundled or stale contributions before the application serves requests.

    The artifact's generated metadata is also consumed by configuration validation,
    permission identities and Host selection. No runtime registration changes those
    consumers; extensions regenerate and ship the catalogue with their Distribution.
    """
    validate_catalog(definitions)
    bundled = {tool.key: (tool.group, tool.effect, tool.summary) for tool in PLATFORM_TOOLS}
    contributed = {tool.name: (tool.group, tool.effect, tool.description) for tool in definitions}
    missing = sorted(bundled.keys() - contributed.keys())
    unbundled = sorted(contributed.keys() - bundled.keys())
    changed = sorted(name for name in bundled.keys() & contributed.keys() if bundled[name] != contributed[name])
    if missing or unbundled or changed:
        raise ValueError(
            "Business artifact catalog does not match Distribution: "
            f"missing definitions={missing}; unbundled definitions={unbundled}; changed metadata={changed}. "
            "Regenerate and bundle the business catalog for this fixed Distribution."
        )
