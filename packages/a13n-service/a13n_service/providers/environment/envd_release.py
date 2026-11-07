"""Render the shared release installer; downloads run only inside the cloud build."""

import shlex

VERSION = "0.7.0"
TARGET = "x86_64-unknown-linux-gnu"

# Executed by the guest's Python, not by the Service event loop. Keep the whole
# program in the frozen command so installer changes invalidate recipe revisions.
_INSTALL = r"""
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

version, target, destination = sys.argv[1:]
destination = pathlib.Path(destination)
asset = f"a13n-envd-{version}-{target}.tar.gz"
base = f"https://github.com/converge-ai-labs/agent-foundation/releases/download/release/a13n-envd-v{version}"
with tempfile.TemporaryDirectory(prefix=".a13n-envd-", dir=destination.parent) as directory:
    root = pathlib.Path(directory)
    for name in (asset, "SHA256SUMS"):
        subprocess.run(
            ["curl", "-fL", "--retry", "2", "--max-time", "120", f"{base}/{name}", "-o", str(root / name)],
            check=True,
        )
    matches = []
    for line in (root / "SHA256SUMS").read_text().splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].removeprefix("*").removeprefix("./") == asset:
            matches.append(parts[0])
    if len(matches) != 1 or re.fullmatch(r"[0-9a-fA-F]{64}", matches[0]) is None:
        raise RuntimeError("Expected one SHA256SUMS entry for " + asset)
    digest = hashlib.sha256()
    with (root / asset).open("rb") as archive:
        for chunk in iter(lambda: archive.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != matches[0].lower():
        raise RuntimeError("Envd release checksum mismatch")
    candidate = root / "a13n-envd"
    with tarfile.open(root / asset, "r:gz") as archive:
        member = archive.getmember("a13n-envd")
        if not member.isfile():
            raise RuntimeError("Envd release executable is not a regular file")
        with archive.extractfile(member) as source, candidate.open("wb") as output:
            shutil.copyfileobj(source, output)
    candidate.chmod(0o755)
    result = subprocess.check_output([str(candidate), "--version"], text=True, timeout=10).strip()
    if result != f"a13n-envd {version}":
        raise RuntimeError("Envd release version mismatch: " + result)
    candidate.replace(destination)
"""


def installation_command(destination: str = "/usr/local/bin/a13n-envd") -> str:
    """Return one POSIX shell line suitable for a Dockerfile RUN or guest script."""
    return shlex.join(["python3", "-I", "-c", f"exec({_INSTALL!r})", VERSION, TARGET, destination])
