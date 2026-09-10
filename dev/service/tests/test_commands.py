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
    monkeypatch.setattr(Environment, "compose", lambda self, *args: events.append(args))
    monkeypatch.setattr(Langfuse, "start", lambda self: events.append("langfuse"))
    monkeypatch.setattr(
        commands, "DatabaseMigrator", lambda *args: SimpleNamespace(upgrade=lambda: events.append("migrate"))
    )
    monkeypatch.setattr(commands, "reset", lambda *args: pytest.fail("setup must never reset or seed"))
    for _ in range(2):
        commands.setup(environment, Langfuse(environment), LOCAL_CONFIG)
    assert events == [("up", "-d", "--wait"), "langfuse", "migrate"] * 2
    assert retained.read_text() == "preserve data"
    assert "Service and Console have not been started" in capsys.readouterr().out


def test_setup_refuses_incomplete_reset_before_touching_infrastructure(tmp_path, monkeypatch):
    environment = local_environment(tmp_path)
    environment.incomplete.parent.mkdir(parents=True)
    environment.incomplete.write_text("seeded\n")
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
    environment = local_environment(tmp_path, service={"port": port})
    commands.check_ports(environment)
