"""Settings load strictly from TOML plus `A13N_<SECTION>__<FIELD>` overrides and refuse bounds that do not nest."""

import json
from pathlib import Path

import pytest
from a13n_logging import LogFormat
from a13n_service.settings import Server, Settings, load_settings


def test_environment_overrides_the_file_and_unknown_keys_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_environment_variables_carry_lists_maps_and_sections_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    key = "a" * 44
    for name, value in {
        "A13N_PLUGINS__KEYS": '["notes"]',
        "A13N_ASSISTANT__MODELS": '["claude-sonnet-5"]',
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
    assert (loaded.plugins.keys, loaded.assistant.models) == (("notes",), ("claude-sonnet-5",))
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
        ({"control": {"outbox_lease_seconds": 20}}, "control.webhook_timeout"),
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
    ],
)
def test_bounds_must_nest(values: dict, refused: str) -> None:
    with pytest.raises(ValueError, match=refused):
        Settings.model_validate(values)


def test_defaults_nest() -> None:
    Settings()
