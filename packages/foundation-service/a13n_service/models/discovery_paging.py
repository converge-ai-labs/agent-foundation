"""Provider- and enumeration-bound pages over a complete transient catalog."""

import hashlib

from a13n_service.collection_cursors import (
    InvalidCollectionCursorError,
    decode_collection_cursor,
    encode_collection_cursor,
)
from a13n_service.iam.authorization import AuthenticatedActor

from .domain import ModelProvider
from .providers import DiscoverModelsRequest, ModelDescriptionCollection
from .service_common import ModelError


def discovery_page(
    catalog: ModelDescriptionCollection,
    request: DiscoverModelsRequest,
    *,
    provider: ModelProvider,
    actor: AuthenticatedActor,
) -> ModelDescriptionCollection:
    items = sorted({item.upstream_model: item for item in catalog.items}.values(), key=lambda item: item.upstream_model)
    scope = {
        "provider_id": provider.id,
        "updated_at": provider.updated_at.isoformat(),
        "principal": actor.principal.model_dump(mode="json"),
        "catalog": hashlib.sha256(
            catalog.model_dump_json(exclude={"items": {"__all__": {"settings_schema"}}}).encode()
        ).hexdigest(),
    }
    if request.cursor is not None:
        try:
            position = decode_collection_cursor(request.cursor, scope=scope)["after"]
            if not isinstance(position, str) or not any(item.upstream_model == position for item in items):
                raise ValueError("unknown position")
        except (InvalidCollectionCursorError, KeyError, ValueError) as error:
            raise ModelError("invalid_cursor", "Restart discovery with a fresh cursor.", status_code=400) from error
        items = [item for item in items if item.upstream_model > position]
    page = items[: request.limit]
    cursor = (
        encode_collection_cursor({"after": page[-1].upstream_model}, scope=scope)
        if len(items) > request.limit
        else None
    )
    return ModelDescriptionCollection(items=tuple(page), next_cursor=cursor)
