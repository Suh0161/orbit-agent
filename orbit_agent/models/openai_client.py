import os
from typing import List, Dict, Any
from orbit_agent.models.base import BaseModelClient, Message, ModelResponse
from openai import AsyncOpenAI

class OpenAIClient(BaseModelClient):
    def __init__(self, api_key: str, model_name: str = "gpt-4-turbo", base_url: str = None):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model_name = model_name

    async def generate(self, messages: List[Message], **kwargs) -> ModelResponse:
        msgs = [{"role": m.role, "content": m.content} for m in messages]
        try:
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=msgs,
                **kwargs
            )
            content = response.choices[0].message.content
            usage = response.usage.model_dump()
            return ModelResponse(content=content, usage=usage)
        except Exception as e:
            # Fallback or re-raise
            raise e
