"""Shared file-facet operations; native setup and observations stay with each provider."""

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
    ("move", lambda files, path: files.move(path, BASE + "/moved")),
    ("copy", lambda files, path: files.copy(path, BASE + "/copied")),
    ("replace", lambda files, path: files.write_text(path, "CHANGED", mode="replace")),
    ("patch", lambda files, path: files.patch_text(path, "@@ -1 +1 @@\n-ORIGINAL\n+CHANGED\n")),
]


WRONG_TYPES = [
    *[(name, lambda files, operation=operation: operation(files, BASE + "/directory")) for name, operation in READS],
    ("list-file", lambda files: files.list(BASE + "/source", max_results=10)),
    ("replace-directory", lambda files: files.write_text(BASE + "/directory", "CHANGED", mode="replace")),
    ("copy-directory", lambda files: files.copy(BASE + "/directory", BASE + "/copy")),
    ("move-over-directory", lambda files: files.move(BASE + "/source", BASE + "/directory", replace=True)),
    ("mkdir-below-file", lambda files: files.mkdir(BASE + "/source/child")),
]


async def assert_traversal_rejected(files, path, failure):
    for _, operation in READS:
        with failure("environment_request_invalid"):
            await operation(files, path)
    for operation in (
        lambda: files.stat(path),
        lambda: files.write_text(path, "CHANGED", mode="upsert"),
        lambda: files.copy(BASE + "/source", path),
        lambda: files.move(BASE + "/source", path),
    ):
        with failure("environment_request_invalid"):
            await operation()


async def assert_read_only_files(files, code, failure):
    assert (await files.read_text(BASE + "/source")).text == "ORIGINAL\n"
    for mode in ("create", "replace", "upsert", "append"):
        with failure(code):
            await files.write_text(BASE + "/source", "CHANGED", mode=mode)
    for operation in (
        lambda: files.mkdir(BASE + "/new"),
        lambda: files.remove(BASE + "/source"),
        lambda: files.remove(BASE + "/directory", recursive=True),
        lambda: files.move(BASE + "/source", BASE + "/moved"),
        lambda: files.copy(BASE + "/source", BASE + "/copied"),
        lambda: files.patch_text(BASE + "/source", "@@ -1 +1 @@\n-ORIGINAL\n+CHANGED\n"),
    ):
        with failure(code):
            await operation()

    async def forbidden_stream():
        raise AssertionError("Read-only upload consumed input")
        yield b"never"

    with failure(code):
        await files.write_bytes_stream(BASE + "/new", forbidden_stream(), mode="create")
