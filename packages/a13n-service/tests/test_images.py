"""Profile images and icons through their HTTP routes over real PostgreSQL/Redis and the local object store.

Only raster signatures are accepted, changes need the owner's authority and ETag, and content is served with its
recognized type and headers that keep a browser from treating it as anything but an image.
"""

import hashlib
from contextlib import AsyncExitStack
from types import SimpleNamespace

import httpx2
import pytest
from a13n_service.infra.http import etag
from a13n_service.settings import Settings

pytestmark = pytest.mark.anyio
PNG = b"\x89PNG\r\n\x1a\n" + bytes(24)
JPEG = b"\xff\xd8\xff\xe0" + bytes(24)
WEBP = b"RIFF" + (20).to_bytes(4, "little") + b"WEBPVP8 " + bytes(12)
AVATAR = "/api/v1/users/me/avatar"


def changing(item: dict, content_type: str = "image/png") -> dict[str, str]:
    return {"if-match": etag(item["id"], item["version"]), "content-type": content_type}


async def join(service: SimpleNamespace, stack: AsyncExitStack, email: str, role: str) -> httpx2.AsyncClient:
    """A client logged in as a new member of the default workspace, joined through a real invitation."""
    receipt = (
        await service.client.post(f"{service.workspace}/invitations", json={"email": email, "role": role})
    ).json()
    client = await stack.enter_async_context(
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test")
    )
    url = receipt["invitation_url"]
    token = url.split("#token=", 1)[1]
    path = "/api/v1/invitations/" + url.split("/invitations/", 1)[1].split("#", 1)[0]
    accepted = await client.post(path, json={"token": token, "password": "member-password-1234"})
    client.headers["x-csrf-token"] = accepted.json()["csrf_token"]
    return client


