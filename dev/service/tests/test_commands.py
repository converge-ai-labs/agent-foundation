"""Development preparation is repeatable and never starts application listeners."""

import socket
import sys
from types import SimpleNamespace

import pytest
from a13n_service.configuration.sources import load_settings

from dev.service import __main__ as commands
from dev.service.environment import LOCAL_CONFIG, Environment
from dev.service.langfuse import Langfuse


def local_environment(tmp_path, **overrides):
    return Environment(
        load_settings(
            LOCAL_CONFIG,
            environ={},
            overrides={
                "objects": {"local_root": tmp_path / "var/service/objects"},
                "filesystem": {"root": tmp_path / "var/service/files"},
                **overrides,
            },
        ),
        tmp_path,
    )


def test_setup_is_repeatable_preserves_state_and_never_resets(tmp_path, monkeypatch, capsys):
    environment = local_environment(tmp_path)
    environment.state.mkdir(parents=True)
    retained = environment.state / "retained.txt"
    retained.write_text("preserve data")
    events = []
    monkeypatch.setattr(commands, "ensure_docker", lambda: events.append("docker"))
    monkeypatch.setattr(Environment, "compose", lambda self, *args: events.append(args))
    monkeypatch.setattr(Langfuse, "start", lambda self: events.append("langfuse"))
    monkeypatch.setattr(
        commands, "DatabaseMigrator", lambda *args: SimpleNamespace(upgrade=lambda: events.append("migrate"))
    )
    monkeypatch.setattr(commands, "reset", lambda *args: pytest.fail("setup must never reset or seed"))
    for _ in range(2):
        commands.setup(environment, Langfuse(environment), LOCAL_CONFIG)
    assert events == ["docker", ("up", "-d", "--wait"), "langfuse", "migrate"] * 2
    assert retained.read_text() == "preserve data"
    assert "Service and Console have not been started" in capsys.readouterr().out


def test_setup_refuses_incomplete_reset_before_touching_infrastructure(tmp_path, monkeypatch):
    environment = local_environment(tmp_path)
    environment.incomplete.parent.mkdir(parents=True)
    environment.incomplete.write_text("seeded\n")
    monkeypatch.setattr(commands, "ensure_docker", lambda: pytest.fail("must not start Docker"))
    monkeypatch.setattr(Environment, "compose", lambda *args: pytest.fail("must not start stores"))
    with pytest.raises(ValueError, match="reset did not complete"):
        commands.setup(environment, Langfuse(environment), LOCAL_CONFIG)
    assert environment.incomplete.read_text() == "seeded\n"


