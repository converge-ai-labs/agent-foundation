"""SDK preparation uses explicit credentials and validates immutable image identity."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import modal
import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.modal.shared import ModalConnectionConfiguration, ModalCredential
from a13n_service.providers.environments.modal.desktop_build import DesktopBuildAPI, specification

pytestmark = pytest.mark.anyio


@pytest.fixture
def sdk(monkeypatch):
    client = SimpleNamespace(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    factory = Mock(return_value=client)
    monkeypatch.setattr(modal, "Client", factory)
    app = SimpleNamespace()
    lookup = AsyncMock(return_value=app)
    monkeypatch.setattr(modal, "App", SimpleNamespace(lookup=SimpleNamespace(aio=lookup)))
    image = SimpleNamespace(object_id="im-TestImage", build=SimpleNamespace(aio=AsyncMock()))
    from_id = AsyncMock(return_value=image)
    monkeypatch.setattr(modal, "Image", SimpleNamespace(from_id=SimpleNamespace(aio=from_id)))
    api = DesktopBuildAPI(
        ModalConnectionConfiguration(), ModalCredential(token_id="test-id", token_secret="test-secret")
    )
    return SimpleNamespace(client=client, factory=factory, lookup=lookup, image=image, from_id=from_id, api=api)


async def test_reuse_resolves_exact_image_with_explicit_client(sdk):
    assert await sdk.api.build(specification(), "scope", "im-TestImage") == "im-TestImage"
    assert sdk.factory.call_args.args[2] == ("test-id", "test-secret")
    assert sdk.lookup.call_args.kwargs["create_if_missing"] is False
    assert sdk.lookup.call_args.kwargs["client"] is sdk.client
    sdk.from_id.assert_awaited_once_with("im-TestImage", client=sdk.client)
    await sdk.api.close()
    sdk.client.__aexit__.assert_awaited_once()


async def test_missing_reused_image_is_definitive_and_client_can_close(sdk):
    sdk.from_id.side_effect = modal.exception.NotFoundError("private upstream detail")
    with pytest.raises(EnvironmentProviderError) as error:
        await sdk.api.build(specification(), "scope", "im-TestImage")
    assert error.value.code == "desktop_image_missing"
    assert "private upstream" not in str(error.value)
    await sdk.api.close()
    sdk.client.__aexit__.assert_awaited_once()


async def test_unexpected_image_identity_is_refused(sdk):
    sdk.image.object_id = "im-OtherImage"
    with pytest.raises(EnvironmentProviderError) as error:
        await sdk.api.build(specification(), "scope", "im-TestImage")
    assert error.value.code == "desktop_image_identity_mismatch"
    await sdk.api.close()


def test_custom_images_keep_defaults_and_automatic_has_desktop_requirements():
    from a13n_service.providers.environments.modal.configuration import ModalRecipe

    auto = ModalRecipe(desktop=True)
    assert (auto.image, auto.desktop_assistance, auto.cpu, auto.memory, auto.timeout_seconds) == (
        "auto",
        True,
        2,
        4096,
        3600,
    )
    custom = ModalRecipe(image="im-Custom", desktop=True)
    assert custom.desktop_assistance is False
    assert custom.memory == 1024
    assert ModalRecipe(desktop=True, desktop_assistance=False).desktop_assistance is False
    with pytest.raises(ValueError):
        ModalRecipe(image="auto")
