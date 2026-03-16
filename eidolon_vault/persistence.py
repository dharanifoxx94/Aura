# eidolon_vault/persistence.py
import os
import hashlib
from pathlib import Path
import chromadb
from chromadb.config import Settings
from sqlalchemy import create_engine, text
from chromadb.errors import InvalidArgumentError

_DEFAULT_DATA_DIR = Path.home() / ".eidolon_vault" / "legacy_memory"

class EidolonMemory:
    """
    Persistence layer for agentic memory using SQLite and ChromaDB.
    Ensures that long-term 'consciousness' is stored locally.
    """

    def __init__(self, agent_id: str, data_dir: Path | None = None):
        self.agent_id = agent_id
        base = data_dir or _DEFAULT_DATA_DIR
        base.mkdir(parents=True, exist_ok=True)

        self.db_path = base / f"{agent_id}.db"
        self.engine = create_engine(f"sqlite:///{self.db_path}")

        # Step 2: Ensure table exists
        with self.engine.connect() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS memories ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "content TEXT, "
                "timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"
            ))
            conn.commit()

        # Step 2: Ensure ChromaDB collection exists (Optimized for old hardware)
        chroma_path = str(base / "chroma")
        self.vector_client = chromadb.PersistentClient(
            path=chroma_path,
            settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.vector_client.get_or_create_collection(name=agent_id)

    def save_memory(self, text_content: str, metadata: dict = None):
        """
        Store a memory entry in both SQLite and ChromaDB.
        """
        # Save to SQLite
        with self.engine.connect() as conn:
            conn.execute(
                text("INSERT INTO memories (content) VALUES (:text)"),
                {"text": text_content}
            )
            conn.commit()

        # Save to ChromaDB for vector retrieval
        mem_id = hashlib.md5(text_content.encode("utf-8")).hexdigest()
        add_kwargs = {
            "documents": [text_content],
            "ids": [mem_id]
        }
        if metadata:
            add_kwargs["metadatas"] = [metadata]
        self.collection.add(**add_kwargs)

    def get_recent_memories(self, limit: int = 20):
        """
        Retrieve recent memory entries from SQLite.
        """
        with self.engine.connect() as conn:
            result = conn.execute(
                text("SELECT content FROM memories ORDER BY timestamp DESC LIMIT :limit"),
                {"limit": limit}
            )
            return [row[0] for row in result]

    def search_memories(self, query_text: str, n_results: int = 5) -> list:
        """
        Retrieve semantically relevant memories from ChromaDB.
        """
        count = self.collection.count()
        if count == 0:
            return []
        safe_n = min(n_results, count)
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=safe_n
            )
            return results["documents"][0] if results["documents"] else []
        except InvalidArgumentError:
            return []
