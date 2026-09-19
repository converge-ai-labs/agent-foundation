"""Shared file-facet operations; native setup and observations stay with each provider."""

from posixpath import dirname

BASE = "/file-tests"


async def read_stream(files, path):
    return b"".join([chunk async for chunk in files.read_bytes_stream(path)])


READS = [
    ("read", lambda files, path: files.read_bytes(path)),
    ("stream", read_stream),
    ("text", lambda files, path: files.read_text(path)),
]
MISSING = [
    *READS,
    ("stat", lambda files, path: files.stat(path)),
    ("list", lambda files, path: files.list(path, max_results=10)),
    ("remove", lambda files, path: files.remove(path)),
    ("move", lambda files, path: files.move(path, dirname(path) + "/moved")),
    ("copy", lambda files, path: files.copy(path, dirname(path) + "/copied")),
    ("replace", lambda files, path: files.write_text(path, "CHANGED", mode="replace")),
    ("patch", lambda files, path: files.patch_text(path, "@@ -1 +1 @@\n-ORIGINAL\n+CHANGED\n")),
]


WRONG_TYPES = [
    *[
        (name, lambda files, base=BASE, operation=operation: operation(files, base + "/directory"))
        for name, operation in READS
    ],
    ("list-file", lambda files, base=BASE: files.list(base + "/source", max_results=10)),
    ("replace-directory", lambda files, base=BASE: files.write_text(base + "/directory", "CHANGED", mode="replace")),
    ("copy-directory", lambda files, base=BASE: files.copy(base + "/directory", base + "/copy")),
    ("move-over-directory", lambda files, base=BASE: files.move(base + "/source", base + "/directory", replace=True)),
    ("mkdir-below-file", lambda files, base=BASE: files.mkdir(base + "/source/child")),
]


async def assert_traversal_rejected(files, path, failure, *, base=BASE):
    for _, operation in READS:
        with failure("environment_request_invalid"):
            await operation(files, path)
    for operation in (
        lambda: files.stat(path),
        lambda: files.write_text(path, "CHANGED", mode="upsert"),
        lambda: files.copy(base + "/source", path),
        lambda: files.move(base + "/source", path),
    ):
        with failure("environment_request_invalid"):
            await operation()
