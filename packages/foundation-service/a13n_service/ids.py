"""Shared Foundation object-ID allocation."""

import re
import secrets
import string

_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,7}$")
_ID_ALPHABET = string.ascii_lowercase + string.digits
_ID_RANDOM_LENGTH = 24


def new_object_id(kind: str) -> str:
    """Allocate one unpredictable Foundation object ID for an assigned kind."""

    if _KIND_PATTERN.fullmatch(kind) is None:
        raise ValueError("object ID kind must be 2-8 lowercase ASCII letters or digits")
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_RANDOM_LENGTH))
    return f"{kind}_{suffix}"
