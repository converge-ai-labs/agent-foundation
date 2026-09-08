"""Receive real OTLP/HTTP protobuf exports; optionally reject them with HTTP 503."""

import gzip
import json

import anyio
from fastapi import APIRouter, Depends, Request, Response
from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


def telemetry_router(root, authenticate):
    router = APIRouter(prefix="/__live__/otlp", dependencies=[Depends(authenticate)])

    @router.post("/v1/traces")
    async def receive(request: Request):
        body = await request.body()
        if request.headers.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        message = ExportTraceServiceRequest.FromString(body)
        async with await anyio.Path(root / "otlp.jsonl").open("a") as output:
            await output.write(json.dumps(MessageToDict(message)) + "\n")
        if await anyio.Path(root / "telemetry_fail").exists():
            return Response(status_code=503)
        return Response(b"", media_type="application/x-protobuf")

    return router
