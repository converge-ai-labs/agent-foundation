from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_service.assets.errors import AssetError
from a13n_service.assets.service import AssetService
from a13n_service.iam import WorkspaceAction

from ..models.conftest import actor

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("changed", [False, True])
async def test_execution_content_uses_asset_use_and_rechecks_after_io(changed):
    asset = SimpleNamespace(id="ast_1234567890abcdef")
    staged = SimpleNamespace(remove=AsyncMock())
    objects = Mock(prepare_verified_content=AsyncMock(return_value=staged))
    service = AssetService(Mock(), objects, Mock(), max_size_bytes=100)
    service._get_active = AsyncMock(side_effect=[asset, object() if changed else asset])
    if changed:
        with pytest.raises(AssetError):
            await service.prepare_content_for_use(actor=actor(), asset_id=asset.id)
        staged.remove.assert_awaited_once()
    else:
        prepared = await service.prepare_content_for_use(actor=actor(), asset_id=asset.id)
        assert prepared.content is staged
        staged.remove.assert_not_called()
    for call in service._get_active.await_args_list:
        assert call.kwargs["action"] is WorkspaceAction.asset_use
