import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class RunTrace:
    """
    Minimal JSONL trace writer for debugging autonomy issues.
    Writes one JSON object per line (append-only).
    """

    path: Path

    @classmethod
    def for_task(cls, root: Path, name: str) -> "RunTrace":
        root.mkdir(parents=True, exist_ok=True)
        return cls(path=(root / f"{name}.jsonl"))

    def write(self, event: str, data: Optional[Dict[str, Any]] = None) -> None:
        rec: Dict[str, Any] = {
            "ts": datetime.utcnow().isoformat(),
            "event": event,
        }
        if data:
            rec.update(data)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

