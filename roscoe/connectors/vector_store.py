"""A local vector store — remember text, then recall the closest matches.

```yaml
connectors:
  memory:
    type: vector_store
    path: ./vectorstore.db   # created if it doesn't exist
```

No embedding API, no server, no driver: similarity is TF-IDF cosine over the
stored text, computed in pure Python at search time. That makes it exact and
free rather than semantic — "revenue growth" won't match "sales increased"
the way a real embedding model would. For a handful to a few thousand notes,
memories or support snippets that's the honest trade against needing an
OpenAI-compatible embeddings endpoint just to remember things locally; for
real semantic recall at scale, point a `rest_api` connector at a hosted
vector database instead.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.tools import StructuredTool

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class VectorStoreError(RuntimeError):
    """Raised for a store/search failure that isn't a plain config mistake."""


class VectorStoreConnector:
    """Tools: remember, recall.

    Not a :class:`~roscoe.connectors.base_connector.BaseConnector` — there is
    no HTTP endpoint to authenticate against, only a local file, matching how
    :class:`~roscoe.connectors.database.DatabaseConnector` handles SQLite.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        path = config.get("path") or "./vectorstore.db"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS documents "
            "(id TEXT PRIMARY KEY, text TEXT NOT NULL, metadata TEXT NOT NULL)"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _rows(self) -> list[tuple[str, str, str]]:
        return self._conn.execute("SELECT id, text, metadata FROM documents").fetchall()

    def _search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        rows = self._rows()
        if not rows:
            return []

        docs = [(row[0], row[1], row[2], _tokens(row[1])) for row in rows]
        q_tokens = _tokens(query)

        # Smoothed idf over the stored documents plus the query itself, so a
        # term that only appears in the query doesn't get an undefined score.
        doc_freq: Counter[str] = Counter()
        for _, _, _, tokens in docs:
            doc_freq.update(set(tokens))
        n_docs = len(docs) + 1
        vocab = set(doc_freq) | set(q_tokens)
        idf = {term: math.log((n_docs + 1) / (doc_freq.get(term, 0) + 1)) + 1 for term in vocab}

        def vector(tokens: list[str]) -> dict[str, float]:
            counts = Counter(tokens)
            total = sum(counts.values()) or 1
            weighted = {term: (count / total) * idf.get(term, 0.0) for term, count in counts.items()}
            norm = math.sqrt(sum(v * v for v in weighted.values())) or 1.0
            return {term: v / norm for term, v in weighted.items()}

        q_vec = vector(q_tokens)
        scored = []
        for doc_id, text, metadata_json, tokens in docs:
            d_vec = vector(tokens)
            score = sum(q_vec.get(term, 0.0) * weight for term, weight in d_vec.items())
            if score <= 0:
                continue
            scored.append({
                "id": doc_id, "text": text, "score": round(score, 4),
                "metadata": json.loads(metadata_json) if metadata_json else {},
            })

        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:top_k]

    @property
    def tools(self) -> list[StructuredTool]:
        def remember(text: str, id: str = "", metadata: dict[str, Any] | None = None) -> Any:
            """Store a piece of text for later recall. `id` is generated if
            omitted; reusing an existing id overwrites that entry."""
            doc_id = id or str(uuid4())
            self._conn.execute(
                "INSERT INTO documents (id, text, metadata) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET text=excluded.text, metadata=excluded.metadata",
                (doc_id, text, json.dumps(metadata or {})),
            )
            self._conn.commit()
            return {"id": doc_id, "stored": True}

        def recall(query: str, top_k: int = 5) -> Any:
            """Return the stored entries whose text is closest to `query`,
            most relevant first."""
            return self._search(query, max(1, top_k))

        return [
            StructuredTool.from_function(remember, description=remember.__doc__),
            StructuredTool.from_function(recall, description=recall.__doc__),
        ]
