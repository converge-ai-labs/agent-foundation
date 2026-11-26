"""The installed CLI against a real database: bootstrap once, the operator's user enable/disable switch, and where\noperator commands log."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_service import cli as service_cli
from a13n_service.app_definitions import AppDefinition, InstalledApp
from a13n_service.cli import main
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.crypto import secret_hash
from a13n_service.settings import Settings
from a13n_service.tenancy.credentials import verify_password
from a13n_service.tenancy.tables import OrganizationRow, PasswordRow, PrincipalRow, TokenRow
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
    short = runner.invoke(main, ["--config", str(config), *bootstrap], input="short-7\n")
    assert short.exit_code == 1 and "8 characters" in short.output
    created = cli(*bootstrap, password="eight-88\n")
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


@pytest.mark.parametrize("installed", [True, False])
def test_cli_bootstrap_seeds_only_installed_apps(
    settings: Settings, tmp_path: Path, clean_environment: None, monkeypatch: pytest.MonkeyPatch, installed: bool
) -> None:
    app = InstalledApp(AppDefinition(app_id="acme.reports", name="Reports"), "reports", "1.0")
    monkeypatch.setattr(service_cli, "discover_apps", lambda: (app,) if installed else ())
    config = tmp_path / "service.toml"
    config.write_text(
        f'[database]\nurl = "{settings.database.url.get_secret_value()}"\nauto_migrate = false\n'
        '[plugins]\nbootstrap_enabled_apps = ["acme.reports"]\n'
    )
    runner = CliRunner()
    args = ["--config", str(config), "bootstrap", "--email", "owner@example.com", "--password-stdin"]
    result = runner.invoke(main, args, input="eight-88\n", catch_exceptions=False)
    engine = create_engine(settings.database.url.get_secret_value())
    try:
        with Session(engine) as session:
            organization = session.scalars(select(OrganizationRow)).one_or_none()
            if installed:
                assert result.exit_code == 0, result.output
                assert organization is not None
                assert organization.settings == {"app_plugins": {"enabled": ["acme.reports"]}}
            else:
                assert result.exit_code == 1 and "not installed" in result.output
                assert organization is None
    finally:
        engine.dispose()


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


def test_operator_resets_local_user_password_and_revokes_tokens(
    settings: Settings, tmp_path: Path, clean_environment: None
) -> None:
    config = tmp_path / "service.toml"
    config.write_text(f'[database]\nurl = "{settings.database.url.get_secret_value()}"\nauto_migrate = false\n')
    runner = CliRunner()

    def invoke(*args: str, stdin: str | None = None):
        return runner.invoke(main, ["--config", str(config), *args], input=stdin, catch_exceptions=False)

    created_result = invoke("bootstrap", "--email", "Owner@Example.com", "--password-stdin", stdin="old-pass-8\n")
    assert created_result.exit_code == 0, created_result.output
    principal_id = json.loads(created_result.output.splitlines()[-1])["principal_id"]

    # Usage and validation guards.
    assert invoke("user", "reset-password", "--password-stdin", stdin="new-pass-8\n").exit_code != 0
    assert (
        invoke(
            "user",
            "reset-password",
            "--email",
            "owner@example.com",
            "--principal-id",
            principal_id,
            "--password-stdin",
            stdin="new-pass-8\n",
        ).exit_code
        != 0
    )
    short = invoke("user", "reset-password", "--email", "owner@example.com", "--password-stdin", stdin="short-7\n")
    assert short.exit_code == 1 and "8" in short.output

    # Seed an active session token, a pending reset token, and an OIDC-only user.
    engine = create_engine(settings.database.url.get_secret_value())
    try:
        with Session(engine) as session:
            expires = datetime.now(UTC) + timedelta(hours=1)
            session.add_all(
                [
                    TokenRow(
                        id="ase_existing",
                        principal_id=principal_id,
                        kind="session",
                        secret_hash=secret_hash("session-secret-value"),
                        expires_at=expires,
                    ),
                    TokenRow(
                        id="prt_existing",
                        principal_id=principal_id,
                        kind="password_reset",
                        secret_hash=secret_hash("reset-secret-value"),
                        expires_at=expires,
                    ),
                    PrincipalRow(
                        id="usr_oidc_only",
                        kind="user",
                        name="OIDC User",
                        email=None,
                        oidc_issuer="https://idp.example.com",
                        oidc_subject="sub-123",
                        oidc_email="oidc@example.com",
                        status="active",
                    ),
                ]
            )
            session.commit()
    finally:
        engine.dispose()

    # Refuses OIDC-only user and unknown user.
    oidc_res = invoke(
        "user", "reset-password", "--principal-id", "usr_oidc_only", "--password-stdin", stdin="new-pass-8\n"
    )
    assert oidc_res.exit_code == 1 and "no local password" in oidc_res.output
    missing = invoke(
        "user", "reset-password", "--email", "nobody@example.com", "--password-stdin", stdin="new-pass-8\n"
    )
    assert missing.exit_code == 1 and "not found" in missing.output

    # Refuses disabled user until re-enabled.
    assert invoke("user", "disable", "--email", "owner@example.com").exit_code == 0
    disabled_res = invoke(
        "user", "reset-password", "--email", "owner@example.com", "--password-stdin", stdin="new-pass-8\n"
    )
    assert disabled_res.exit_code == 1 and "disabled" in disabled_res.output
    assert invoke("user", "enable", "--email", "owner@example.com").exit_code == 0

    # Refuses when password sign-in is disabled.
    no_pw_config = tmp_path / "no-pw.toml"
    no_pw_config.write_text(
        f'[database]\nurl = "{settings.database.url.get_secret_value()}"\nauto_migrate = false\n'
        "[auth]\npassword_enabled = false\n"
        '[auth.oidc]\ndefaults = [{ workspace_id = "ws_example", role = "runner" }]\n'
        '[auth.oidc.providers.team]\nlabel = "Team"\nissuer = "https://idp.example.com"\n'
        'client_id = "a13n-console"\nclient_secret = "secret"\n'
    )
    no_pw_res = runner.invoke(
        main,
        ["--config", str(no_pw_config), "user", "reset-password", "--email", "owner@example.com", "--password-stdin"],
        input="new-pass-8\n",
        catch_exceptions=False,
    )
    assert no_pw_res.exit_code == 1 and "Password sign-in is disabled" in no_pw_res.output

    # Re-add fresh tokens after enable (disable revoked the first pair) and reset by email.
    engine = create_engine(settings.database.url.get_secret_value())
    try:
        with Session(engine) as session:
            expires = datetime.now(UTC) + timedelta(hours=1)
            session.add_all(
                [
                    TokenRow(
                        id="ase_live",
                        principal_id=principal_id,
                        kind="session",
                        secret_hash=secret_hash("live-session-secret"),
                        expires_at=expires,
                    ),
                    TokenRow(
                        id="prt_live",
                        principal_id=principal_id,
                        kind="password_reset",
                        secret_hash=secret_hash("live-reset-secret"),
                        expires_at=expires,
                    ),
                ]
            )
            session.commit()
    finally:
        engine.dispose()

    reset_by_email = invoke(
        "user", "reset-password", "--email", "OWNER@example.com", "--password-stdin", stdin="new-pass-88\n"
    )
    assert reset_by_email.exit_code == 0, reset_by_email.output
    assert json.loads(reset_by_email.output.splitlines()[-1]) == {"id": principal_id}

    # Also supports --principal-id.
    reset_by_id = invoke(
        "user", "reset-password", "--principal-id", principal_id, "--password-stdin", stdin="newer-pass-99\n"
    )
    assert reset_by_id.exit_code == 0, reset_by_id.output
    assert json.loads(reset_by_id.output.splitlines()[-1]) == {"id": principal_id}

    engine = create_engine(settings.database.url.get_secret_value())
    try:
        with Session(engine) as session:
            stored_hash = session.get_one(PasswordRow, principal_id).hash
            live_tokens = session.scalars(select(TokenRow).where(TokenRow.principal_id == principal_id)).all()
            reset_events = session.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.target_id == principal_id, AuditEventRow.action == "user.password.reset")
                .order_by(AuditEventRow.occurred_at)
            ).all()
    finally:
        engine.dispose()

    assert asyncio.run(verify_password(stored_hash, "newer-pass-99"))
    assert not asyncio.run(verify_password(stored_hash, "old-pass-8"))
    assert all(token.revoked_at is not None for token in live_tokens)
    assert [(event.actor_id, event.details) for event in reset_events] == [
        (None, {"authority": "operator"}),
        (None, {"authority": "operator"}),
    ]
