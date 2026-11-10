"""Authenticated management readiness retries observation, never target mutations."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError, provider_error
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_service.providers.environment.envd_readiness import wait_until_ready

pytestmark = pytest.mark.anyio


def scope(*, failure=None, desktop_ready=True):
    return SimpleNamespace(
        open=AsyncMock(side_effect=failure),
        check_ready=AsyncMock(),
        close=AsyncMock(),
        operations=SimpleNamespace(
            computer=SimpleNamespace(
                describe=AsyncMock(
                    return_value=SimpleNamespace(
                        observe_ready=desktop_ready, input_ready=desktop_ready, targets=["display"]
                    )
                )
            )
        ),
    )


async def test_transient_connection_failure_retries_a_fresh_authenticated_scope():
    first = scope(failure=provider_error("test", "connection_failed", Category.UNAVAILABLE))
    second = scope()
    factory = Mock(side_effect=[first, second])
    await wait_until_ready(factory, provider_key="test", desktop=False, timeout=1)
    assert factory.call_count == 2
    first.close.assert_awaited_once()
    second.close.assert_awaited_once()
    second.check_ready.assert_awaited_once_with(frozenset())
    second.operations.computer.describe.assert_not_awaited()


@pytest.mark.parametrize("category", [Category.INVALID, Category.DENIED, Category.UNKNOWN_OUTCOME, Category.CONFLICT])
async def test_incompatible_unauthorized_and_uncertain_outcomes_are_not_retried(category):
    error = provider_error("test", "refused", category)
    execution = scope(failure=error)
    factory = Mock(return_value=execution)
    with pytest.raises(EnvironmentProviderError) as raised:
        await wait_until_ready(factory, provider_key="test", desktop=False, timeout=1)
    assert raised.value is error
    factory.assert_called_once()
    execution.close.assert_awaited_once()


async def test_desktop_requires_observation_and_input_then_closes_every_probe():
    first, second = scope(desktop_ready=False), scope()
    factory = Mock(side_effect=[first, second])
    await wait_until_ready(factory, provider_key="test", desktop=True, timeout=1)
    for execution in (first, second):
        execution.check_ready.assert_awaited_once_with(frozenset({"computer"}))
        execution.close.assert_awaited_once()


async def test_readiness_deadline_is_typed_and_closes_the_scope():
    execution = scope()

    async def pending(**kwargs):
        await asyncio.sleep(10)

    execution.open.side_effect = pending
    with pytest.raises(EnvironmentProviderError) as raised:
        await wait_until_ready(lambda: execution, provider_key="test", desktop=False, timeout=0.01)
    assert raised.value.category == Category.TIMEOUT
    execution.close.assert_awaited_once()


async def test_cancellation_propagates_after_closing_scope():
    execution = scope(failure=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await wait_until_ready(lambda: execution, provider_key="test", desktop=False, timeout=1)
    execution.close.assert_awaited_once()
