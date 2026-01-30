from abc import ABC, abstractmethod
from typing import Dict, Any, Type, Optional
from pydantic import BaseModel

class SkillConfig(BaseModel):
    name: str
    description: str
    version: str = "1.0.0"
    permissions_required: list[str] = []

class BaseSkill(ABC):
    """
    Abstract base class for all skills.
    """
    def __init__(self, config: Optional[SkillConfig] = None):
        self.config = config or self.default_config

    @property
    @abstractmethod
    def default_config(self) -> SkillConfig:
        pass

    @property
    @abstractmethod
    def input_schema(self) -> Type[BaseModel]:
        """Pydantic model for input validation"""
        pass

    @property
    @abstractmethod
    def output_schema(self) -> Type[BaseModel]:
        """Pydantic model for output validation"""
        pass

    @abstractmethod
    async def execute(self, inputs: BaseModel) -> BaseModel:
        """
        Execute the skill.
        :param inputs: Validated input model
        :return: Validated output model
        """
        pass
