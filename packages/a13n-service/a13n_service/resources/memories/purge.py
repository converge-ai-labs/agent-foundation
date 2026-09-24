"""The outbox sender that purges a deleted record memory's namespace in its provider's backend.

Deleting a record memory stages one `memory_purge` delivery naming the provider and the namespace. The sender
reads the provider, enabled or not, since purging finishes a deletion rather than starting new use, and calls the
backend outside any session within `providers.operation_seconds`. A failed purge retries with the outbox's
backoff and ends dead after `control.outbox_attempts`; a provider type the deployment no longer registers ends it
dead at once. While a purge is pending, no new memory may claim its namespace.
"""

from dataclasses import dataclass

from a13n_harness.providers.memory import MemoryStoreError
from anyio import fail_after

from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.outbox import Claim, Undelivered, settle
from a13n_service.resources.memories.records import backend, open_record_store
from a13n_service.resources.providers.service import read_provider
from a13n_service.resources.providers.tables import MemoryProviderRow
from a13n_service.resources.requests import Runtime


@dataclass(frozen=True, slots=True)
class MemoryPurger:
    """The outbox handler for `memory_purge` rows."""

    runtime: Runtime

    async def __call__(self, claim: Claim) -> None:
        runtime = self.runtime
        async with short_session(runtime.storage) as session:
            provider = await read_provider(session, MemoryProviderRow, claim.target["provider_id"])
        if provider.type not in runtime.registry.memory:
            async with transaction(runtime.storage) as session:
                await settle(session, claim, "dead", error="type_unavailable")
            return
        try:
            with fail_after(runtime.settings.providers.operation_seconds):
                async with open_record_store(runtime, provider, claim.target["namespace"]) as store:
                    with backend():
                        await store.purge()
        except MemoryStoreError as error:
            raise Undelivered(error.code) from None
        except TimeoutError:
            raise Undelivered("timeout") from None
        async with transaction(runtime.storage) as session:
            await settle(session, claim, "delivered")
