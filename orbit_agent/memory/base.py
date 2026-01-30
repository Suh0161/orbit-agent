from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class MemoryInterface(ABC):
    @abstractmethod
    async def add(self, text: str, metadata: Optional[Dict[str, Any]] = None):
        """Add an item to memory."""
        pass

    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search memory for relevant items."""
        pass
    
    @abstractmethod
    async def clear(self):
        """Clear the memory."""
        pass
