"""Developer inventory is metadata-only and cleanup targets explicitly reviewed UIDs."""

import argparse
import json
import tomllib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from a13n_harness.providers.environment.kubernetes.configuration import (
    LABEL_PREFIX,
    ClusterProfile,
    KubernetesConnectionConfiguration,
    Resource,
)
from a13n_harness.providers.environment.kubernetes.runtime import KubernetesRuntime
from a13n_service.settings import Settings

from dev.service import kubernetes
from dev.service.checkout import Checkout


@pytest.fixture
def anyio_backend():
    return "asyncio"


pytestmark = pytest.mark.anyio


def runtime() -> KubernetesRuntime:
    connection = KubernetesConnectionConfiguration(cluster="local", namespace="sandboxes")
    connection._cluster = ClusterProfile(namespaces=("sandboxes",), ownership_id="dev-one")
    return KubernetesRuntime(connection, AsyncMock())


def item(name: str = "test", uid: str = "uid", owner: str = "dev-one") -> dict:
    return {
        "metadata": {
            "name": name,
            "uid": uid,
            "labels": {LABEL_PREFIX + "owner": owner, LABEL_PREFIX + "instance": "pod"},
        },
        "data": {"token": "secret"},
    }


async def test_inventory_paginates_and_exports_no_secret_bodies() -> None:
    native = runtime()
    native.api.call_api.side_effect = [
        {"items": [item()], "metadata": {"continue": "next"}},
        {"items": [item(owner="other")], "metadata": {}},
        *[{"items": [], "metadata": {}} for _ in range(3)],
    ]
    records = await kubernetes.inventory(native)
    assert len(records) == 1 and records[0]["uid"] == "uid"
    assert "secret" not in json.dumps(records)
    assert ("continue", "next") in native.api.call_api.call_args_list[1].kwargs["query_params"]
    assert all(
        ("labelSelector", LABEL_PREFIX + "owner=dev-one") in call.kwargs["query_params"]
        for call in native.api.call_api.call_args_list
    )


async def test_cleanup_preview_and_exact_uid_delete() -> None:
    native = runtime()
    native.api.call_api.return_value = item()
    selected = [Resource(kind="Pod", name="test", uid="uid")]
    assert (await kubernetes.cleanup(native, selected, delete_storage=False, confirmed=False))[0]["result"] == "preview"
    assert all(call.args[1] == "GET" for call in native.api.call_api.call_args_list)
    result = await kubernetes.cleanup(native, selected, delete_storage=False, confirmed=True)
    assert result[0]["result"] == "requested"
    assert native.api.call_api.call_args.kwargs["body"]["preconditions"] == {"uid": "uid"}


@pytest.mark.parametrize("changed", [item(uid="replacement"), item(owner="other")])
async def test_cleanup_refuses_changed_identity_before_any_deletion(changed: dict) -> None:
    native = runtime()
    native.api.call_api.side_effect = [item(), changed]
    with pytest.raises(ValueError, match="Ownership or UID"):
        await kubernetes.cleanup(
            native,
            [Resource(kind="Pod", name=name, uid="uid") for name in ("test", "second")],
            delete_storage=False,
            confirmed=True,
        )
    assert all(call.args[1] == "GET" for call in native.api.call_api.call_args_list)


async def test_cleanup_requires_separate_storage_consent_and_never_deletes_borrowed_configmaps() -> None:
    native = runtime()
    with pytest.raises(ValueError, match="--delete-storage"):
        await kubernetes.cleanup(
            native,
            [Resource(kind="PersistentVolumeClaim", name="data", uid="uid")],
            delete_storage=False,
            confirmed=True,
        )
    with pytest.raises(ValueError, match="owned Kind"):
        await kubernetes.cleanup(
            native, [Resource(kind="ConfigMap", name="settings", uid="uid")], delete_storage=True, confirmed=True
        )
    native.api.call_api.assert_not_called()


def test_configured_profile_survives_settings_rewrites(checkout_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = Checkout.resolve(checkout_root)
    monkeypatch.setattr(Checkout, "resolve", lambda: checkout)
    kubernetes.configure(
        argparse.Namespace(
            kubeconfig=checkout_root / "kubeconfig",
            context="kind-test",
            namespace="sandboxes",
            service_account="sandbox",
        )
    )
    for _ in range(2):
        settings = Settings.model_validate(
            tomllib.loads(checkout.write_settings({"trace_backend": "none"}).read_text())
        )
        profile = settings.environments.kubernetes_clusters["local"]
        assert profile.ownership_id == f"dev-{checkout.id}"
        assert profile.context == "kind-test" and profile.allow_port_forward
        assert profile.sandbox_service_account == "sandbox"
