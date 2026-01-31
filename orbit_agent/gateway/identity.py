from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class GatewayIdentity:
    persona: str = ""  # operating style / rules
    goals: List[str] = None  # long-term goals
    created_at: str = ""
    updated_at: str = ""

    def touch(self) -> None:
        now = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = now
        self.updated_at = now
        if self.goals is None:
            self.goals = []


@dataclass
class WorkingMemory:
    last_context_hash: str = ""
    last_summary: str = ""
    last_sent_by_chat: Dict[str, float] = None
    updated_at: str = ""

    def touch(self) -> None:
        self.updated_at = datetime.now().isoformat()
        if self.last_sent_by_chat is None:
            self.last_sent_by_chat = {}


def hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


class IdentityStore:
    def __init__(self, path: str = "data/gateway/identity.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> GatewayIdentity:
        if not self.path.exists():
            ident = GatewayIdentity(persona="", goals=[])
            ident.touch()
            return ident
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            ident = GatewayIdentity(
                persona=str(raw.get("persona") or ""),
                goals=list(raw.get("goals") or []),
                created_at=str(raw.get("created_at") or ""),
                updated_at=str(raw.get("updated_at") or ""),
            )
            ident.touch()
            return ident
        except Exception:
            ident = GatewayIdentity(persona="", goals=[])
            ident.touch()
            return ident

    def save(self, ident: GatewayIdentity) -> None:
        ident.touch()
        self.path.write_text(json.dumps(asdict(ident), indent=2, ensure_ascii=False), encoding="utf-8")


class WorkingMemoryStore:
    def __init__(self, path: str = "data/gateway/working_memory.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> WorkingMemory:
        if not self.path.exists():
            wm = WorkingMemory(last_sent_by_chat={})
            wm.touch()
            return wm
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            wm = WorkingMemory(
                last_context_hash=str(raw.get("last_context_hash") or ""),
                last_summary=str(raw.get("last_summary") or ""),
                last_sent_by_chat=dict(raw.get("last_sent_by_chat") or {}),
                updated_at=str(raw.get("updated_at") or ""),
            )
            wm.touch()
            return wm
        except Exception:
            wm = WorkingMemory(last_sent_by_chat={})
            wm.touch()
            return wm

    def save(self, wm: WorkingMemory) -> None:
        wm.touch()
        self.path.write_text(json.dumps(asdict(wm), indent=2, ensure_ascii=False), encoding="utf-8")

