"""Local-only NDJSON forwarder for an agent that accepts only stdio MCP servers."""

import os
import sys
from urllib.request import ProxyHandler, Request, build_opener


def main() -> None:
    url = os.environ["A13N_ACP_MCP_URL"]
    headers = {
        "Authorization": f"Bearer {os.environ['A13N_ACP_MCP_TOKEN']}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    opener = build_opener(ProxyHandler({}))
    for line in sys.stdin.buffer:
        if line.strip():
            with opener.open(Request(url, data=line, headers=headers)) as response:
                if data := response.read():
                    sys.stdout.buffer.write(data + b"\n")
                    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
