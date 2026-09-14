"""Pinned OSS server extension: native PGVector keyset pages, not Platform emulation."""

from uuid import UUID

from auth import verify_auth
from fastapi import APIRouter, Depends, HTTPException, Query
from mem0.memory.main import _payload_is_expired
from mem0.vector_stores.pgvector import PGVector
from server_state import get_memory_instance

router = APIRouter(dependencies=[Depends(verify_auth)])


@router.get("/memories/page")
def memory_page(
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    top_k: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=36),
):
    filters = {
        key: value for key, value in {"user_id": user_id, "agent_id": agent_id, "run_id": run_id}.items() if value
    }
    if len(filters) != 1:
        raise HTTPException(400, "Select exactly one memory subject")
    if cursor is not None:
        try:
            cursor = str(UUID(cursor))
        except ValueError:
            raise HTTPException(400, "Invalid memory cursor") from None
    store = get_memory_instance().vector_store
    if not isinstance(store, PGVector):
        raise HTTPException(501, "Keyset pagination requires the patched PGVector backend")
    records, next_cursor = store.list_page(filters=filters, top_k=top_k, after_id=cursor)
    results = []
    for record in records:
        payload = record.payload or {}
        if _payload_is_expired(payload):
            continue
        item = {"id": record.id, "memory": payload.get("data", "")}
        for key in ("user_id", "agent_id", "run_id", "created_at", "updated_at", "expiration_date"):
            if key in payload:
                item[key] = payload[key]
        results.append(item)
    # An expiry-filtered page can be empty with a continuation. The cursor follows
    # storage rows, so expired records never truncate traversal or cause a loop.
    return {"results": results, "next_cursor": next_cursor}
