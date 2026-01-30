import json
import os
from pathlib import Path
from typing import List, Optional, Dict, Any
from orbit_agent.tasks.models import TaskStep
from pydantic import BaseModel

class Routine(BaseModel):
    goal: str
    steps: List[Dict[str, Any]]
    run_count: int = 1
    avg_duration: float = 0.0

class RoutineMemory:
    def __init__(self, memory_path: Path):
        self.path = memory_path / "routines.json"
        self._data: Dict[str, Routine] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r") as f:
                    raw = json.load(f)
                    for k, v in raw.items():
                        self._data[k] = Routine(**v)
            except Exception as e:
                print(f"[RoutineMemory] Failed to load: {e}")

    def _save(self):
        keys = list(self._data.keys())
        dump = {k: self._data[k].model_dump() for k in keys}
        try:
            with open(self.path, "w") as f:
                json.dump(dump, f, indent=2)
        except Exception as e:
             print(f"[RoutineMemory] Failed to save: {e}")

    def get_plan(self, goal: str) -> Optional[List[TaskStep]]:
        # Simple exact match for now. Fuzzy matching could be added later.
        # Normalize goal?
        key = goal.strip().lower()
        if key in self._data:
            print(f"[Muscle Memory] Recall: Found routine for '{goal}'")
            routine = self._data[key]
            # Convert dicts back to TaskStep objects
            # We must regenerate IDs to ensure uniqueness in new task
            # But keep structure
            steps = []
            from uuid import uuid4
            
            # Map old IDs to New IDs to preserve dependencies
            id_map = {}
            
            for s_dict in routine.steps:
                old_id = s_dict["id"]
                new_id = str(uuid4())[:8]
                id_map[old_id] = new_id
                
                # Fix dependencies
                new_deps = [id_map.get(d, d) for d in s_dict["dependencies"]]
                
                step = TaskStep(
                    id=new_id,
                    skill_name=s_dict["skill_name"],
                    skill_config=s_dict["skill_config"],
                    dependencies=new_deps
                )
                steps.append(step)
            return steps
        return None

    def save_routine(self, goal: str, steps: List[TaskStep]):
        key = goal.strip().lower()
        
        # Convert steps to serializable dicts
        # We only save the definition, not the runtime state (output, error, etc)
        # So we construct a clean list
        clean_steps = []
        for s in steps:
            clean_steps.append({
                "id": s.id, # Keep original ID structure for relative dependecies in template
                "skill_name": s.skill_name,
                "skill_config": s.skill_config,
                "dependencies": s.dependencies
            })
            
        if key in self._data:
            self._data[key].run_count += 1
            self._data[key].steps = clean_steps # Update with latest version? Yes, self-healing might have improved it.
        else:
            self._data[key] = Routine(goal=goal, steps=clean_steps)
            
        self._save()
        print(f"[Muscle Memory] Learned routine for: {goal}")
