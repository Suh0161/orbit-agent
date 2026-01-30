from typing import List, Dict, Any, Optional
from pathlib import Path

from orbit_agent.memory.base import MemoryInterface

class LongTermMemory(MemoryInterface):
    def __init__(self, persistence_path: Path, collection_name: str = "orbit_memory"):
        self.path = persistence_path
        self.client = None
        self.collection = None
        
        try:
            import chromadb
            self.client = chromadb.PersistentClient(path=str(persistence_path))
            self.collection = self.client.get_or_create_collection(name=collection_name)
            
            # SANITY CHECK: Test embedding generation (This triggers the ONNX check)
            # If this fails, we must fallback.
            try:
                self.collection.add(
                    documents=["test_sanity_check"],
                    ids=["test_sanity_id"],
                    metadatas=[{"test": "true"}]
                )
                self.collection.delete(ids=["test_sanity_id"])
            except Exception as embed_error:
                print(f"[Warning] ChromaDB initialized but Embedding failed (ONNX?): {embed_error}")
                raise embed_error # Trigger the outer except
                
        except Exception as e:
            print(f"[Warning] LongTermMemory failed to init (ChromaDB error): {e}")
            print("[Warning] Falling back to ephemeral memory.")
            self.client = None # Force fallback
            self.collection = None
            self._fallback_memory = []

    async def add(self, text: str, metadata: Optional[Dict[str, Any]] = None):
        if not self.client:
            self._fallback_memory.append({"content": text, "metadata": metadata or {}})
            return
            
        # We need an ID. Using hash or generating one?
        # We need an ID. Using hash or generating one?
        # Chroma handles ID if we want, or we provide one.
        import uuid
        meta = metadata or {}
        meta["timestamp"] = meta.get("timestamp", "")
        
        self.collection.add(
            documents=[text],
            metadatas=[meta],
            ids=[str(uuid.uuid4())]
        )

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        if not self.client:
            # Simple keyword match fallback
            return [m for m in self._fallback_memory if query.lower() in m["content"].lower()][:limit]

        results = self.collection.query(
            query_texts=[query],
            n_results=limit
        )
        
        # Format results
        # results is a dict of lists
        output = []
        if results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                output.append({
                    "content": doc,
                    "metadata": meta,
                    "distance": results["distances"][0][i] if "distances" in results else None
                })
        return output

    async def clear(self):
        if not self.client:
            self._fallback_memory = []
            return

        try:
            self.client.delete_collection(self.collection.name)
            self.collection = self.client.create_collection(name=self.collection.name)
        except Exception as e:
            print(f"Error clearing memory: {e}") 
