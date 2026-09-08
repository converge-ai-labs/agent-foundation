"""S3 HTTP test boundary with atomic conditions, backed by one LocalObjectStore owner.

The existing MinIO fixture deliberately lacks conditional delete. Runner tests
need an endpoint that passes the unmodified production compatibility probe.
"""

from contextlib import nullcontext
from socket import socket
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring

import pytest
import uvicorn
from a13n_service.storage.object_store import ByteRange, LocalObjectStore, ObjectConflict, ObjectNotFound
from anyio import create_task_group, fail_after, sleep
from fastapi import FastAPI, Request, Response


@pytest.fixture(name="runner_s3_endpoint")
async def runner_s3_endpoint(tmp_path):
    objects = await LocalObjectStore.create(tmp_path / "s3-server")
    app = FastAPI()

    @app.middleware("http")
    async def absolute_request_target(request, call_next):
        # Accept both origin-form and absolute-form HTTP request targets.
        if request.scope["path"].lstrip("/").startswith("http://"):
            request.scope["path"] = urlsplit(request.scope["path"].lstrip("/")).path
            request.scope["raw_path"] = request.scope["path"].encode()
        return await call_next(request)

    @app.api_route("/bucket", methods=["GET", "HEAD"])
    async def bucket(request: Request):
        if request.method == "HEAD":
            return Response(status_code=200)
        page = await objects.list(
            prefix=request.query_params.get("prefix", ""),
            cursor=request.query_params.get("continuation-token"),
            limit=int(request.query_params.get("max-keys", "1000")),
        )
        root = Element("ListBucketResult", xmlns="http://s3.amazonaws.com/doc/2006-03-01/")
        SubElement(root, "IsTruncated").text = "true" if page.cursor else "false"
        if page.cursor:
            SubElement(root, "NextContinuationToken").text = page.cursor
        for item in page.items:
            node = SubElement(root, "Contents")
            SubElement(node, "Key").text = item.key
            SubElement(node, "Size").text = str(item.size)
            SubElement(node, "ETag").text = f'"{item.version}"'
            SubElement(node, "LastModified").text = item.modified_at.isoformat()
        return Response(tostring(root), media_type="application/xml")

    @app.api_route("/bucket/{key:path}", methods=["GET", "HEAD", "PUT", "DELETE"])
    async def object_request(key: str, request: Request):
        condition = request.headers.get("if-match")
        if condition is not None:
            condition = condition.strip('"')
        try:
            if request.method == "PUT":
                info = await objects.put(
                    key,
                    await request.body(),
                    if_match=condition,
                    if_none_match=request.headers.get("if-none-match") == "*",
                    content_type=request.headers.get("content-type"),
                    metadata={
                        "test-s3-content-encoding": request.headers.get("content-encoding", ""),
                        **{
                            name.removeprefix("x-amz-meta-"): value
                            for name, value in request.headers.items()
                            if name.startswith("x-amz-meta-")
                        },
                    },
                )
            elif request.method == "DELETE":
                await objects.delete(key, if_match=condition)
                return Response(status_code=204)
            else:
                info = await objects.stat(key)
            headers = {
                "etag": f'"{info.version}"',
                "content-length": str(info.size),
                "last-modified": info.modified_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                **{
                    f"x-amz-meta-{name}": value
                    for name, value in info.metadata.items()
                    if name != "test-s3-content-encoding"
                },
            }
            if encoding := info.metadata.get("test-s3-content-encoding"):
                headers["content-encoding"] = encoding
            if request.method == "PUT":
                return Response(status_code=200, headers={"etag": headers["etag"]})
            body = b""
            if request.method == "GET":
                byte_range = None
                if raw_range := request.headers.get("range"):
                    start, end = raw_range.removeprefix("bytes=").split("-", 1)
                    byte_range = ByteRange(int(start), int(end) + 1 if end else None)
                async with objects.open(key, byte_range=byte_range) as reader:
                    body = b"".join([chunk async for chunk in reader])
                headers["content-length"] = str(len(body))
                if byte_range is not None:
                    headers["content-range"] = (
                        f"bytes {byte_range.start}-{byte_range.start + len(body) - 1}/{info.size}"
                    )
                    return Response(body, status_code=206, headers=headers, media_type=info.content_type)
            return Response(body, status_code=200, headers=headers, media_type=info.content_type)
        except (ObjectConflict, ObjectNotFound) as error:
            code, status = ("PreconditionFailed", 412) if isinstance(error, ObjectConflict) else ("NoSuchKey", 404)
            return Response(f"<Error><Code>{code}</Code></Error>", status_code=status, media_type="application/xml")

    with socket() as listener:
        listener.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(
            uvicorn.Config(app, log_config=None, access_log=False, lifespan="off", timeout_graceful_shutdown=2)
        )
        server.capture_signals = nullcontext
        async with create_task_group() as tasks:
            tasks.start_soon(_serve, server, listener)
            with fail_after(5):
                while not server.started:
                    await sleep(0.01)
            try:
                yield endpoint
            finally:
                server.should_exit = True


async def _serve(server, listener):
    await server.serve(sockets=[listener])
