"""Fictional identity and access scenarios through ordinary management APIs."""

from datetime import UTC, datetime, timedelta

import httpx2

from .seed_assets import asset_examples
from .seed_client import Client

PASSWORD = "local-public-password-123"


async def profiles(client: Client, organization_id: str, workspace_id: str) -> None:
    png = next(content for _, media_type, content in asset_examples() if media_type == "image/png")
    for path, name, image_suffix in (
        (f"/api/v1/organizations/{organization_id}", "Northstar Studio · fictional organization", "icon"),
        (f"/api/v1/workspaces/{workspace_id}", "Product lab · 产品体验", "icon"),
        ("/api/v1/users/me", "Alex Chen · Local administrator", "avatar"),
    ):
        with client.scope(workspace_id if "/workspaces/" in path else None):
            await client.request("PATCH", path, json={"name": name}, headers=await client.etag(path))
            await client.request(
                "PUT",
                path + "/" + image_suffix,
                content=png,
                headers={**await client.etag(path), "Content-Type": "image/png"},
            )


async def members(client: Client, base: str, app, origin: str) -> dict:
    scenarios = {}
    for role in ("builder", "runner", "viewer"):
        invitation = await client.request(
            "POST", base + "/invitations", expected=201, json={"email": f"{role}@example.com", "role": role}
        )
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app),
            base_url=client.http.base_url,
            headers={"Origin": origin},
            trust_env=False,
        ) as http:
            # Each account completes the ordinary invitation/password flow.
            other = Client(http)
            login = await other.request(
                "POST",
                f"/api/v1/invitations/{invitation['invitation']['id']}/accept",
                json={"token": invitation["invitation_url"].split("#token=")[1], "password": PASSWORD},
            )
            http.headers["X-A13N-CSRF-Token"] = login["csrf_token"]
            http.headers["X-A13N-Workspace-ID"] = base.rsplit("/", 1)[-1]
            user = await other.request(
                "PATCH",
                "/api/v1/users/me",
                json={"name": f"Local {role.title()} / 演示成员"},
                headers=await other.etag("/api/v1/users/me"),
            )
            permissions = await other.request("GET", base + "/permissions")
            if (
                "agent.read" not in permissions["actions"]
                or ("agent.create" in permissions["actions"]) != (role == "builder")
                or permissions["organization_admin"]
            ):
                raise RuntimeError("Fictional member has unexpected effective permissions")
            scenarios[role] = {"user_id": user["id"], "permissions": permissions}
            await other.request("POST", "/api/v1/auth/logout", expected=204)

    for state in ("pending", "revoked"):
        created = await client.request(
            "POST", base + "/invitations", expected=201, json={"email": f"{state}@example.com", "role": "viewer"}
        )
        invitation = created["invitation"]
        if state == "pending":
            resent = await client.request(
                "POST",
                f"/api/v1/invitations/{invitation['id']}/resend",
                json={"expected_version": invitation["version"]},
            )
            invitation = resent["invitation"]
        if state == "revoked":
            invitation = await client.request(
                "POST",
                f"/api/v1/invitations/{invitation['id']}/revoke",
                json={"expected_version": invitation["version"]},
            )
        if (invitation["revoked_at"] is not None) != (state == "revoked"):
            raise RuntimeError("Invitation lifecycle scenario did not reach its expected state")
        scenarios[f"invitation_{state}"] = invitation["id"]

    for state in ("active", "revoked", "expiring", "expired"):
        body = {"name": f"Fictional local API key · {state}"}
        if state == "expiring":
            body["expires_at"] = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        elif state == "expired":
            # Let this key expire naturally while the remaining journeys execute.
            body["expires_at"] = (datetime.now(UTC) + timedelta(seconds=2)).isoformat()
        created = await client.request("POST", base + "/personal-api-keys", expected=201, json=body)
        key = created["key"]
        if state == "revoked":
            key = await client.request("POST", f"/api/v1/api-keys/{key['id']}/revoke")
        # Plaintext keys are intentionally discarded; only public resource IDs are retained.
        scenarios[f"api_key_{state}"] = key["id"]

    for role in ("builder", "runner", "viewer"):
        account = await client.request(
            "POST",
            base + "/service-accounts",
            expected=201,
            json={"name": f"Local {role} automation", "description": "Fictional local-only identity", "role": role},
        )
        await client.request(
            "POST",
            f"/api/v1/service-accounts/{account['id']}/api-keys",
            expected=201,
            json={"name": "Public development fixture; plaintext discarded"},
        )
        if role == "viewer":
            account = await client.request(
                "PATCH",
                f"/api/v1/service-accounts/{account['id']}",
                json={
                    "expected_version": account["version"],
                    "name": account["name"],
                    "status": "disabled",
                    "role": role,
                },
            )
        scenarios[f"service_account_{role}"] = account["id"]
    return scenarios
