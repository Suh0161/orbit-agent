from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel

class Message(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]

class ModelResponse(BaseModel):
    content: str
    usage: Dict[str, Any] = {}

class BaseModelClient(ABC):
    @abstractmethod
    async def generate(self, messages: List[Message], **kwargs) -> ModelResponse:
        pass
