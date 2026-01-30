import json
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, List


@dataclass
class ScheduledJob:
    id: str
    user_id: int
    chat_id: int
    kind: str  # "once", "interval", "daily"
    goal: str
    enabled: bool = True
    created_at: str = ""
    next_run: float = 0.0  # unix timestamp (seconds)
    interval_seconds: Optional[int] = None
    daily_time: Optional[str] = None  # "HH:MM"


class JobStore:
    """
    Tiny JSON-backed scheduler store.
    Keeps jobs in ./data/uplink/jobs.json by default.
    """

    def __init__(self, path: str = "data/uplink/jobs.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, ScheduledJob]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            jobs: Dict[str, ScheduledJob] = {}
            for job_id, raw in data.items():
                jobs[job_id] = ScheduledJob(**raw)
            return jobs
        except Exception:
            return {}

    def save(self, jobs: Dict[str, ScheduledJob]) -> None:
        payload = {job_id: asdict(job) for job_id, job in jobs.items()}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def compute_next_run(job: ScheduledJob, now: Optional[datetime] = None) -> float:
    now_dt = now or datetime.now()

    if job.kind == "interval":
        if not job.interval_seconds:
            return (now_dt + timedelta(minutes=10)).timestamp()
        return (now_dt + timedelta(seconds=job.interval_seconds)).timestamp()

    if job.kind == "daily":
        # daily_time = "HH:MM"
        try:
            hh, mm = (job.daily_time or "09:00").split(":")
            target = now_dt.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            target = now_dt.replace(hour=9, minute=0, second=0, microsecond=0)

        if target <= now_dt:
            target = target + timedelta(days=1)
        return target.timestamp()

    # "once"
    return job.next_run

