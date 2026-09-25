"""The installed CLI against a real database: bootstrap once, the operator's user enable/disable switch, and where\noperator commands log."""

import json
from pathlib import Path

from a13n_service.cli import main
from a13n_service.infra.audit import AuditEventRow
from a13n_service.settings import Settings
from a13n_service.tenancy.tables import PrincipalRow
from click.testing import CliRunner
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


def test_bootstrap_then_operator_disables_and_enables_a_user(
    settings: Settings, tmp_path: Path, clean_environment: None
) -> None:
    config = tmp_path / "service.toml"
    config.write_text(f'[database]\nurl = "{settings.database.url.get_secret_value()}"\nauto_migrate = false\n')
    runner = CliRunner()

    def cli(*args: str, password: str | None = None) -> dict:
        result = runner.invoke(main, ["--config", str(config), *args], input=password, catch_exceptions=False)
        assert result.exit_code == 0, result.output
        return json.loads(result.output.splitlines()[-1])

    bootstrap = ["bootstrap", "--email", "Owner@Example.com", "--password-stdin"]
    short = runner.invoke(main, ["--config", str(config), *bootstrap], input="too-short\n")
    assert short.exit_code == 1 and "12 characters" in short.output
    created = cli(*bootstrap, password="a-long-enough-password\n")
    again = runner.invoke(main, ["--config", str(config), *bootstrap], input="another-long-password\n")
    assert again.exit_code == 3 and "already initialized" in again.output
    assert cli("user", "disable", "--email", "OWNER@example.com") == {
        "id": created["principal_id"],
        "status": "disabled",
    }
    assert cli("user", "disable", "--email", "owner@example.com")["status"] == "disabled"
    assert cli("user", "enable", "--email", "owner@example.com")["status"] == "active"
    missing = runner.invoke(main, ["--config", str(config), "user", "enable", "--email", "nobody@example.com"])
    assert missing.exit_code != 0 and "not found" in missing.output

    engine = create_engine(settings.database.url.get_secret_value())
    try:
        with Session(engine) as session:
            assert session.get_one(PrincipalRow, created["principal_id"]).status == "active"
            events = session.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.target_id == created["principal_id"])
                .order_by(AuditEventRow.occurred_at)
            ).all()
    finally:
        engine.dispose()
    # A repeated switch changes nothing and records nothing; the operator acts without a principal.
    assert [(event.action, event.actor_id, event.details) for event in events] == [
        ("user.disable", None, {"authority": "operator"}),
        ("user.enable", None, {"authority": "operator"}),
    ]


def test_operator_commands_log_to_stdout_never_to_the_server_log_file(
    settings: Settings, tmp_path: Path, clean_environment: None
) -> None:
    log_file = tmp_path / "service.log"
    config = tmp_path / "service.toml"
    config.write_text(
        f'[database]\nurl = "{settings.database.url.get_secret_value()}"\nauto_migrate = false\n'
        f'[telemetry]\nlog_stdout = false\nlog_file = "{log_file}"\n'
    )
    result = CliRunner().invoke(main, ["--config", str(config), "user", "enable", "--email", "nobody@example.com"])
    assert result.exit_code != 0 and "not found" in result.output
    # The file belongs to the server process; a command beside it would rotate it under the server.
    assert not log_file.exists()
