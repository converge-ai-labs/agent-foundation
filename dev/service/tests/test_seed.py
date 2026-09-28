"""Seeding and private resources against a real disposable instance: stores, migration, Service and model.

It drives the same functions `make dev-reset STATE=seeded` uses, under a temporary checkout with its own Compose
project, so it never touches a developer's instance. Requires Docker.
"""

import json
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from dev.service import dev_resources, lifecycle, stores
from dev.service.api import Api
from dev.service.applications import applications, create_administrator, migrate
from dev.service.checkout import ADMIN_EMAIL, ADMIN_PASSWORD, Checkout
from dev.service.seed import seed, write_report
from dev.service.seed_verify import verify

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.timeout(300)


@pytest.fixture
def instance(checkout_root: Path) -> Iterator[Checkout]:
    checkout = Checkout.resolve(checkout_root)
    checkout.write_settings({"trace_backend": "none"})
    try:
        stores.start(checkout.instance)
        migrate(checkout)
        assert create_administrator(checkout) is True
        assert create_administrator(checkout) is False
        yield checkout
    finally:
        stores.delete(checkout.instance)


def running(checkout: Checkout) -> AbstractContextManager[None]:
    # The application commands import repository code, so they run from the repository root.
    return lifecycle.running(ROOT, applications(checkout, console=False), checkout.logs)


def test_seeded_state_verifies_after_a_restart_and_private_resources_apply_once(
    instance: Checkout, tmp_path: Path
) -> None:
    with running(instance), Api(instance.service_url) as api:
        api.login(ADMIN_EMAIL, ADMIN_PASSWORD)
        seeded = seed(api, instance.model_url, instance.environments)
        checks = verify(api, seeded)
        assert [name for name, passed in checks if not passed] == []
        write_report(instance.seed_report, instance.console_url, seeded, checks)
    # `make dev` starts the applications again, so what the fixtures hold must outlive their processes.
    with running(instance), Api(instance.service_url) as api:
        api.login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert [name for name, passed in verify(api, seeded) if not passed] == []

        resources = tmp_path / "dev-resources.toml"
        resources.write_text(f"""version = 1
[[model_providers]]
type = "openai"
name = "Private scripted"
configuration = {{ base_url = "{instance.model_url}" }}
credential = {{ api_key = "sk-private-one" }}

[[model_providers.models]]
key = "private-scripted"
name = "Private scripted"
upstream_model = "local-scripted"

[[web_providers]]
type = "brave"
name = "Brave (blank)"
credential = {{ api_key = "" }}
""")
        resources.chmod(0o600)

        assert dev_resources.apply_to(instance, resources) == "1 model providers, 1 models"
        providers = api.items("/api/v1/model-providers")
        provider = next(item for item in providers if item["name"] == "Private scripted")
        assert provider["credential_configured"] and provider["workspace_id"] == seeded.workspace
        assert api.get("/api/v1/models/private-scripted")["provider_id"] == provider["id"]
        assert "Brave (blank)" not in {item["name"] for item in api.items("/api/v1/web-providers")}
        digests = (instance.state / "dev-resources.json").read_text()
        assert "sk-private-one" not in digests

        dev_resources.apply_to(instance, resources)
        path = f"/api/v1/model-providers/{provider['id']}"
        assert api.get(path)["version"] == provider["version"]

        resources.write_text(resources.read_text().replace("sk-private-one", "sk-private-two"))
        dev_resources.apply_to(instance, resources)
        assert api.get(path)["version"] > provider["version"]
        assert json.loads((instance.state / "dev-resources.json").read_text()) != json.loads(digests)
