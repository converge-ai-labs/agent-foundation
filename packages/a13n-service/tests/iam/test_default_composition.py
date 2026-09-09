from urllib.parse import urlsplit

import pytest
from a13n_service.app import create_app
from a13n_service.cli import main
from a13n_service.settings import ProcessRole
from click.testing import CliRunner
from httpx2 import ASGITransport, AsyncClient

from tests.process.support import local_settings


@pytest.mark.anyio
async def test_default_process_accepts_bootstrap_and_authenticates_product_api(tmp_path, caplog):
    app = create_app(local_settings(tmp_path, role=ProcessRole.control))
    async with app.router.lifespan_context(app):
        message = next(
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("Administrator initialization link")
        )
        link = urlsplit(message.split(": ", 1)[1])
        async with AsyncClient(
            transport=ASGITransport(app), base_url="https://testserver", headers={"Origin": "https://testserver"}
        ) as client:
            accepted = await client.post(
                "/api/v1" + link.path,
                json={"token": link.fragment.removeprefix("token="), "password": "initial-administrator-password"},
            )
            assert accepted.status_code == 200, accepted.text
            client.headers["X-A13N-CSRF-Token"] = accepted.json()["csrf_token"]
            org = (await client.get("/api/v1/organizations")).json()["items"][0]["id"]
            ws = (await client.get(f"/api/v1/organizations/{org}/workspaces")).json()["items"][0]["id"]
            key = await client.post(f"/api/v1/workspaces/{ws}/personal-api-keys", json={"name": "application"})
            assert key.status_code == 201, key.text
            callbacks = [
                "/api/v1/oauth/mcp/callback?code=code&state=" + "s" * 32 + "&iss=https://issuer.example",
            ]
            for callback in callbacks:
                denied = await client.get(callback, headers={"X-A13N-CSRF-Token": ""})
                assert denied.status_code == 403, denied.text
                assert denied.json()["error"]["code"] == "csrf_rejected"
            completion = await client.post(
                "/api/v1/connector-setup/complete",
                headers={"X-A13N-CSRF-Token": ""},
                json={"attempt_id": "csa_test", "browser_nonce": "b" * 64, "session_uri": "private-session"},
            )
            assert completion.status_code == 403 and completion.json()["error"]["code"] == "csrf_rejected"
            assert "private-session" not in completion.text
            old_callback = await client.get("/connectivity/v1/connector-setup/callback?session_uri=private-session")
            assert old_callback.status_code == 404
            client.cookies.clear()
            client.headers["Authorization"] = f"Bearer {key.json()['bearer']}"
            result = await client.get(f"/api/v1/workspaces/{ws}/agents")
            assert result.status_code == 200, result.text
            assert result.json()["items"] == []


def test_operator_reissue_invalidates_old_link(tmp_path, monkeypatch):
    import a13n_service.cli as cli

    settings = local_settings(tmp_path, role=ProcessRole.control)
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    runner = CliRunner()
    first = runner.invoke(main, ["iam", "reissue-bootstrap"])
    assert first.exit_code == 0, first.output
    second = runner.invoke(main, ["iam", "reissue-bootstrap"])
    assert second.exit_code == 0, second.output
    first_link, second_link = urlsplit(first.output.strip()), urlsplit(second.output.strip())
    assert first_link.path == second_link.path
    assert first_link.fragment != second_link.fragment
