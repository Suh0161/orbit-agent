import json
import os
from pathlib import Path
from typing import Dict, Optional, List

# A simple JSON-based persistent cache for UI element locations
class UICache:
    def __init__(self, cache_file: str = "data/memory/ui_cache.json"):
        self.cache_file = Path(cache_file)
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache: Dict[str, List[int]] = self._load_cache()

    def _load_cache(self) -> Dict[str, List[int]]:
        if not self.cache_file.exists():
            return {}
        try:
            with open(self.cache_file, "r") as f:
                return json.load(f)
        except:
            return {}

    def _save_cache(self):
        with open(self.cache_file, "w") as f:
            json.dump(self.cache, f, indent=2)

    def get(self, key: str) -> Optional[List[int]]:
        """Retrieve coordinates for a description key."""
        # Clean key to be generic? For now exact match
        return self.cache.get(key)

    def set(self, key: str, coords: List[int]):
        """Save coordinates."""
        self.cache[key] = coords
        self._save_cache()
