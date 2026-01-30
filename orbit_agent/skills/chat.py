
from typing import Type
from pydantic import BaseModel, Field
from orbit_agent.skills.base import BaseSkill, SkillConfig

class ChatInput(BaseModel):
    text: str = Field(..., description="The text message to send to the user.")

class ChatOutput(BaseModel):
    success: bool
    message: str

class ChatSkill(BaseSkill):
    """
    Allows the agent to send a direct message to the user.
    Useful for reporting findings, asking clarifying questions, or confirming completion.
    """
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="chat",
            description="Send a text message to the user/Uplink. Use this to report your final findings or ask questions.",
            permissions_required=[]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return ChatInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return ChatOutput

    async def execute(self, inputs: ChatInput) -> ChatOutput:
        # This skill just returns the text. The Uplink (Telegram Bot) will see this output and send it.
        # We wrap it in a special prefix so the Bot knows it's a chat message, not just a log.
        return ChatOutput(success=True, message=f"[CHAT] {inputs.text}")
