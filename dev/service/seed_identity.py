"""Fictional people, keys and workspaces, created through the ordinary account and administration flows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dev.service.api import Api, Json
from dev.service.checkout import ADMIN_PASSWORD
from dev.service.seed_assets import png

# Every fictional member signs in with the administrator's public local password.
MEMBERS = {
    "builder": ("builder@example.com", "Morgan Lee"),
    "runner": ("runner@example.com", "Priya Raman"),
    "viewer": ("viewer@example.com", "Chen Jing"),
}
PENDING_EMAIL = "reviewer@example.com"


def seed_identity(api: Api, org: str, ws: str) -> dict[str, str]:
    """Names and images, members of every built-in role, a pending invitation, keys, service accounts and
    workspaces. The expiry sweep deletes revoked and expired invitations, so none is seeded."""
    _brand(api, org, ws)
    empty = api.post(f"{org}/workspaces", {"name": "Research lab"})
    archived = api.post(f"{org}/workspaces", {"name": "Q2 pilot"})
    api.post(f"/api/v1/workspaces/{archived['id']}/archive", current=archived)
    seeded = {"empty_workspace": empty["id"], "archived_workspace": archived["id"]}
    for role, (email, name) in MEMBERS.items():
        receipt = api.post(f"{ws}/invitations", {"email": email, "role": role})
        with Api(api.base_url) as member:
            seeded[f"member_{role}"] = member.accept_invitation(receipt["invitation_url"], ADMIN_PASSWORD, name)
    # The builder also works in the second workspace.
    api.post(f"/api/v1/workspaces/{empty['id']}/grants", {"principal_id": seeded["member_builder"], "role": "builder"})
    pending = api.post(f"{ws}/invitations", {"email": PENDING_EMAIL, "role": "viewer"})["invitation"]
    resent = api.post(f"{ws}/invitations/{pending['id']}/resend", current=pending)
    seeded["invitation_pending"] = resent["invitation"]["id"]
    return seeded | _keys(api, ws) | _service_accounts(api, ws)


def _brand(api: Api, org: str, ws: str) -> None:
    for path, name, image in (
        (org, "Northstar Studio", "icon"),
        (ws, "Product lab", "icon"),
        ("/api/v1/users/me", "Alex Chen", "avatar"),
    ):
        current = api.patch(path, api.get(path), {"name": name})
        api.put(f"{path}/{image}", current, png())


def _keys(api: Api, ws: str) -> dict[str, str]:
    """The administrator's own keys: active, expiring soon and revoked. Secrets are discarded."""
    workspace_id = api.get(ws)["id"]
    expiring = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    keys = {
        state: api.post("/api/v1/users/me/keys", {"name": name, "workspace_id": workspace_id, "expires_at": expires})[
            "key"
        ]
        for state, name, expires in (
            ("active", "Release CI", None),
            ("expiring", "Staging smoke test", expiring),
            ("revoked", "Old laptop", None),
        )
    }
    api.delete(f"/api/v1/users/me/keys/{keys['revoked']['id']}", keys["revoked"])
    return {f"api_key_{state}": key["id"] for state, key in keys.items()}


def _service_accounts(api: Api, ws: str) -> dict[str, str]:
    active = api.post(
        f"{ws}/service-accounts",
        {"name": "Release automation", "description": "Publishes fictional release notes.", "role": "runner"},
    )
    api.post(f"{ws}/service-accounts/{active['id']}/keys", {"name": "Deploy pipeline"})
    disabled: Json = api.post(f"{ws}/service-accounts", {"name": "Nightly export", "role": "viewer"})
    api.patch(f"{ws}/service-accounts/{disabled['id']}", disabled, {"status": "disabled"})
    return {"service_account_active": active["id"], "service_account_disabled": disabled["id"]}
