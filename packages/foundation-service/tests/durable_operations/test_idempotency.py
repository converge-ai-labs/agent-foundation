from __future__ import annotations

import hashlib

import pytest
from a13n_service.durable_operations.idempotency import (
    InvalidIdempotencyKey,
    digest_utf8_key,
    digest_visible_ascii_key,
)


@pytest.mark.parametrize("key", ["!", "~" * 512])
def test_visible_ascii_key_policy_accepts_its_existing_boundaries(key: str) -> None:
    assert digest_visible_ascii_key(key) == hashlib.sha256(key.encode("ascii")).hexdigest()


@pytest.mark.parametrize("key", ["", " ", "a b", "\x7f", "é", "a" * 513])
def test_visible_ascii_key_policy_rejects_values_outside_its_existing_contract(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKey):
        digest_visible_ascii_key(key)


@pytest.mark.parametrize("key", ["!", "é" * 256, "重试-🔁"])
def test_plugin_utf8_key_policy_accepts_its_existing_boundaries(key: str) -> None:
    assert digest_utf8_key(key) == hashlib.sha256(key.encode()).hexdigest()


@pytest.mark.parametrize("key", ["", "é" * 257, "\ud800"])
def test_plugin_utf8_key_policy_rejects_values_outside_its_existing_contract(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKey):
        digest_utf8_key(key)
