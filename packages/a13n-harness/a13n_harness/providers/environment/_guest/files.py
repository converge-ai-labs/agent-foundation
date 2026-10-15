"""One bounded filesystem command, executed with the sandbox's Python standard library."""

from __future__ import annotations

import base64
import ctypes
import errno
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from a13n_harness.environment._zip import extract_zip
from a13n_harness.providers.environment._file_patterns import PathPattern, PatternError, content_pattern
from a13n_harness.providers.environment._file_search import search_text_file


def resolve(root: Path, value: str, *, follow: bool = True) -> Path:
    if not value.startswith("/") or ".." in Path(value).parts or "\x00" in value:
        raise ValueError("invalid path")
    candidate = root / value.lstrip("/")
    candidate = candidate.resolve() if follow else candidate.parent.resolve() / candidate.name
    if not candidate.is_relative_to(root):
        raise PermissionError("path escapes root")
    return candidate


class FileRequestError(ValueError):
    def __init__(self, field: str, reason: str, hint: str) -> None:
        self.details = {"field": field, "reason": reason, "hint": hint}
        super().__init__(reason)


class UnsupportedTextError(ValueError):
    pass


def require_regular_file(path: Path) -> None:
    # stat preserves missing/denied errors that is_file() can collapse into False.
    if not stat.S_ISREG(path.stat().st_mode):
        raise FileRequestError("path", "not_file", "Select a regular file, not a directory or special file.")


def metadata(root: Path, path: Path) -> dict:
    mode = path.lstat()
    kind = "other"
    for predicate, name in ((stat.S_ISLNK, "symlink"), (stat.S_ISREG, "file"), (stat.S_ISDIR, "directory")):
        if predicate(mode.st_mode):
            kind = name
            break
    return {
        "path": "/" if path == root else "/" + path.relative_to(root).as_posix(),
        "kind": kind,
        "size": mode.st_size if kind == "file" else None,
        "writable": path != root,
    }


