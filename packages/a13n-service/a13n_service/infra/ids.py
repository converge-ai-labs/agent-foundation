"""Shared Service identifiers: allocated object IDs and the keys that name models."""

import re
import secrets
from typing import Annotated

from pydantic import StringConstraints

_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,7}$")
# Tiers and their lifetime volume budgets are owned by spec/data-conventions.md#service-id-allocation.
_RANDOM_BYTES = {
    **dict.fromkeys(
        (
            "ap",
            "conn",
            "cprov",
            "envtpl",
            "eprov",
            "mdl",
            "mem",
            "memprov",
            "mprov",
            "org",
            "sa",
            "sk",
            "usr",
            "wprov",
            "ws",
        ),
        10,
    ),
    **dict.fromkeys(("apr", "ast", "env", "inv", "mfile", "rb", "sess", "skr"), 12),
    **dict.fromkeys(("inb", "rat", "run"), 14),
}
# Every other kind allocates 32 hex characters (128 random bits).
_DEFAULT_BYTES = 16

_MIN_SUFFIX, _MAX_SUFFIX = 2 * min(_RANDOM_BYTES.values()), 2 * _DEFAULT_BYTES
OBJECT_ID_PATTERN = rf"^[a-z][a-z0-9]{{1,7}}_[0-9a-f]{{{_MIN_SUFFIX},{_MAX_SUFFIX}}}$"
ObjectId = Annotated[str, StringConstraints(pattern=OBJECT_ID_PATTERN, max_length=8 + 1 + _MAX_SUFFIX)]
# The immutable, workspace-unique key that alone identifies a model; object IDs contain `_`.
KEY_MAX_LENGTH = 128
KEY_PATTERN = rf"^[a-z0-9][a-z0-9.-]{{0,{KEY_MAX_LENGTH - 1}}}$"
Key = Annotated[str, StringConstraints(pattern=KEY_PATTERN)]


def new_object_id(kind: str) -> str:
    """Allocate one unpredictable Service object ID for a kind prefix."""
    if _KIND_PATTERN.fullmatch(kind) is None:
        raise ValueError("object ID kind must be 2-8 lowercase ASCII letters or digits")
    return f"{kind}_{secrets.token_hex(_RANDOM_BYTES.get(kind, _DEFAULT_BYTES))}"
