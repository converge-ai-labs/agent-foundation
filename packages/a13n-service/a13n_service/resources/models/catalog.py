"""The models.dev model catalog: the last catalog read successfully, revalidated in the background.

A read that finds the catalog old asks for a refresh and returns the catalog it has; only a read before the
first successful refresh waits for one, at most `FETCH_SECONDS`. `run`, a task of the app lifespan, performs the
refreshes one at a time, each bounded by `FETCH_SECONDS` and `MAX_BYTES` and sent under the Service's outbound
endpoint policy, so a cancelled read never aborts one. A failed refresh keeps the last catalog, now stale, and
is retried for a read at least `RETRY_SECONDS` later.
"""

from collections.abc import Callable, Iterable
from time import monotonic

import anyio
from a13n_harness import model_catalog_updates
from a13n_harness.model_catalog import get_official_model_catalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.model.definition import ModelProviderDefinition
from a13n_harness.spec import HarnessModelCharacteristics
from a13n_logging import exception_details, get_logger
from anyio import to_thread

from a13n_service.infra.outbound import open_http
from a13n_service.resources.models.models_dev import parse_catalog
from a13n_service.resources.models.schemas import ModelCatalog

logger = get_logger(__name__)

MODELS_DEV_URL = "https://models.dev/catalog.json"
FETCH_SECONDS = 30.0
MAX_BYTES = 8 * 1024 * 1024
REFRESH_SECONDS = 3600.0
RETRY_SECONDS = 60.0


async def run_official_catalog(policy: EndpointPolicy) -> None:
    """Refresh Harness supplements in every role under the Service outbound policy."""

    async def fetch() -> bytes:
        async with open_http(
            policy, timeout=model_catalog_updates.FETCH_SECONDS, max_bytes=model_catalog_updates.MAX_BYTES
        ) as client:
            response = await client.get(model_catalog_updates.OFFICIAL_MODELS_URL)
            response.raise_for_status()
            return response.content

    await model_catalog_updates.run_official_model_updates(fetch)


def catalog_channels(definitions: Iterable[ModelProviderDefinition]) -> frozenset[str]:
    """The catalog channels whose models some registered model provider type serves."""
    return frozenset(channel for definition in definitions for channel in definition.catalog_providers)


def model_characteristics(
    channels: tuple[str, ...], model: str, catalog: ModelCatalog
) -> HarnessModelCharacteristics | None:
    """Exact metadata match in declared channel order; never infer access or prices.

    The live catalog is primary. Current official facts fill missing fields or
    serve cold/unavailable catalogs, without fuzzy model aliases or network I/O.
    """
    official = get_official_model_catalog()
    entry = next(
        (
            item
            for channel in channels
            for item in catalog.items
            if item.ref.provider == channel and item.ref.model == model
        ),
        None,
    )
    # A later declared live channel still outranks an earlier official-only fallback.
    for channel in (entry.ref.provider,) if entry is not None else channels:
        official_channel = {
            "google": "google-gla",
            "xai": "grok",
            "x-ai": "grok",
            "fireworks-ai": "fireworks",
            "togetherai": "together",
        }.get(channel, channel)
        bundled = official.get(f"{official_channel}:{model}")
        if entry is None and bundled is None:
            continue
        facts = bundled.characteristics.model_dump(exclude_unset=True) if bundled is not None else {}
        if entry is not None:
            facts.update(entry.characteristics.model_dump(exclude_unset=True, exclude_none=True))
        return HarnessModelCharacteristics.model_validate(facts)
    return None


class ModelsDevCatalog:
    """Owned by an API-serving process, whose lifespan runs `run`; `url` is the catalog document's."""

    def __init__(
        self,
        channels: frozenset[str],
        policy: EndpointPolicy,
        *,
        url: str = MODELS_DEV_URL,
        clock: Callable[[], float] = monotonic,
    ):
        self.channels, self.policy, self.url, self.clock = channels, policy, url, clock
        self.catalog = ModelCatalog(items=[], status="unavailable")
        self.refresh_after = 0.0
        # `due` is set by a read that finds the catalog old; `refreshed` when the refresh it asked for has ended.
        self.due = anyio.Event()
        self.refreshed = anyio.Event()

    async def read(self, *, wait: bool = True) -> ModelCatalog:
        """Request refresh; optional enrichment never waits for the first fetch."""
        if self.clock() >= self.refresh_after:
            self.due.set()
            if wait and self.catalog.status == "unavailable":
                with anyio.move_on_after(FETCH_SECONDS):
                    await self.refreshed.wait()
        return self.catalog

    async def characteristics(
        self, channels: tuple[str, ...], models: tuple[str, ...]
    ) -> dict[str, HarnessModelCharacteristics | None]:
        """Enrich discovered IDs from the current snapshot without waiting for network I/O."""
        snapshot = await self.read(wait=False)

        def resolve() -> dict[str, HarnessModelCharacteristics | None]:
            return {model: model_characteristics(channels, model, snapshot) for model in models}

        # The bundled fallback lazily reads YAML and builds the pricing snapshot.
        return await to_thread.run_sync(resolve)

    async def run(self) -> None:
        """Refresh the catalog whenever a read finds it old, one refresh at a time."""
        while True:
            await self.due.wait()
            await self._refresh()
            # Reads during the refresh asked for this one, not another.
            self.due = anyio.Event()
            self.refreshed.set()
            self.refreshed = anyio.Event()

    async def _refresh(self) -> None:
        try:
            with anyio.fail_after(FETCH_SECONDS):
                async with open_http(self.policy, timeout=FETCH_SECONDS, max_bytes=MAX_BYTES) as client:
                    response = await client.get(self.url)
            response.raise_for_status()
            # Decoding and reading a few megabytes would stall the event loop.
            items = await to_thread.run_sync(parse_catalog, response.content, self.channels)
        except Exception as error:
            # The refresh task outlives any one failure, whatever it is.
            logger.warning(
                "Model catalog refresh failed",
                extra={"error_type": type(error).__name__, "exception_details": exception_details(error)},
            )
            status = "unavailable" if self.catalog.status == "unavailable" else "stale"
            self.catalog = self.catalog.model_copy(update={"status": status})
            self.refresh_after = self.clock() + RETRY_SECONDS
        else:
            self.catalog = ModelCatalog(items=items, status="ready")
            self.refresh_after = self.clock() + REFRESH_SECONDS
