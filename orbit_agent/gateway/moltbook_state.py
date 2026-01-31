from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class MoltbookState:
    last_check_ts: float = 0.0
    last_post_ts: float = 0.0
    last_version_check_ts: float = 0.0
    updated_at: str = ""

    def touch(self) -> None:
        self.updated_at = datetime.now().isoformat()


class MoltbookStateStore:
    def __init__(self, path: str = "data/moltbook/state.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> MoltbookState:
        if not self.path.exists():
            st = MoltbookState()
            st.touch()
            return st
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            st = MoltbookState(
                last_check_ts=float(raw.get("last_check_ts") or 0.0),
                last_post_ts=float(raw.get("last_post_ts") or 0.0),
                last_version_check_ts=float(raw.get("last_version_check_ts") or 0.0),
                updated_at=str(raw.get("updated_at") or ""),
            )
            st.touch()
            return st
        except Exception:
            st = MoltbookState()
            st.touch()
            return st

    def save(self, st: MoltbookState) -> None:
        st.touch()
        self.path.write_text(json.dumps(asdict(st), indent=2, ensure_ascii=False), encoding="utf-8")