def entries(root: Path, path: Path, request: dict, ceiling: int):
    count = 0
    git_prefix: list[str] = []

    def walk(directory: Path):
        nonlocal count
        with os.scandir(directory) as scan:
            children = []
            for entry in scan:
                count += 1
                if count > ceiling:
                    raise OverflowError("directory scan limit")
                if not request.get("include_hidden", False) and entry.name.startswith("."):
                    continue
                children.append(Path(entry.path))
        children.sort()
        ignored = set()
        if request.get("ignore_mode") == "git" and children:
            result = subprocess.run(
                [*git_prefix, "check-ignore", "--no-index", "--stdin", "-z"],
                input=b"".join(os.fsencode(child) + b"\0" for child in children),
                cwd=directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if result.returncode not in (0, 1, 128):
                raise ValueError("git ignore check failed")
            ignored = set(result.stdout.split(b"\0")) if result.returncode == 0 else set()
        actions = []
        for child in children:
            if os.fsencode(child) in ignored or (request.get("ignore_mode") == "git" and child.name == ".git"):
                continue
            actions.append((child.name, child, False))
            if request.get("recursive", False) and child.is_dir() and not child.is_symlink():
                actions.append((child.name + "/", child, True))
        for _, child, descend in sorted(actions):
            if descend:
                yield from walk(child)
            else:
                yield child

    if request.get("ignore_mode") == "git":
        if shutil.which("git") is None:
            raise FileRequestError(
                "ignore_mode", "missing_dependency", "Install Git in the image for git-ignore queries."
            )
        # An isolated Git directory reads only the mount's nested .gitignore files,
        # including outside a repository; no index, global excludes, or target writes.
        with TemporaryDirectory(prefix="a13n-ignore-") as git_directory:
            subprocess.run(
                ["git", "init", "--bare", "--quiet", git_directory],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            git_prefix = [
                "git",
                "--git-dir",
                git_directory,
                "--work-tree",
                str(root),
                "-c",
                "core.excludesFile=/dev/null",
            ]
            yield from walk(path)
    else:
        yield from walk(path)


def query(root: Path, path: Path, request: dict, config: dict) -> dict:
    if not stat.S_ISDIR(path.stat().st_mode):
        raise FileRequestError("root", "not_directory", "Select a directory; use text search to search a single file.")
    matcher = PathPattern(request["pattern"])
    results = []
    offset, limit = request.get("offset", 0), request["max_results"]
    seen = 0
    for child in entries(root, path, request, config["max_query_entries"]):
        if not matcher.matches(child.relative_to(path).as_posix()):
            continue
        item = metadata(root, child)
        if request.get("kinds") and item["kind"] not in request["kinds"]:
            continue
        if seen < offset:
            seen += 1
            continue
        if len(results) == limit:
            return {"entries": results, "offset": offset, "has_more": True}
        results.append(item)
    return {"entries": results, "offset": offset, "has_more": False}


def search(root: Path, path: Path, request: dict, config: dict) -> dict:
    mode = path.stat().st_mode
    single_file = stat.S_ISREG(mode)
    if not single_file and not stat.S_ISDIR(mode):
        raise FileRequestError(
            "root", "not_searchable", "Select a regular file or directory; special files cannot be searched."
        )
    include = PathPattern(request["include"], "include")
    pattern = content_pattern(request["pattern"], request["regex"], request["case_sensitive"])
    matches = []
    result_bytes = 0
    seen = files_scanned = 0
    offset, maximum = request["offset"], request["max_matches"]
    candidates = (
        iter((path,))
        if single_file
        else entries(root, path, {**request, "recursive": True}, config["max_query_entries"])
    )
    for candidate in candidates:
        relative = Path(request["path"]).name if single_file else candidate.relative_to(path).as_posix()
        if not include.matches(relative):
            continue
        item = metadata(root, candidate)
        if item["kind"] != "file" or item["size"] > request["max_file_bytes"]:
            continue
        if request["max_files"] is not None and files_scanned >= request["max_files"]:
            raise OverflowError("eligible file limit")
        files_scanned += 1
        try:
            scanned = search_text_file(
                candidate,
                request["pattern"],
                pattern,
                request["case_sensitive"],
                max(offset - seen, 0),
                maximum - len(matches) + 1,
                request["max_matches_per_file"],
                request["context_lines"],
                request["max_line_length"],
                min(request["max_file_bytes"], config["max_file_bytes"]),
            )
        except OSError:
            if single_file:
                raise
            continue
        if scanned is None:
            continue
        selected, count = scanned
        seen += count
        for line, text, truncated, context, start in selected:
            if len(matches) == maximum:
                return {"matches": matches, "offset": offset, "has_more": True}
            match = {
                "path": request["path"] if single_file else item["path"],
                "line": line,
                "text": text,
                "text_truncated": truncated,
                "context": context,
                "context_start_line": start,
            }
            result_bytes += len(json.dumps(match, separators=(",", ":")).encode())
            if result_bytes > config["max_file_bytes"]:
                raise OverflowError("search result exceeds configured limit")
            matches.append(match)
    return {"matches": matches, "offset": offset, "has_more": False}


def execute(request: dict) -> dict:
    config, action = request["configuration"], request["action"]
    if action == "batch":
        operations = request["arguments"]["operations"]
        if any(item["action"] not in {"stat", "mkdir", "write_text", "remove"} for item in operations):
            raise ValueError("invalid batch operation")
        results = []
        for item in operations:
            try:
                result = execute({"configuration": config, "action": item["action"], "arguments": item})
            except FileNotFoundError:
                if item["action"] not in {"stat", "remove"} or not item.get("missing_ok"):
                    raise
                result = None
            results.append(result)
        return {"results": results}
    root = Path(config["root"]).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError()
    arguments = request["arguments"]
    path = resolve(root, arguments.get("path", "/"), follow=action not in {"stat", "remove", "move", "archive"})
    mutations = {
        "stage",
        "publish",
        "mkdir",
        "remove",
        "move",
        "archive",
        "write_text",
    }
    if action in mutations and path == root:
        raise PermissionError()
    if action == "resolve":
        if arguments.get("regular_file"):
            require_regular_file(path)
            # SDK transfers can use privileged file I/O even with a user option.
            with path.open("rb"):
                pass
        return {"path": str(path)}
    if action == "stage":
        # Authorize creation as the configured guest user before SDK upload.
        if arguments.get("parents"):
            path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
        return {"path": str(path)}
    if action == "stat":
        return metadata(root, path)
    if action == "read":
        require_regular_file(path)
        with path.open("rb") as file:
            file.seek(arguments["offset"])
            return {"data": base64.b64encode(file.read(arguments["length"])).decode()}
    if action == "publish":
        staged = resolve(root, arguments["staged"])
        mode = arguments["mode"]
        if mode == "create":
            os.link(staged, path)
            staged.unlink()
        else:
            if mode == "replace":
                require_regular_file(path)
            try:
                original_mode = path.stat().st_mode
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISREG(original_mode):
                    # Apply access/executable bits to the candidate before atomic publication, never set-ID bits.
                    staged.chmod(original_mode & 0o777)
            os.replace(staged, path)
        return {}
    if action == "write_text":
        text, mode = arguments["text"], arguments["mode"]
        if "\x00" in text:
            raise UnsupportedTextError("text contains NUL")
        if mode not in {"create", "replace", "upsert"}:
            raise ValueError("invalid write mode")
        content = text.encode("utf-8")
        if len(content) > config["max_file_bytes"]:
            raise OverflowError("file exceeds configured limit")
        with NamedTemporaryFile(dir=path.parent, prefix=".a13n-write-", delete=False) as file:
            staged = Path(file.name)
            # Match ordinary newly created files, subject to the guest's umask.
            mask = os.umask(0)
            os.umask(mask)
            os.fchmod(file.fileno(), 0o666 & ~mask)
            file.write(content)
        try:
            execute(
                {
                    "configuration": config,
                    "action": "publish",
                    "arguments": {
                        "path": arguments["path"],
                        "staged": "/" + staged.relative_to(root).as_posix(),
                        "mode": mode,
                    },
                }
            )
        finally:
            staged.unlink(missing_ok=True)
        return {"bytes_written": len(content)}
    if action == "archive":
        staged = resolve(root, arguments["staged"])
        require_regular_file(staged)
        try:
            extract_zip(
                staged,
                path,
                digest=arguments["digest"],
                root=arguments["root"],
                entries=arguments["entries"],
            )
        finally:
            staged.unlink()
        return {}
    if action == "mkdir":
        path.mkdir(parents=arguments["parents"], exist_ok=arguments["exist_ok"])
        return {}
    if action == "remove":
        if path.is_symlink() or not path.is_dir():
            path.unlink()
        elif arguments["recursive"]:
            shutil.rmtree(path)
        else:
            path.rmdir()
        return {}
    if action == "move":
        destination = resolve(root, arguments["destination"], follow=False)
        if destination == root or destination.is_symlink():
            raise PermissionError()
        if path == destination:
            return {}
        if arguments["replace"]:
            try:
                os.replace(path, destination)
            except OSError as exc:
                if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST} or not path.is_dir():
                    raise
                rename_atomic(path, destination, exchange=True)
                try:
                    shutil.rmtree(path)
                except OSError:
                    return {"error": "environment_unknown_outcome"}
        else:
            rename_atomic(path, destination, exchange=False)
        return {}
    if action == "list":
        return query(root, path, {**arguments, "pattern": "*", "recursive": False}, config)
    if action == "query":
        return query(root, path, arguments, config)
    if action == "search":
        return search(root, path, arguments, config)
    raise ValueError("unknown operation")


def rename_atomic(source: Path, destination: Path, *, exchange: bool) -> None:
    if sys.platform not in {"linux", "darwin"}:
        raise OSError(errno.ENOTSUP, "atomic directory move is unavailable")
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renamex_np" if sys.platform == "darwin" else "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOTSUP, "atomic directory move is unavailable")
    rename.restype = ctypes.c_int
    if sys.platform == "darwin":
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(source), os.fsencode(destination), 2 if exchange else 4)
    else:
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 2 if exchange else 1)
    if result != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def main() -> None:
    request = json.loads(sys.argv[1])
    signal.alarm(max(1, int(request["configuration"]["request_timeout_seconds"])))
    try:
        result = execute(request)
    except FileNotFoundError:
        result = {"error": "environment_not_found"}
    except PermissionError:
        result = {"error": "environment_denied"}
    except FileExistsError:
        result = {"error": "environment_conflict"}
    except OverflowError:
        result = {"error": "environment_too_large"}
    except UnsupportedTextError:
        result = {"error": "environment_unsupported"}
    except (PatternError, FileRequestError) as exc:
        result = {"error": "environment_request_invalid", "details": exc.details}
    except (
        ValueError,
        NotADirectoryError,
        IsADirectoryError,
        re.error,
        zipfile.BadZipFile,
        zlib.error,
        EOFError,
        NotImplementedError,
    ):
        result = {"error": "environment_request_invalid"}
    except OSError as exc:
        unsupported = exc.errno in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV}
        result = {"error": "environment_unsupported" if unsupported else "environment_provider_failure"}
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
