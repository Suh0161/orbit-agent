from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class UserProfile:
    """
    Minimal per-user persona/profile for Uplink.
    Stored locally and injected into prompts to improve continuity.
    """

    preferred_name: str = ""
    persona: str = ""  # e.g. "direct, concise, no emojis"
    timezone: str = ""  # e.g. "Asia/Kuala_Lumpur"
    default_location: str = ""  # e.g. "Kuala Lumpur"
    default_airport: str = ""  # e.g. "KUL"
    notes: str = ""  # free-form user preferences
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "UserProfile":
        try:
            return cls(**{k: raw.get(k) for k in cls().__dict__.keys()})
        except Exception:
            # Best-effort: accept partial/unknown fields.
            p = cls()
            for k in p.__dict__.keys():
                if k in raw:
                    setattr(p, k, raw.get(k) or "")
            return p

    def touch(self) -> None:
        now = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = now
        self.updated_at = now


class ProfileStore:
    """
    Tiny JSON-backed profile store.
    Keys are channel-qualified (e.g. 'telegram:123456').
    """

    def __init__(self, path: str = "data/uplink/profiles.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, UserProfile]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            out: Dict[str, UserProfile] = {}
            for k, v in (raw or {}).items():
                if isinstance(v, dict):
                    out[str(k)] = UserProfile.from_dict(v)
            return out
        except Exception:
            return {}

    def save(self, profiles: Dict[str, UserProfile]) -> None:
        payload = {k: asdict(p) for k, p in profiles.items()}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

