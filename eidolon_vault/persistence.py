# eidolon_vault/persistence.py
import os
import hashlib
from pathlib import Path
import chromadb
from sqlalchemy import create_engine, text

class EidolonMemory:
    """
    Persistence layer for agentic memory using SQLite and ChromaDB.
    Ensures that long-term 'consciousness' is stored locally.
    """

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        # Step 6: Ensure data/ folder creation
        os.makedirs("data", exist_ok=True)
        
        self.db_path = Path("data") / f"{agent_id}.db"
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

        # Step 2: Ensure ChromaDB collection exists
        self.vector_client = chromadb.PersistentClient(path="data/chroma")
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
        if metadata:
            self.collection.add(
                documents=[text_content], 
                metadatas=[metadata], 
                ids=[mem_id]
            )
        else:
            self.collection.add(
                documents=[text_content], 
                ids=[mem_id]
            )

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
