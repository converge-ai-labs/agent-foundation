"""Bounded authenticated EIP readiness for explicit management transitions."""

import asyncio
from collections.abc import Callable

from a13n_harness.environment.errors import EnvironmentProviderError, provider_error
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.remote_envd.environment import RemoteEnvdExecution


async def wait_until_ready(
    execution_factory: Callable[[], RemoteEnvdExecution],
    *,
    provider_key: str,
    desktop: bool,
    timeout: float,
) -> None:
    """Probe fresh temporary scopes; never dispatch or replay Agent operations."""
    try:
        async with asyncio.timeout(timeout):
            while True:
                execution = execution_factory()
                try:
                    await execution.open(execution_id="exec-readiness")
                    await execution.check_ready(frozenset({"computer"}) if desktop else frozenset())
                    if not desktop:
                        return
                    computer = execution.operations.computer
                    if computer is not None:
                        description = await computer.describe()
                        if description.observe_ready and description.input_ready and description.targets:
                            return
                except EnvironmentProviderError as error:
                    if error.category not in {Category.UNAVAILABLE, Category.TIMEOUT}:
                        raise
                except EnvironmentError as error:
                    if error.code not in {"environment_unavailable", "environment_provider_failure"}:
                        raise
                finally:
                    await execution.close()
                await asyncio.sleep(0.1)
    except TimeoutError:
        raise provider_error(provider_key, "envd_readiness_timeout", Category.TIMEOUT) from None
