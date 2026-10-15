#!/bin/sh
set -eu

if ! a13n-sandbox-init; then
    printf '%s\n' 'a13n-sandbox-entrypoint: skill initialization failed; continuing without initialization' >&2
fi
# A separate, protected credential in each container writable layer survives stop/start.
# Custom images must implement this same HTTP credential-file bootstrap contract.
if [ "${A13N_ENVD_TRANSPORT:-}" = "http" ]; then
    python3 -I -c '
import os, secrets, tempfile
from pathlib import Path
path = Path(os.environ.get("A13N_ENVD_HTTP_CREDENTIAL_FILE", "/var/lib/a13n-envd/http-token"))
path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix=".http-token-", dir=path.parent)
try:
    with os.fdopen(fd, "w") as stream:
        stream.write(secrets.token_urlsafe(32))
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        pass
finally:
    os.unlink(temporary)
'
    export A13N_ENVD_HTTP_CREDENTIAL_FILE="${A13N_ENVD_HTTP_CREDENTIAL_FILE:-/var/lib/a13n-envd/http-token}"
fi
exec "$@"
