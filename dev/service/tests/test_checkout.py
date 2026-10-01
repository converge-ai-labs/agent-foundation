"""The resolver writes an ordinary Service settings file that isolates the checkout."""

import stat
import tomllib
from pathlib import Path

from a13n_service.settings import Settings

from dev.service.checkout import Checkout

LANGFUSE = {
    "trace_backend": "langfuse",
    "trace_url": "http://127.0.0.1:3000",
    "langfuse_public_key": "lf_pk_local",
    "langfuse_secret_key": "lf_sk_local",
}


def written(checkout: Checkout, telemetry: dict[str, object]) -> Settings:
    return Settings.model_validate(tomllib.loads(checkout.write_settings(telemetry).read_text()))


def test_settings_describe_only_this_checkout(checkout_root: Path) -> None:
    checkout = Checkout.resolve(checkout_root)
    ports = checkout.instance.ports
    settings = written(checkout, LANGFUSE)

    assert (settings.server.host, settings.server.port) == ("127.0.0.1", ports.service)
    # Browsers change state only from the Console origin, which proxies the API.
    assert settings.server.public_origin == f"http://{checkout.id}.localhost:{ports.console}"
    assert settings.database.url.get_secret_value().endswith(f"@127.0.0.1:{ports.postgres}/a13n_service_dev")
    assert settings.redis.url.get_secret_value() == f"redis://127.0.0.1:{ports.redis}/0"
    assert settings.objects.root == checkout_root / "var/dev/objects"
    assert settings.providers.require_https is False
    assert settings.provisioning.local.enabled is True
    assert settings.provisioning.local.root == checkout.environments
    assert settings.telemetry.trace_config() is not None
    assert stat.S_IMODE(checkout.settings_file.stat().st_mode) == 0o600


def test_the_encryption_key_survives_rewrites(checkout_root: Path) -> None:
    checkout = Checkout.resolve(checkout_root)
    first = written(checkout, {"trace_backend": "none"}).encryption
    second = written(checkout, {"trace_backend": "none"}).encryption
    assert first.active_key_id == "local" and first.keys["local"] == second.keys["local"]
    assert stat.S_IMODE((checkout.state / "encryption.key").stat().st_mode) == 0o600


def test_checkouts_get_separate_cookie_hosts(tmp_path: Path) -> None:
    urls = set()
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
        urls.add(Checkout.resolve(tmp_path / name).console_url.split(":")[1])
    assert len(urls) == 2
