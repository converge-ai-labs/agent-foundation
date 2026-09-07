"""One bounded filesystem command, executed with the sandbox's Python standard library."""

from __future__ import annotations

import base64
import fnmatch
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
from pathlib import Path


def resolve(root: Path, value: str, *, follow: bool = True) -> Path:
    if not value.startswith("/") or ".." in Path(value).parts or "\x00" in value:
        raise ValueError("invalid path")
    candidate = root / value.lstrip("/")
    candidate = candidate.resolve() if follow else candidate.parent.resolve() / candidate.name
    if not candidate.is_relative_to(root):
        raise PermissionError("path escapes root")
    return candidate


def metadata(root: Path, path: Path, read_only: bool) -> dict:
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
        "writable": not read_only and path != root,
    }


def entries(root: Path, path: Path, request: dict, ceiling: int):
    count = 0
    pending = [path]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as scan:
            children = []
            for entry in scan:
                count += 1
                if count > ceiling:
                    raise OverflowError("directory scan limit")
                if not request.get("include_hidden", False) and entry.name.startswith("."):
                    continue
                children.append(Path(entry.path))
        yield from sorted(children)
        if request.get("recursive", False):
            pending.extend(child for child in reversed(sorted(children)) if child.is_dir() and not child.is_symlink())


def query(root: Path, path: Path, request: dict, config: dict) -> dict:
    results = []
    for child in entries(root, path, request, config["max_query_entries"]):
        relative = child.relative_to(path).as_posix()
        pattern = request["pattern"]
        if not (
            fnmatch.fnmatchcase(relative, pattern)
            or (pattern.startswith("**/") and fnmatch.fnmatchcase(relative, pattern[3:]))
        ):
            continue
        if request.get("ignore_mode") == "git":
            result = subprocess.run(
                ["git", "check-ignore", "-q", "--", str(child)],
                cwd=path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if result.returncode == 0:
                continue
            if result.returncode not in (1, 128):
                raise ValueError("git ignore check failed")
        item = metadata(root, child, config["read_only"])
        if request.get("kinds") and item["kind"] not in request["kinds"]:
            continue
        results.append(item)
    offset, limit = request.get("offset", 0), request["max_results"]
    return {"entries": results[offset : offset + limit], "offset": offset, "has_more": len(results) > offset + limit}


def search(root: Path, path: Path, request: dict, config: dict) -> dict:
    candidates = query(
        root,
        path,
        {
            "pattern": request["include"],
            "recursive": True,
            "include_hidden": request["include_hidden"],
            "ignore_mode": request["ignore_mode"],
            "kinds": ["file"],
            "max_results": config["max_query_entries"],
        },
        config,
    )["entries"]
    flags = 0 if request["case_sensitive"] else re.IGNORECASE
    pattern = re.compile(request["pattern"] if request["regex"] else re.escape(request["pattern"]), flags)
    matches = []
    result_bytes = 0
    offset, maximum = request["offset"], request["max_matches"]
    for item in candidates[: request["max_files"]]:
        candidate = resolve(root, item["path"])
        limit = min(request["max_file_bytes"], config["max_file_bytes"])
        with candidate.open("rb") as file:
            content = file.read(limit + 1)
        if len(content) > limit or b"\x00" in content:
            continue
        try:
            lines = list(io.StringIO(content.decode("utf-8"), newline="\n"))
        except UnicodeDecodeError:
            continue
        count = 0
        for number, line in enumerate(lines):
            if not pattern.search(line):
                continue
            if request["max_matches_per_file"] is not None and count >= request["max_matches_per_file"]:
                break
            count += 1
            width = request["max_line_length"]
            start = max(0, number - request["context_lines"])
            end = number + request["context_lines"] + 1
            text = line.rstrip("\r\n")
            matches.append(
                {
                    "path": item["path"],
                    "line": number + 1,
                    "text": text[:width],
                    "text_truncated": len(text) > width,
                    "context": "".join(part[:width] for part in lines[start:end]),
                    "context_start_line": start + 1,
                }
            )
            result_bytes += len(json.dumps(matches[-1], separators=(",", ":")).encode())
            if result_bytes > config["max_file_bytes"]:
                raise OverflowError("search result exceeds configured limit")
            if len(matches) > offset + maximum:
                return {"matches": matches[offset : offset + maximum], "offset": offset, "has_more": True}
    return {"matches": matches[offset : offset + maximum], "offset": offset, "has_more": False}


def execute(request: dict) -> dict:
    config, action = request["configuration"], request["action"]
    root = Path(config["root"]).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError()
    arguments = request["arguments"]
    path = resolve(root, arguments.get("path", "/"), follow=action not in {"stat", "remove", "move"})
    mutations = {
        "publish",
        "mkdir",
        "remove",
        "move",
    }
    if action in mutations and (config["read_only"] or path == root):
        raise PermissionError()
    if action == "resolve":
        if arguments.get("regular_file") and not path.is_file():
            raise ValueError("not a regular file")
        return {"path": str(path)}
    if action == "stat":
        return metadata(root, path, config["read_only"])
    if action == "read":
        if not path.is_file():
            raise ValueError("not a regular file")
        with path.open("rb") as file:
            file.seek(arguments["offset"])
            return {"data": base64.b64encode(file.read(arguments["length"])).decode()}
    if action == "publish":
        staged = resolve(root, arguments["staged"])
        mode = arguments["mode"]
        if mode == "create":
            os.link(staged, path)
            staged.unlink()
        elif mode == "replace" and not path.is_file():
            raise FileNotFoundError()
        else:
            os.replace(staged, path)
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
        destination = resolve(root, arguments["destination"])
        if destination == root:
            raise PermissionError()
        if arguments["replace"]:
            os.replace(path, destination)
        else:
            # Linux renameat2 provides an atomic no-replace publication for files and directories.
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            result = libc.renameat2(-100, os.fsencode(path), -100, os.fsencode(destination), 1)
            if result != 0:
                number = ctypes.get_errno()
                raise OSError(number, os.strerror(number))
        return {}
    if action == "list":
        return query(root, path, {**arguments, "pattern": "*", "recursive": False}, config)
    if action == "query":
        return query(root, path, arguments, config)
    if action == "search":
        return search(root, path, arguments, config)
    raise ValueError("unknown operation")


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
    except (ValueError, NotADirectoryError, IsADirectoryError, re.error):
        result = {"error": "environment_request_invalid"}
    except OSError:
        result = {"error": "environment_provider_failure"}
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
