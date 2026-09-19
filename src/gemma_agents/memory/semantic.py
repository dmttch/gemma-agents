"""Store workspace-scoped facts and rank them using model-specific embeddings."""

import json
import math
from pathlib import Path
from uuid import uuid4

from gemma_agents.memory.database import Database, now_iso


class SemanticMemory:
    """Persist facts and retrieve them by cosine similarity within one workspace."""

    def __init__(self, database: Database, workspace: Path, llm, model: str):
        """Bind memory storage, workspace identity, and the embedding model."""
        self.database = database
        self.workspace = str(workspace.resolve())
        self.llm = llm
        self.model = model

    def remember(self, content: str) -> str:
        """Save a useful long-term fact explicitly requested by the user.

        Args:
            content: Concise durable fact; do not store secrets.
        """
        if not content.strip() or len(content) > 8000:
            raise ValueError("Un souvenir doit contenir entre 1 et 8000 caractères.")
        vector = self.llm.embed(content, self.model)
        memory_id = uuid4().hex[:12]
        with self.database.connect() as db:
            db.execute("INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?)",
                       (memory_id, self.workspace, content, self.model,
                        json.dumps(vector), now_iso()))
        return memory_id

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Return the highest-scoring memories for this workspace and embedding model.

        Skip vectors with incompatible dimensions and score zero-length vectors
        as zero. Avoid an embedding request when no stored memories match the scope.
        """
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT * FROM memories WHERE workspace = ? AND model = ?",
                (self.workspace, self.model),
            ).fetchall()
        if not rows:
            return []
        vector = self.llm.embed(query, self.model)
        matches = []
        for row in rows:
            stored = json.loads(row["embedding"])
            if len(stored) != len(vector):
                continue
            # Cosine similarity normalizes magnitude; a zero vector gets score 0
            # instead of causing division by zero.
            norm = math.sqrt(sum(x*x for x in stored) * sum(x*x for x in vector))
            score = sum(a*b for a, b in zip(stored, vector)) / norm if norm else 0
            matches.append({"id": row["id"], "content": row["content"], "score": score})
        return sorted(matches, key=lambda item: item["score"], reverse=True)[:limit]

    def recall(self, query: str, limit: int = 5) -> str:
        """Find long-term memories relevant to a query in this workspace.

        Args:
            query: Meaning to search for.
            limit: Maximum number of memories, 1 to 10.
        """
        if not 1 <= limit <= 10:
            raise ValueError("limit doit être compris entre 1 et 10.")
        return json.dumps(self.search(query, limit), ensure_ascii=False)

    def forget(self, memory_id: str) -> str:
        """Delete one memory from this workspace.

        Args:
            memory_id: Identifier returned by remember or recall.
        """
        with self.database.connect() as db:
            count = db.execute("DELETE FROM memories WHERE id = ? AND workspace = ?",
                               (memory_id, self.workspace)).rowcount
        return f"{count} souvenir supprimé."
