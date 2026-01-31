from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional


def _now_iso() -> str:
    return datetime.now().isoformat()


@dataclass
class KnownAgent:
    name: str
    notes: str = ""
    tags: List[str] = None  # type: ignore[assignment]
    seen_count: int = 0
    last_seen_at: str = ""
    first_seen_at: str = ""

    def __post_init__(self) -> None:
        if self.tags is None:
            self.tags = []


class MoltbookSocialStore:
    """
    Very small "social memory" for Moltbook agents Orbit has encountered.
    Stored at: data/moltbook/known_agents.json
    """

    def __init__(self, path: str = "data/moltbook/known_agents.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, KnownAgent]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            agents_raw = raw.get("agents") if isinstance(raw, dict) else None
            if not isinstance(agents_raw, dict):
                return {}
            out: Dict[str, KnownAgent] = {}
            for k, v in agents_raw.items():
                if not isinstance(v, dict):
                    continue
                name = str(v.get("name") or k).strip()
                if not name:
                    continue
                out[name] = KnownAgent(
                    name=name,
                    notes=str(v.get("notes") or "").strip(),
                    tags=[str(x) for x in (v.get("tags") or []) if str(x).strip()],
                    seen_count=int(v.get("seen_count") or 0),
                    last_seen_at=str(v.get("last_seen_at") or "").strip(),
                    first_seen_at=str(v.get("first_seen_at") or "").strip(),
                )
            return out
        except Exception:
            return {}

    def save(self, agents: Dict[str, KnownAgent]) -> None:
        payload: Dict[str, Any] = {
            "updated_at": _now_iso(),
            "agents": {name: asdict(a) for name, a in sorted(agents.items(), key=lambda kv: kv[0].lower())},
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def observe(self, name: str, tags: Optional[List[str]] = None) -> None:
        n = (name or "").strip()
        if not n:
            return
        agents = self.load()
        a = agents.get(n) or KnownAgent(name=n)
        if not a.first_seen_at:
            a.first_seen_at = _now_iso()
        a.last_seen_at = _now_iso()
        a.seen_count = int(a.seen_count or 0) + 1
        for t in (tags or []):
            tt = str(t).strip()
            if tt and tt not in a.tags:
                a.tags.append(tt)
        agents[n] = a
        self.save(agents)

    def set_note(self, name: str, notes: str, tags: Optional[List[str]] = None) -> None:
        n = (name or "").strip()
        if not n:
            return
        agents = self.load()
        a = agents.get(n) or KnownAgent(name=n)
        if not a.first_seen_at:
            a.first_seen_at = _now_iso()
        a.last_seen_at = _now_iso()
        if notes is not None:
            a.notes = str(notes).strip()
        if tags is not None:
            cleaned = [str(x).strip() for x in tags if str(x).strip()]
            a.tags = []
            for t in cleaned:
                if t not in a.tags:
                    a.tags.append(t)
        agents[n] = a
        self.save(agents)

    def get(self, name: str) -> Optional[KnownAgent]:
        n = (name or "").strip()
        if not n:
            return None
        return self.load().get(n)

