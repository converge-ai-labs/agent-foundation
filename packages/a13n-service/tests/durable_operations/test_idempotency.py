from __future__ import annotations

import hashlib

import pytest
from a13n_service.durable_operations.idempotency import (
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
)


@pytest.mark.parametrize("key", ["!", pytest.param("~" * 512, id="max-length-key")])
def test_visible_ascii_key_policy_accepts_its_existing_boundaries(key: str) -> None:
    assert digest_visible_ascii_key(key) == hashlib.sha256(key.encode("ascii")).hexdigest()


@pytest.mark.parametrize("key", ["", " ", "a b", "\x7f", "é", pytest.param("a" * 513, id="oversized-key")])
def test_visible_ascii_key_policy_rejects_values_outside_its_existing_contract(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKey):
        digest_visible_ascii_key(key)
