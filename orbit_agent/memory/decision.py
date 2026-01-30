import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
import aiofiles

from orbit_agent.memory.base import MemoryInterface

class DecisionLog(MemoryInterface):
    """
    Appends decisions to a JSONL file. 
    Strictly chronological, useful for audit and context.
    """
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    async def add(self, text: str, metadata: Optional[Dict[str, Any]] = None):
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "content": text,
            "metadata": metadata or {}
        }
        async with aiofiles.open(self.log_path, mode="a", encoding="utf-8") as f:
            await f.write(json.dumps(entry) + "\n")

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        # Naive linear search for now, reversing to get most recent
        results = []
        if not self.log_path.exists():
            return results
        
        async with aiofiles.open(self.log_path, mode="r", encoding="utf-8") as f:
            lines = await f.readlines()
            
        for line in reversed(lines):
            if len(results) >= limit:
                break
            try:
                entry = json.loads(line)
                if query.lower() in entry["content"].lower():
                    results.append(entry)
            except:
                continue
        return results

    async def clear(self):
        if self.log_path.exists():
            self.log_path.unlink()
