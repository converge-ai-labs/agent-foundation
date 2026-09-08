"""One bounded filesystem command, executed with the sandbox's Python standard library."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from ..._file_patterns import PathPattern, PatternError, content_pattern
from ..._file_search import search_text_file


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
    matcher = PathPattern(request["pattern"])
    results = []
    offset, limit = request.get("offset", 0), request["max_results"]
    seen = 0
    for child in entries(root, path, request, config["max_query_entries"]):
        if not matcher.matches(child.relative_to(path).as_posix()):
            continue
        item = metadata(root, child, config["read_only"])
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
    include = PathPattern(request["include"], "include")
    pattern = content_pattern(request["pattern"], request["regex"], request["case_sensitive"])
    matches = []
    result_bytes = 0
    seen = files_scanned = 0
    offset, maximum = request["offset"], request["max_matches"]
    for candidate in entries(root, path, {**request, "recursive": True}, config["max_query_entries"]):
        if not include.matches(candidate.relative_to(path).as_posix()):
            continue
        item = metadata(root, candidate, config["read_only"])
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
            continue
        if scanned is None:
            continue
        selected, count = scanned
        seen += count
        for line, text, truncated, context, start in selected:
            if len(matches) == maximum:
                return {"matches": matches, "offset": offset, "has_more": True}
            match = {
                "path": item["path"],
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
    except PatternError as exc:
        result = {"error": "environment_request_invalid", "details": exc.details}
    except (ValueError, NotADirectoryError, IsADirectoryError, re.error):
        result = {"error": "environment_request_invalid"}
    except OSError:
        result = {"error": "environment_provider_failure"}
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
