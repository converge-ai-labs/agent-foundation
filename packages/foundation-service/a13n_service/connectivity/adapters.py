"""Stable adapter identities used by Connectivity composition."""

from typing import Protocol


class IngressAdapter(Protocol):
    provider_key: str
    config_versions: frozenset[int]


class ConnectorAdapter(Protocol):
    driver_key: str
    config_versions: frozenset[int]
