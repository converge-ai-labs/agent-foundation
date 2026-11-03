"""Absolute Browser deadlines share the input supervisor without changing Environment leases."""

import pytest
from a13n_service.providers.environment import desktop_guest as guest

from .environment_providers.test_desktop_access import grant, request
from .environment_providers.test_desktop_access import supervisor as supervisor

pytestmark = pytest.mark.anyio


async def test_absolute_ensure_replays_never_shorten_and_expire_without_management(supervisor, monkeypatch):
    instance, clock, _ = supervisor
    wall = [1000.0]
    monkeypatch.setattr(guest.time, "time", lambda: wall[0])
    first = await request(instance, "ensure", id="browser_first", episode=1, expires_at=1040.0)
    assert first["expires_at"] == 1040
    assert instance.grants["browser_first"].deadline == 140
    wall[0], clock[0] = 1010, 110
    longer = await request(instance, "ensure", id="browser_first", episode=1, expires_at=1100.0)
    older = await request(instance, "ensure", id="browser_first", episode=1, expires_at=1030.0)
    assert older["expires_at"] == longer["expires_at"] == 1100
    assert older["token"] == first["token"]
    assert instance.grants["browser_first"].deadline == 200
    async with instance.lock:
        wall[0], clock[0] = 1100, 200
        instance.expire_input(instance.grants["browser_first"])
        assert instance.grants["browser_first"].process.returncode == -9
    with pytest.raises(guest.Refused, match="grant_not_active"):
        await request(instance, "ensure", id="browser_first", episode=1, expires_at=1200.0)


async def test_revoke_before_ensure_fences_late_call_and_new_runtime_rejects_old_nonce(supervisor, monkeypatch):
    instance, _, _ = supervisor
    monkeypatch.setattr(guest.time, "time", lambda: 1000.0)
    await request(instance, "revoke_browser", id="browser_first", episode=1)
    with pytest.raises(guest.Refused, match="grant_not_active"):
        await request(instance, "ensure", id="browser_first", episode=1, expires_at=1060)
    previous = instance.generation
    instance.generation = "b" * 32
    instance.revocations.clear()
    with pytest.raises(guest.Refused, match="stale_generation"):
        await instance.dispatch(
            {
                "protocol_version": 2,
                "generation": previous,
                "operation": "ensure",
                "id": "browser_first",
                "episode": 1,
                "expires_at": 1060,
            }
        )
    assert not instance.grants


async def test_environment_renew_uses_absolute_deadline_without_shortening(supervisor, monkeypatch):
    instance, clock, _ = supervisor
    wall = [1000.0]
    monkeypatch.setattr(guest.time, "time", lambda: wall[0])
    await grant(instance)
    wall[0], clock[0] = 1010, 110
    await request(instance, "renew", id="control_first", epoch=1, expires_at=1015)
    assert instance.grants["control_first"].deadline == 160
    assert instance.grants["control_first"].expires_at == 1060


async def test_startup_delay_does_not_extend_absolute_input_deadline(supervisor, monkeypatch):
    instance, clock, _ = supervisor
    wall = [1000.0]
    monkeypatch.setattr(guest.time, "time", lambda: wall[0])
    original = instance.wait_ready

    async def delayed(*args):
        wall[0], clock[0] = 1010, 110
        await original(*args)

    monkeypatch.setattr(instance, "wait_ready", delayed)
    with pytest.raises(guest.Refused, match="grant_expired"):
        await request(instance, "ensure", id="browser_first", episode=1, expires_at=1005)
    assert "browser_first" not in instance.grants


async def test_episode_fence_bounds_history_and_ignores_old_revokes(supervisor, monkeypatch):
    instance, _, _ = supervisor
    monkeypatch.setattr(guest.time, "time", lambda: 1000.0)
    monkeypatch.setattr(guest, "MAX_HISTORY", 4)
    for episode in range(1, 12):
        identity = f"browser_{episode:08d}"
        await request(instance, "ensure", id=identity, episode=episode, expires_at=1060)
        await request(instance, "revoke_browser", id=identity, episode=episode)
    assert not instance.grants and not instance.revocations
    await request(instance, "ensure", id="browser_current", episode=12, expires_at=1060)
    await request(instance, "revoke_browser", id="browser_00000001", episode=1)
    assert instance.grants["browser_current"].state == "active"
    with pytest.raises(guest.Refused, match="grant_not_active"):
        await request(instance, "ensure", id="browser_00000001", episode=1, expires_at=1060)
    with pytest.raises(guest.Refused, match="grant_conflict"):
        await request(instance, "ensure", id="browser_other", episode=12, expires_at=1060)


