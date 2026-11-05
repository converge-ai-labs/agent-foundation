"""Explicit Kubernetes inputs and native resource verification for Service journeys."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from a13n_harness.providers.environment.kubernetes.configuration import (
    ClusterProfile,
    KubernetesConnectionConfiguration,
)
from a13n_harness.providers.environment.kubernetes.runtime import KubernetesRuntime, open_runtime


def profile() -> ClusterProfile | None:
    path = os.environ.get("E2E_KUBERNETES_PROFILE")
    return None if path is None else ClusterProfile.model_validate_json(Path(path).read_text())


@asynccontextmanager
async def runtime() -> AsyncIterator[KubernetesRuntime]:
    selected = profile()
    assert selected is not None, "E2E_KUBERNETES_PROFILE is required"
    connection = KubernetesConnectionConfiguration(cluster="e2e", namespace=selected.namespaces[0])
    connection._cluster = selected
    native = await open_runtime(configuration=connection, credential=None)
    try:
        yield native
    finally:
        await native.close()
