from datetime import datetime
from enum import Enum
from typing import List, Dict, Any, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field

class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class StepState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

class Artifact(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    content: Optional[str] = None
    path: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class TaskStep(BaseModel):
    id: str = Field(description="Unique identifier for the step (e.g., 'research_api')")
    skill_name: str = Field(description="Name of the skill to execute")
    skill_config: Dict[str, Any] = Field(default_factory=dict, description="Configuration/inputs for the skill")
    
    state: StepState = Field(default=StepState.PENDING)
    output: Optional[Any] = None
    error: Optional[str] = None
    
    retry_count: int = 0
    max_retries: int = 3
    
    dependencies: List[str] = Field(default_factory=list, description="IDs of steps that must complete first")
    
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

class Task(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    goal: str
    
    steps: List[TaskStep] = Field(default_factory=list)
    state: TaskState = Field(default=TaskState.PENDING)
    
    artifacts: List[Artifact] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict, description="Shared context/memory for the task")
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    def get_step(self, step_id: str) -> Optional[TaskStep]:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None
