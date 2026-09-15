"""Shared Service object-ID allocation."""

import re
import secrets
from typing import Annotated

from pydantic import StringConstraints

# Existing lowercase alphanumeric IDs remain valid; allocation is narrower than acceptance.
ObjectId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,7}_[a-z0-9]{16,64}$", max_length=72)]

_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,7}$")
# Allocation tiers and their lifetime volume budgets are owned by
# spec/data-conventions.md#service-id-allocation.
_ID_RANDOM_BYTES = {
    **dict.fromkeys(
        (
            "acct",
            "ap",
            "cconn",
            "cnr",
            "envp",
            "envtpl",
            "hsub",
            "mcpc",
            "mdl",
            "mprov",
            "memprov",
            "org",
            "sa",
            "sk",
            "usr",
            "wprov",
            "ws",
        ),
        10,
    ),
    **dict.fromkeys(
        (
            "a2actx",
            "aguitb",
            "apr",
            "ast",
            "bind",
            "env",
            "envrev",
            "hsubr",
            "img",
            "inv",
            "rb",
            "sess",
            "session",
            "skr",
            "sku",
            "tgt",
        ),
        12,
    ),
    **dict.fromkeys(
        ("a2amsg", "a2apush", "a2atask", "aguirb", "crr", "envop", "ibat", "inb", "qsub", "rat", "run"),
        14,
    ),
}


def new_object_id(kind: str) -> str:
    """Allocate one unpredictable Service object ID for an assigned kind."""

    if _KIND_PATTERN.fullmatch(kind) is None:
        raise ValueError("object ID kind must be 2-8 lowercase ASCII letters or digits")
    suffix = secrets.token_hex(_ID_RANDOM_BYTES.get(kind, 16))
    return f"{kind}_{suffix}"
