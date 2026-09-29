"""Settings load strictly from TOML plus `A13N_<SECTION>__<FIELD>` overrides and refuse bounds that do not nest."""

import json
from pathlib import Path

import pytest
from a13n_logging import LogFormat
from a13n_service.settings import Server, Settings, load_settings


def test_environment_overrides_the_file_and_unknown_keys_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_environment: None
) -> None:
    config = tmp_path / "service.toml"
    config.write_text('[worker]\nslots = 2\n[telemetry]\nlog_format = "pretty"\n')
    monkeypatch.setenv("A13N_WORKER__SLOTS", "6")
    loaded = load_settings(config)
    assert (loaded.worker.slots, loaded.telemetry.log_format) == (6, LogFormat.pretty)
    monkeypatch.setenv("A13N_WORKER__SLOT", "6")
    with pytest.raises(ValueError, match=r"worker\.slot"):
        load_settings(config)
    monkeypatch.delenv("A13N_WORKER__SLOT")
    monkeypatch.setenv("A13N_WORKERS__SLOTS", "6")
    with pytest.raises(ValueError, match="Unknown Service setting"):
        load_settings(config)


def test_environment_variables_carry_lists_maps_and_sections_as_json(
    monkeypatch: pytest.MonkeyPatch, clean_environment: None
) -> None:
    key = "a" * 44
    for name, value in {
        "A13N_PLUGINS__KEYS": '["notes"]',
        "A13N_COMPOSER__MODELS": '["claude-sonnet-5"]',
        "A13N_ENVIRONMENTS__DOCKER_MOUNT_ROOTS": '["/srv/shared"]',
        "A13N_PROVIDERS__MCP_SERVERS": json.dumps(
            [
                {
                    "key": "docs",
                    "name": "Docs",
                    "description": "Docs search",
                    "url": "https://mcp.example.com/mcp",
                    "auth": "none",
                }
            ]
        ),
        "A13N_ENCRYPTION__ACTIVE_KEY_ID": "k",
        "A13N_ENCRYPTION__KEYS": f'{{"k": "{key}"}}',
        "A13N_AUTH__MAIL": '{"smtp_host": "127.0.0.1", "sender": "a13n@example.com"}',
        "A13N_MEMORY__DEFAULT_GUIDE": '{"file": "Keep one topic per file."}',
    }.items():
        monkeypatch.setenv(name, value)
    loaded = load_settings()
    assert (loaded.plugins.keys, loaded.composer.models) == (("notes",), ("claude-sonnet-5",))
    assert [str(root) for root in loaded.environments.docker_mount_roots] == ["/srv/shared"]
    assert [server.key for server in loaded.providers.mcp_servers] == ["docs"]
    assert loaded.encryption.keys["k"].get_secret_value() == key
    assert loaded.auth.mail.smtp_host == "127.0.0.1"
    assert loaded.memory.default_guide.file == "Keep one topic per file."


def test_a_distribution_section_cannot_shadow_a_core_section() -> None:
    with pytest.raises(ValueError, match="shadow core sections"):
        load_settings(extensions={"server": Server})


@pytest.mark.parametrize(
    ("values", "refused"),
    [
        ({"worker": {"scan_seconds": 2}}, "worker.scan_seconds"),
        ({"worker": {"lease_seconds": 3}}, "worker.authority_seconds"),
        ({"worker": {"lease_seconds": 12}}, "objects.timeout"),
        ({"outbox": {"by_kind": {"webhook": {"lease_seconds": 20}}}}, "control.webhook_timeout"),
        ({"worker": {"drain_seconds": 20}}, "worker.drain_seconds"),
        ({"objects": {"upload_bytes": 4194304}}, "objects.upload_bytes"),
        ({"control": {"inbox_bytes": 1048576}}, "worker.output_bytes"),
        ({"environments": {"scan_seconds": 30, "renewal_seconds": 60}}, r"environments.scan_seconds \+ 2"),
        ({"memory": {"always_load_bytes": 40000}}, "memory.always_load_bytes"),
        ({"memory": {"max_file_bytes": 2048, "frontmatter_bytes": 2048}}, "memory.frontmatter_bytes"),
        ({"memory": {"max_file_bytes": 1048576, "max_total_bytes": 65536}}, "memory.max_file_bytes"),
        ({"memory": {"default_guide": {"file": "x" * 4097}}}, "memory.default_guide.file"),
        ({"memory": {"default_guide": {"record": "x" * 4097}}}, "memory.default_guide.record"),
        ({"memory": {"record_chars": 8001}}, "record_chars"),
        ({"providers": {"operation_seconds": 30}}, "providers.operation_seconds"),
        ({"memory": {"mounts_per_thread": 33}}, "mounts_per_thread"),
        ({"telemetry": {"log_stdout": False}}, "telemetry.log_file"),
        ({"telemetry": {"log_file": "service.log", "log_file_backups": 0}}, "log_file_backups"),
        ({"server": {"port": 9464}, "telemetry": {"metrics_port": 9464}}, "telemetry.metrics_port"),
    ],
)
def test_bounds_must_nest(values: dict, refused: str) -> None:
    with pytest.raises(ValueError, match=refused):
        Settings.model_validate(values)


