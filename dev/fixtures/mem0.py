"""A loopback fake of the self-hosted mem0 REST server; it never contacts mem0 and does no inference.

It keeps records in memory and serves the routes the `mem0_oss` Memory Provider calls. Search scores a record by
the share of the query's words it contains. `/fixture/*` routes let a journey fail operations and read what a
namespace holds after the Service can no longer show it.
"""

import argparse
import re
from datetime import UTC, datetime
from uuid import uuid4

import uvicorn
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

router = APIRouter()
# Record ID to {id, memory, user_id, created_at, updated_at}; mem0 IDs are global across namespaces.
RECORDS: dict[str, dict] = {}
# Operations (`add`, `list`, `search`, `update`, `delete`, `purge`) that answer 503 until cleared.
FAILING: set[str] = set()


def _refused(operation: str) -> JSONResponse | None:
    if operation in FAILING:
        return JSONResponse({"detail": f"Intentional local mem0 {operation} failure"}, status_code=503)
    return None


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _owned(user_id: str) -> list[dict]:
    return [record for record in RECORDS.values() if record["user_id"] == user_id]


@router.post("/memories")
async def add(body: dict):
    if refused := _refused("add"):
        return refused
    now = datetime.now(UTC).isoformat()
    text = "\n".join(message["content"] for message in body["messages"])
    record = {"id": str(uuid4()), "memory": text, "user_id": body["user_id"], "created_at": now, "updated_at": now}
    RECORDS[record["id"]] = record
    return {"results": [{"id": record["id"], "memory": text, "event": "ADD"}]}


@router.get("/memories")
async def list_records(user_id: str, top_k: int = 100):
    return _refused("list") or {"results": _owned(user_id)[:top_k]}


@router.get("/memories/{record_id}")
async def get(record_id: str):
    return RECORDS.get(record_id)


@router.put("/memories/{record_id}")
async def update(record_id: str, body: dict):
    if refused := _refused("update"):
        return refused
    if (record := RECORDS.get(record_id)) is None:
        return JSONResponse({"detail": "Memory not found"}, status_code=404)
    record.update(memory=body["text"], updated_at=datetime.now(UTC).isoformat())
    return {"message": "Memory updated successfully!"}


@router.delete("/memories/{record_id}")
async def delete(record_id: str):
    if refused := _refused("delete"):
        return refused
    if RECORDS.pop(record_id, None) is None:
        return JSONResponse({"detail": "Memory not found"}, status_code=404)
    return {"message": "Memory deleted successfully"}


@router.delete("/memories")
async def purge(user_id: str):
    if refused := _refused("purge"):
        return refused
    for record in _owned(user_id):
        del RECORDS[record["id"]]
    return {"message": "All relevant memories deleted"}


@router.post("/search")
async def search(body: dict):
    if refused := _refused("search"):
        return refused
    user_id = body.get("user_id") or body.get("filters", {}).get("user_id")
    words = _words(body["query"])
    scored = [
        {**record, "score": len(words & _words(record["memory"])) / max(len(words), 1)} for record in _owned(user_id)
    ]
    ranked = sorted((record for record in scored if record["score"]), key=lambda record: -record["score"])
    return {"results": ranked[: body.get("top_k", 10)]}


@router.put("/fixture/failing")
async def fail(body: dict):
    FAILING.clear()
    FAILING.update(body["operations"])
    return {"failing": sorted(FAILING)}


@router.get("/fixture/namespaces/{user_id}")
async def namespace(user_id: str):
    return {"texts": [record["memory"] for record in _owned(user_id)]}


app = FastAPI()
app.include_router(router)


@app.get("/healthz")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fd", type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(app, fd=args.fd, log_level="warning", access_log=False)
