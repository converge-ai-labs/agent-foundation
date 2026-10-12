"""TLS policy for Environment-owned HTTP transports."""

import os

OUTBOUND_TLS_VERIFY_ENV = "A13N_OUTBOUND_TLS_VERIFY"


def outbound_tls_verify() -> bool:
    """Read the operator-only TLS setting when constructing an owned HTTP client.

    Unset means verified TLS. Only explicit ``false`` disables certificate chain
    and hostname verification; malformed values never silently weaken TLS.
    Caller-supplied clients, transports and CA contexts keep their own policy.
    """
    value = os.environ.get(OUTBOUND_TLS_VERIFY_ENV)
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"{OUTBOUND_TLS_VERIFY_ENV} must be true or false")
    return normalized == "true"
