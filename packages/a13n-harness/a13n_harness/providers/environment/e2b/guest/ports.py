"""Bounded sandbox-local TCP readiness check."""

import json
import socket
import sys

request = json.loads(sys.argv[1])
with socket.socket() as connection:
    connection.settimeout(0.25)
    print(json.dumps({"listening": connection.connect_ex(("127.0.0.1", request["port"])) == 0}))
