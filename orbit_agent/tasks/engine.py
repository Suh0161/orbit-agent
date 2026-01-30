import json
from pathlib import Path
from typing import Dict, List, Optional
from uuid import UUID
from datetime import datetime
import asyncio

from orbit_agent.tasks.models import Task, TaskStep, TaskState, StepState
from orbit_agent.config.config import OrbitConfig

class TaskEngine:
    def __init__(self, config: OrbitConfig):
        self.config = config
        self.persistence_path = Path(config.memory.path) / "tasks"
        self.persistence_path.mkdir(parents=True, exist_ok=True)
        self._current_task: Optional[Task] = None
    
    def create_task(self, goal: str, steps: List[TaskStep]) -> Task:
        task = Task(goal=goal, steps=steps)
        self.save_task(task)
        self._current_task = task
        return task

    def save_task(self, task: Task) -> None:
        """Save task state to disk for resumability."""
        task.updated_at = datetime.utcnow()
        file_path = self.persistence_path / f"{task.id}.json"
        with open(file_path, "w") as f:
            f.write(task.model_dump_json(indent=2))

    def load_task(self, task_id: UUID) -> Optional[Task]:
        file_path = self.persistence_path / f"{task_id}.json"
        if not file_path.exists():
            return None
        with open(file_path, "r") as f:
            data = json.load(f)
        return Task(**data)

    def get_runnable_steps(self, task: Task) -> List[TaskStep]:
        """
        Identify steps that are PENDING and have all dependencies COMPLETED.
        """
        runnable = []
        completed_ids = {s.id for s in task.steps if s.state == StepState.COMPLETED}
        
        for step in task.steps:
            if step.state in [StepState.PENDING, StepState.FAILED]:
                # If failed, check retries
                if step.state == StepState.FAILED:
                    if step.retry_count >= step.max_retries:
                         continue
                
                # Check dependencies
                if all(dep_id in completed_ids for dep_id in step.dependencies):
                    runnable.append(step)
        
        return runnable

    def update_step_state(self, task: Task, step_id: str, state: StepState, output: Optional[str] = None, error: Optional[str] = None):
        step = task.get_step(step_id)
        if step:
            step.state = state
            if output:
                step.output = output
            if error:
                step.error = error
            if state == StepState.FAILED:
                step.retry_count += 1
            if state == StepState.RUNNING and not step.started_at:
                step.started_at = datetime.utcnow()
            if state in [StepState.COMPLETED, StepState.FAILED]:
                step.completed_at = datetime.utcnow()
            
            self.save_task(task)

    def check_task_completion(self, task: Task) -> bool:
        """Check if all steps are done, update task state."""
        if not task.steps: return False
        
        if all(s.state == StepState.COMPLETED for s in task.steps):
            task.state = TaskState.COMPLETED
            self.save_task(task)
            return True
        
        if any(s.state == StepState.FAILED and s.retry_count >= s.max_retries for s in task.steps):
            task.state = TaskState.FAILED
            self.save_task(task)
            return True
            
        return False

    def add_step(self, task: Task, step: TaskStep) -> None:
        """Dynamically add a step to the task."""
        task.steps.append(step)
        self.save_task(task)