async def test_many_unknown_browser_revocations_do_not_exhaust_history(supervisor):
    instance, _, _ = supervisor
    for episode in range(1, guest.MAX_HISTORY + 2):
        await request(instance, "revoke_browser", id=f"browser_{episode:08d}", episode=episode)
    assert not instance.revocations and not instance.grants
    with pytest.raises(guest.Refused, match="grant_not_active"):
        await request(
            instance, "ensure", id=f"browser_{episode:08d}", episode=episode, expires_at=guest.time.time() + 60
        )


async def test_newer_revoke_does_not_prevent_old_grant_cleanup(supervisor, monkeypatch):
    instance, _, _ = supervisor
    monkeypatch.setattr(guest.time, "time", lambda: 1000.0)
    await request(instance, "ensure", id="browser_first", episode=1, expires_at=1060)
    await request(instance, "revoke_browser", id="browser_next", episode=2)
    assert instance.grants["browser_first"].state == "active"
    await request(instance, "revoke_browser", id="browser_first", episode=1)
    assert not instance.grants
    with pytest.raises(guest.Refused, match="grant_not_active"):
        await request(instance, "ensure", id="browser_next", episode=2, expires_at=1060)


async def test_failed_cleanup_is_retried_before_new_authority(supervisor, monkeypatch):
    instance, _, _ = supervisor
    monkeypatch.setattr(guest.time, "time", lambda: 1000.0)
    await request(instance, "ensure", id="browser_first", episode=1, expires_at=1060)
    original = instance.settle

    async def fail():
        raise guest.Refused("input_cleanup_unconfirmed")

    monkeypatch.setattr(instance, "settle", fail)
    with pytest.raises(guest.Refused, match="input_cleanup_unconfirmed"):
        await request(instance, "revoke_browser", id="browser_first", episode=1)
    assert instance.grants["browser_first"].state == "revoking"
    with pytest.raises(guest.Refused, match="input_cleanup_unconfirmed"):
        await request(instance, "ensure", id="browser_next", episode=2, expires_at=1060)
    monkeypatch.setattr(instance, "settle", original)
    await request(instance, "ensure", id="browser_next", episode=2, expires_at=1060)
    assert "browser_first" not in instance.grants
    assert instance.grants["browser_next"].state == "active"


async def test_reused_identity_cannot_attach_to_an_older_episode(supervisor, monkeypatch):
    instance, _, _ = supervisor
    monkeypatch.setattr(guest.time, "time", lambda: 1000.0)
    await request(instance, "ensure", id="browser_first", episode=1, expires_at=1060)
    with pytest.raises(guest.Refused, match="grant_conflict"):
        await request(instance, "ensure", id="browser_first", episode=2, expires_at=1060)
    await request(instance, "revoke_browser", id="browser_first", episode=1)
    assert not instance.grants


async def test_failed_token_publication_retains_record_until_cleanup(supervisor, monkeypatch):
    instance, _, _ = supervisor
    monkeypatch.setattr(guest.time, "time", lambda: 1000.0)
    original = instance.tokens

    def fail():
        raise OSError("publication failed")

    monkeypatch.setattr(instance, "tokens", fail)
    with pytest.raises(OSError, match="publication failed"):
        await request(instance, "ensure", id="browser_first", episode=1, expires_at=1060)
    assert instance.browser_grants == {"browser_first": 1}
    monkeypatch.setattr(instance, "tokens", original)
    with pytest.raises(guest.Refused, match="grant_not_active"):
        await request(instance, "ensure", id="browser_first", episode=1, expires_at=1060)
    assert not instance.grants and not instance.browser_grants
