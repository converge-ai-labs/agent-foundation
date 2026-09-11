"""Search and paginate one complete Provider directory snapshot."""

from datetime import datetime

from a13n_service.application_errors import ErrorCategory
from a13n_service.collection_cursors import decode_collection_cursor, encode_collection_cursor

from .contracts import DiscoveredConnector
from .domain import Connector, ConnectorCollection
from .errors import ConnectorError


def directory_page(
    items: tuple[DiscoveredConnector, ...],
    *,
    provider_id: str,
    refreshed_at: datetime,
    query: str,
    cursor: str | None,
    limit: int,
) -> ConnectorCollection:
    query = query.strip().casefold()
    scope: dict[str, object] = {
        "provider": provider_id,
        "refreshed_at": refreshed_at.isoformat(),
        "query": query,
    }
    after = ""
    if cursor is not None:
        try:
            value = decode_collection_cursor(cursor, scope=scope, kind="connector_directory").get("after")
            if not isinstance(value, str):
                raise ValueError("invalid directory cursor")
            after = value
        except ValueError as error:
            raise ConnectorError(
                "invalid_cursor",
                "Directory changed or cursor does not match this search.",
                category=ErrorCategory.invalid_request,
            ) from error
    matches = sorted(
        (
            item
            for item in items
            if item.key > after
            and (not query or query in f"{item.key} {item.name} {item.description or ''}".casefold())
        ),
        key=lambda item: item.key,
    )
    page = matches[:limit]
    return ConnectorCollection(
        items=tuple(Connector(connector_provider_id=provider_id, **item.model_dump()) for item in page),
        refreshed_at=refreshed_at,
        next_cursor=encode_collection_cursor({"after": page[-1].key}, scope=scope, kind="connector_directory")
        if len(matches) > limit
        else None,
    )
