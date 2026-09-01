from __future__ import annotations

import pytest
from a13n_service.trace_query import TraceQueryProviderRegistry


def test_registry_rejects_duplicates_and_unknown_keys() -> None:
    registry = TraceQueryProviderRegistry()
    provider = object()
    registry.register("langfuse", lambda: provider)  # type: ignore[arg-type,return-value]

    assert registry.keys() == ("langfuse",)
    assert registry.create("langfuse") is provider
    with pytest.raises(ValueError, match="already registered"):
        registry.register("langfuse", lambda: provider)  # type: ignore[arg-type,return-value]
    with pytest.raises(ValueError, match="not registered"):
        registry.create("tempo")


def test_registry_copy_is_isolated() -> None:
    registry = TraceQueryProviderRegistry()
    registry.register("custom", lambda: object())  # type: ignore[arg-type,return-value]

    copied = registry.copy()
    copied.register("second", lambda: object())  # type: ignore[arg-type,return-value]

    assert registry.keys() == ("custom",)
    assert copied.keys() == ("custom", "second")


@pytest.mark.parametrize("key", ["none", "1provider", "Provider", "provider-name"])
def test_registry_rejects_unselectable_or_reserved_keys(key: str) -> None:
    registry = TraceQueryProviderRegistry()

    with pytest.raises(ValueError, match="key is invalid"):
        registry.register(key, lambda: object())  # type: ignore[arg-type,return-value]
