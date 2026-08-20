"""Local OpenAI-compatible embeddings server (no OpenAI account needed).

Serves POST /v1/embeddings backed by sentence-transformers
`all-mpnet-base-v2` (768 dimensions — same as the demo's Neo4j vector index).

Usage:
    uv run python src/local_embeddings.py            # listens on 127.0.0.1:8876

Then point the demo at it in .env:
    EMBEDDING_MODEL=openai/all-mpnet-base-v2
    EMBEDDING_API_BASE=http://127.0.0.1:8876/v1

and re-embed the graph once:
    uv run python src/build_semantic_layer.py --only embeddings
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_NAME = "all-mpnet-base-v2"
PORT = int(os.getenv("LOCAL_EMBEDDINGS_PORT", "8876"))

print(f"Loading {MODEL_NAME} ...", flush=True)
from sentence_transformers import SentenceTransformer  # noqa: E402

_model = SentenceTransformer(MODEL_NAME)
print(f"Ready on http://127.0.0.1:{PORT}/v1/embeddings", flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._json(200, {"status": "ok", "model": MODEL_NAME})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("embeddings"):
            self._json(404, {"error": "only /v1/embeddings is supported"})
            return
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        inputs = req.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        # `dimensions` in the request is ignored: mpnet is natively 768.
        vectors = _model.encode(inputs, normalize_embeddings=True)
        self._json(
            200,
            {
                "object": "list",
                "model": MODEL_NAME,
                "data": [
                    {"object": "embedding", "index": i, "embedding": v.tolist()}
                    for i, v in enumerate(vectors)
                ],
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            },
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
