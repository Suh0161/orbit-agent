from typing import List, Dict, Any, Optional
from orbit_agent.memory.base import MemoryInterface

class ShortTermMemory(MemoryInterface):
    def __init__(self):
        self.storage: List[Dict[str, Any]] = []

    async def add(self, text: str, metadata: Optional[Dict[str, Any]] = None):
        self.storage.append({
            "content": text,
            "metadata": metadata or {}
        })

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        # Simple keyword match
        results = []
        for item in self.storage:
            if query.lower() in item["content"].lower():
                results.append(item)
            if len(results) >= limit:
                break
        return results

    async def clear(self):
        self.storage = []
    
    def get_all(self) -> List[Dict[str, Any]]:
        return self.storage
