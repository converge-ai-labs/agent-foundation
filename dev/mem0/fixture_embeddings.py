"""Deterministic OpenAI-compatible embeddings for local wiring tests, not semantic inference."""

import hashlib
import json
import math
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIMENSIONS = 128


def embed(text: str) -> list[float]:
    values = [0.0] * DIMENSIONS
    for word in re.findall(r"\w+", text.casefold()) or [text]:
        digest = hashlib.sha256(word.encode()).digest()
        values[int.from_bytes(digest[:4], "big") % DIMENSIONS] += 1 if digest[4] & 1 else -1
    norm = math.sqrt(sum(value * value for value in values)) or 1
    return [value / norm for value in values]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        if self.path != "/v1/embeddings":
            self.send_error(400, "This local fixture supports embeddings only; configure a real LLM for inference")
            return
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 1024 * 1024:
            self.send_error(413)
            return
        try:
            body = json.loads(self.rfile.read(size))
            texts = body["input"]
            if isinstance(texts, str):
                texts = [texts]
            if not isinstance(texts, list) or len(texts) > 100 or any(not isinstance(text, str) for text in texts):
                raise ValueError("Invalid input")
            if body.get("dimensions", DIMENSIONS) != DIMENSIONS:
                raise ValueError("The local fixture uses 128 dimensions")
            result = {
                "object": "list",
                "model": "local-hash-128",
                "data": [
                    {"object": "embedding", "index": index, "embedding": embed(text)}
                    for index, text in enumerate(texts)
                ],
                "usage": {"prompt_tokens": len(texts), "total_tokens": len(texts)},
            }
        except (ValueError, KeyError, TypeError):
            self.send_error(400, "Invalid embedding request")
            return
        encoded = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 18081), Handler).serve_forever()
