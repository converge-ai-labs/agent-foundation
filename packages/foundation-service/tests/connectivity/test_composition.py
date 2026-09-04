import pytest
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry


def test_registry_rejects_unknown_duplicate_and_unversioned_adapters() -> None:
    registry = AdapterRegistry[object]()
    definition = AdapterDefinition(
        key="slack",
        config_versions=frozenset({"slack_http_v1"}),
        factory=object,
    )
    registry.register(definition)

    assert registry.keys() == ("slack",)
    assert registry.create("slack", config_version="slack_http_v1") is not None
    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition)
    with pytest.raises(ValueError, match="not registered"):
        registry.create("github", config_version="github_app_http_v1")
    with pytest.raises(ValueError, match="version"):
        registry.create("slack", config_version="slack_http_v2")
    with pytest.raises(ValueError, match="stable configuration versions"):
        registry.register(
            AdapterDefinition(
                key="github",
                config_versions=frozenset(),
                factory=object,
            )
        )