@pytest.mark.parametrize("style", ["auto", "virtual"])
def test_conflicting_object_addressing_fails(style: str) -> None:
    with pytest.raises(ValueError, match=r"objects\.path_style=true conflicts"):
        Settings.model_validate({"objects": {"path_style": True, "addressing_style": style}})


def test_object_addressing_environment_overrides_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_environment: None
) -> None:
    config = tmp_path / "service.toml"
    config.write_text('[objects]\naddressing_style = "auto"\n')
    monkeypatch.setenv("A13N_OBJECTS__ADDRESSING_STYLE", "virtual")
    assert load_settings(config).objects.effective_addressing_style == "virtual"
    monkeypatch.setenv("A13N_OBJECTS__ADDRESSING_STYLE", "unsupported")
    with pytest.raises(ValueError, match="addressing_style"):
        load_settings(config)


def test_a_key_file_replaces_the_key_ring() -> None:
    with pytest.raises(ValueError, match=r"encryption\.key_file excludes"):
        Settings.model_validate({"encryption": {"key_file": "/app/var/encryption.key", "active_key_id": "k"}})


def test_outbox_defaults_and_partial_kind_overrides(tmp_path: Path, monkeypatch, clean_environment: None) -> None:  # type: ignore[no-untyped-def]
    config = tmp_path / "outbox.toml"
    config.write_text(
        "[outbox.defaults]\nmax_attempts = 4\nparallel = 3\n[ outbox.by_kind.webhook ]\ndelivered_retention_seconds = 604800\n"
    )
    monkeypatch.setenv("A13N_OUTBOX__BY_KIND", '{"checkpoint_cleanup": {"parallel": 2, "max_attempts": 12}}')
    loaded = load_settings(config).outbox
    # The environment replaces this map; an explicit built-in default still overrides the shared default.
    assert loaded.policies["checkpoint_cleanup"].max_attempts == 12
    assert loaded.policies["checkpoint_cleanup"].parallel == 2
    assert loaded.policies["webhook"].parallel == 3
    assert loaded.policies["webhook"].delivered_retention_seconds == 86400
    assert loaded.policies["email"].max_attempts == 4
    assert loaded.policies is loaded.policies


@pytest.mark.parametrize(
    "outbox",
    [
        {"by_kind": {"typo": {}}},
        {"defaults": {"max_attempt": 1}},
        {"by_kind": {"email": {"parallel": 33}}},
        {"defaults": {"batch": 2}, "by_kind": {"email": {"parallel": 2}}},
        {"purge_interval_seconds": 5},
    ],
)
def test_outbox_invalid_policy_fails_at_startup(outbox: dict) -> None:
    with pytest.raises(ValueError):
        Settings.model_validate({"outbox": outbox})


def test_object_write_mode_file_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_environment: None
) -> None:
    config = tmp_path / "service.toml"
    config.write_text('[objects]\nbackend = "s3"\nbucket = "agents"\nwrite_mode = "oss"\n')
    assert load_settings(config).objects.write_mode == "oss"
    monkeypatch.setenv("A13N_OBJECTS__WRITE_MODE", "s3")
    assert load_settings(config).objects.write_mode == "s3"
    monkeypatch.setenv("A13N_OBJECTS__WRITE_MODE", "invalid")
    with pytest.raises(ValueError, match="write_mode"):
        load_settings(config)
    with pytest.raises(ValueError, match="requires the s3 backend"):
        Settings.model_validate({"objects": {"write_mode": "oss"}})
