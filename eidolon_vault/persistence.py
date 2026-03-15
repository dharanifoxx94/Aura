# eidolon_vault/persistence.py
import chromadb
from sqlalchemy import create_engine, text
from pathlib import Path

class EidolonMemory:
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.db_path = Path("data") / f"{agent_id}.db"
        self.db_path.parent.mkdir(exist_ok=True)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        # Initialize the database table if it doesn't exist
        with self.engine.connect() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"))
            conn.commit()

        self.vector_client = chromadb.PersistentClient(path="data/chroma")
        self.collection = self.vector_client.get_or_create_collection(name=agent_id)

    def save_memory(self, text_content: str, metadata: dict = None):
        import hashlib
        mem_id = hashlib.md5(text_content.encode()).hexdigest()
        with self.engine.connect() as conn:
            conn.execute(text("INSERT INTO memories (content) VALUES (:text)"),
                        {"text": text_content})
            conn.commit()
        if metadata:
            self.collection.add(documents=[text_content], metadatas=[metadata], ids=[mem_id])
        else:
             self.collection.add(documents=[text_content], ids=[mem_id])

    def get_recent_memories(self, limit: int = 20):
        with self.engine.connect() as conn:
            result = conn.execute(text("SELECT content FROM memories ORDER BY timestamp DESC LIMIT :limit"),
                                 {"limit": limit})
            return [row[0] for row in result]