async def test_profile_images(service, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    profile = (await client.get("/api/v1/users/me")).json()
    assert profile["image_url"] is None
    assert (await client.put(AVATAR, content=PNG, headers={"content-type": "image/png"})).status_code == 428
    # Only a raster signature counts, whatever the request claims: SVG and HTML are refused.
    for content in (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', b"<html></html>"):
        refused = await client.put(AVATAR, content=content, headers=changing(profile))
        assert refused.status_code == 400 and refused.json()["error"]["details"]["reason"] == "unsupported_type"
    stored = await client.put(AVATAR, content=PNG, headers=changing(profile))
    assert stored.status_code == 200, stored.text
    digest = hashlib.sha256(PNG).hexdigest()
    assert stored.json()["image_url"] == f"/api/v1/users/{profile['id']}/avatar?v={digest}"
    assert stored.headers["etag"] == etag(profile["id"], profile["version"] + 1)
    assert (settings.objects.root / f"users/{profile['id']}/images/{digest}").read_bytes() == PNG
    served = await client.get(stored.json()["image_url"])
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert served.headers["cache-control"] == "private, no-store"
    # The same image again changes nothing.
    again = await client.put(AVATAR, content=PNG, headers=changing(stored.json()))
    assert again.json()["version"] == stored.json()["version"]
    # A stale ETag is refused before any bytes are stored.
    assert (await client.put(AVATAR, content=JPEG, headers=changing(profile))).status_code == 412
    assert not (settings.objects.root / f"users/{profile['id']}/images/{hashlib.sha256(JPEG).hexdigest()}").exists()

    async with AsyncExitStack() as stack:
        member = await join(service, stack, "member@example.com", "viewer")
        me = (await member.get("/api/v1/users/me")).json()
        # Anyone sharing an organization with the user sees the image; an API key cannot change it.
        assert (await member.get(stored.json()["image_url"])).content == PNG
        key = await member.post(
            "/api/v1/users/me/keys", json={"workspace_id": service.tenant.workspace_id, "name": "k"}
        )
        bearer = {**changing(me, "image/jpeg"), "authorization": "Bearer " + key.json()["secret"]}
        assert (await member.put(AVATAR, content=JPEG, headers=bearer)).status_code == 403
        theirs = await member.put(AVATAR, content=JPEG, headers=changing(me, "image/jpeg"))
        assert theirs.status_code == 200
        # Views that name a principal carry its image.
        [grant] = (await client.get(f"{service.workspace}/grants")).json()["items"]
        assert grant["principal"]["image_url"] == theirs.json()["image_url"]
        assert (await client.get(theirs.json()["image_url"])).headers["content-type"] == "image/jpeg"
        # Once the user shares no organization with the caller, there is no such user to show.
        assert (await client.delete(f"{service.workspace}/grants/{grant['id']}")).status_code == 204
        assert (await client.get(theirs.json()["image_url"])).status_code == 404
    account = (await client.post(f"{service.workspace}/service-accounts", json={"name": "ci"})).json()
    assert (await client.get(f"/api/v1/users/{account['id']}/avatar")).status_code == 404

    removed = await client.delete(AVATAR, headers=changing(stored.json()))
    assert removed.status_code == 200 and removed.json()["image_url"] is None
    assert (await client.get(stored.json()["image_url"])).status_code == 404


async def test_organization_and_workspace_icons(service, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    client, objects = service.client, settings.objects.root
    organization = (await client.get(service.organization)).json()
    workspace = (await client.get(service.workspace)).json()
    async with AsyncExitStack() as stack:
        viewer = await join(service, stack, "viewer@example.com", "viewer")
        # Only administrators change icons, and a refused upload stores nothing.
        refused = await viewer.put(f"{service.organization}/icon", content=JPEG, headers=changing(organization))
        assert refused.status_code == 403
        assert (
            await viewer.put(f"{service.workspace}/icon", content=JPEG, headers=changing(workspace))
        ).status_code == 403
        assert not (objects / "orgs").exists()

        icon = await client.put(f"{service.organization}/icon", content=JPEG, headers=changing(organization))
        assert icon.status_code == 200, icon.text
        # A stale ETag is refused before any bytes are stored.
        stale = await client.put(f"{service.organization}/icon", content=PNG, headers=changing(organization))
        assert stale.status_code == 412
        assert hashlib.sha256(PNG).hexdigest() not in {path.name for path in objects.rglob("*")}
        assert icon.json()["image_url"].startswith(f"/api/v1/organizations/{organization['id']}/icon?v=")
        served = await viewer.get(icon.json()["image_url"])
        assert (served.headers["content-type"], served.content) == ("image/jpeg", JPEG)

        workspace_icon = await client.put(f"{service.workspace}/icon", content=WEBP, headers=changing(workspace))
        assert workspace_icon.status_code == 200
        digest = hashlib.sha256(WEBP).hexdigest()
        assert (objects / f"orgs/{organization['id']}/images/{workspace['id']}/{digest}").read_bytes() == WEBP
        [listed] = (await viewer.get("/api/v1/workspaces")).json()["items"]
        assert listed["image_url"] == workspace_icon.json()["image_url"]
        assert (await viewer.get(listed["image_url"])).headers["content-type"] == "image/webp"

        removed = await client.delete(f"{service.workspace}/icon", headers=changing(workspace_icon.json()))
        assert removed.status_code == 200 and removed.json()["image_url"] is None
        assert (await viewer.get(f"{service.workspace}/icon")).status_code == 404
    events = (await client.get(f"{service.organization}/audit-events")).json()["items"]
    changes = [(event["action"], event["details"]) for event in events if event["outcome"] == "ok"][:3]
    assert changes == [("workspace.update", {"fields": ["image"]})] * 2 + [
        ("organization.update", {"fields": ["image"]})
    ]
    # An archived workspace's icon no longer changes, and the refusal comes before any bytes are stored.
    archived = await client.post(f"{service.workspace}/archive", headers=changing(removed.json()))
    refused = await client.put(f"{service.workspace}/icon", content=PNG, headers=changing(archived.json()))
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "disabled"
    digest = hashlib.sha256(PNG).hexdigest()
    assert not (objects / f"orgs/{organization['id']}/images/{workspace['id']}/{digest}").exists()


async def test_images_are_bounded_like_uploads(serve, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    objects = settings.objects.model_copy(update={"upload_bytes": 1024, "upload_limit": 2})
    async with serve(settings=settings.model_copy(update={"objects": objects})) as service:
        client = service.client
        profile = (await client.get("/api/v1/users/me")).json()
        large = await client.put(AVATAR, content=PNG + bytes(1024), headers=changing(profile))
        assert large.status_code == 413 and large.json()["error"]["details"] == {"limit": 1024}
        assert (await client.put(AVATAR, content=PNG, headers=changing(profile))).status_code == 200
        # Images and workspace uploads share one per-principal budget.
        upload = await client.post(
            f"{service.api}/uploads",
            files={"file": ("a.txt", b"a", "text/plain")},
            headers={"idempotency-key": "one"},
        )
        assert upload.status_code == 429