def test_port_check_reports_owner_without_terminating_it(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        environment = local_environment(tmp_path, service={"port": port})
        with pytest.raises(ValueError, match=f"Service port 127.0.0.1:{port} is in use"):
            commands.check_ports(environment)
        assert listener.getsockname()[1] == port


@pytest.mark.parametrize(
    "overrides, console",
    [
        ({"service": {"host": "0.0.0.0"}}, False),
        ({"service": {"port": 18080}}, False),
        ({"iam": {"public_origin": "https://127.0.0.1:5173"}}, True),
        ({"iam": {"public_origin": "http://127.0.0.1:8000"}}, True),
    ],
)
def test_invalid_listener_configuration_fails_early(tmp_path, overrides, console):
    with pytest.raises(ValueError):
        commands.check_ports(local_environment(tmp_path, **overrides), console=console)


def test_console_uses_selected_service_and_origin_not_ambient_upstream(tmp_path, monkeypatch):
    environment = local_environment(
        tmp_path,
        service={"port": 8100},
        iam={"public_origin": "http://127.0.0.1:5273"},
    )
    monkeypatch.setattr(commands, "load_settings", lambda path: environment.settings)
    monkeypatch.setattr(commands, "Environment", lambda settings: environment)
    monkeypatch.setattr(sys, "argv", ["dev.service", "console"])
    monkeypatch.setenv("A13N_CONSOLE_SERVICE_URL", "https://remote.invalid")
    calls = []

    def execvpe(executable, argv, environ):
        calls.append((executable, argv, environ))

    monkeypatch.setattr(commands.os, "execvpe", execvpe)
    commands.main()
    executable, argv, environ = calls[0]
    assert executable == "pnpm"
    assert argv[-2:] == ["--port", "5273"]
    assert environ["A13N_CONSOLE_SERVICE_URL"] == "http://127.0.0.1:8100"


def test_stop_includes_existing_langfuse_after_opt_out(tmp_path, monkeypatch):
    environment = local_environment(tmp_path, observability={"tracing": False, "query": {"provider": "none"}})
    monkeypatch.setattr(commands, "load_settings", lambda path: environment.settings)
    monkeypatch.setattr(commands, "Environment", lambda settings: environment)
    monkeypatch.setattr(sys, "argv", ["dev.service", "stop"])
    events = []
    monkeypatch.setattr(Environment, "require_stopped", lambda self: events.append("require-stopped"))
    monkeypatch.setattr(Environment, "compose", lambda self, *args: events.append(("service", args)))
    monkeypatch.setattr(Langfuse, "compose", lambda self, *args: events.append(("langfuse", args)))
    commands.main()
    assert events == ["require-stopped", ("service", ("stop",)), ("langfuse", ("stop",))]


def test_port_check_allows_immediate_restart_after_closed_connection(tmp_path, monkeypatch):
    with socket.socket() as server, socket.socket() as client:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        server.listen()
        client.connect(("127.0.0.1", port))
        accepted, _ = server.accept()
        accepted.close()
        assert client.recv(1) == b""

    # Only the Service socket's TIME_WAIT behavior is under test. Give the
    # scripted-model probe an owned ephemeral port instead of requiring the
    # developer's fixed model port to be idle.
    class OwnedModelPortSocket(socket.socket):
        def bind(self, address):
            super().bind(("127.0.0.1", 0) if address == ("127.0.0.1", 18080) else address)

    monkeypatch.setattr(commands.socket, "socket", OwnedModelPortSocket)
    environment = local_environment(tmp_path, service={"port": port})
    commands.check_ports(environment)


@pytest.mark.parametrize("action", ["setup", "reset"])
def test_logfire_local_commands_skip_langfuse_and_keep_selected_export(tmp_path, monkeypatch, action):
    environment = local_environment(
        tmp_path,
        observability={
            "query": {
                "provider": "logfire",
                "logfire_base_url": "https://logfire-us.pydantic.dev",
                "logfire_read_token": "test-read-token",
                "logfire_history_from": "2026-09-14T00:00:00Z",
            }
        },
    )
    monkeypatch.setattr(commands, "load_settings", lambda path: environment.settings)
    monkeypatch.setattr(commands, "Environment", lambda settings: environment)
    monkeypatch.setattr(sys, "argv", ["dev.service", action, *(["seeded"] if action == "reset" else [])])
    monkeypatch.setenv("LOGFIRE_TOKEN", "test-write-token")
    monkeypatch.delenv("LOGFIRE_BASE_URL", raising=False)
    monkeypatch.delenv("A13N_DEV_TRACE_BACKEND", raising=False)
    monkeypatch.setattr(Langfuse, "compose", lambda *args: pytest.fail("must not operate Langfuse"))
    monkeypatch.setattr(Langfuse, "warn_about_legacy_stack", lambda *args: pytest.fail("must not inspect Langfuse"))
    monkeypatch.setattr(Langfuse, "check_credentials", lambda *args: pytest.fail("must not contact Langfuse"))
    events = []
    monkeypatch.setattr(commands, "ensure_docker", lambda: events.append("docker"))
    monkeypatch.setattr(Environment, "compose", lambda self, *args: events.append(args))
    monkeypatch.setattr(
        commands, "DatabaseMigrator", lambda *args: SimpleNamespace(upgrade=lambda: events.append("migrate"))
    )

    def reset(selected, state):
        assert selected is environment
        assert commands.os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://logfire-us.pydantic.dev"
        assert commands.os.environ["OTEL_EXPORTER_OTLP_HEADERS"] == "Authorization=test-write-token"
        events.append(("reset", state))

    monkeypatch.setattr(commands, "reset", reset)
    commands.main()
    assert events == (["docker", ("up", "-d", "--wait"), "migrate"] if action == "setup" else [("reset", "seeded")])
