from typing import Type
from pydantic import BaseModel, Field
from AppOpener import open as app_open, close as app_close
from orbit_agent.skills.base import BaseSkill, SkillConfig

class AppInput(BaseModel):
    action: str = Field(..., description="'open' or 'close'")
    app_name: str = Field(..., description="Name of the application (e.g. 'spotify', 'chrome').")

class AppOutput(BaseModel):
    success: bool
    message: str

class AppControlSkill(BaseSkill):
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="app_control",
            description="Open or close local applications.",
            permissions_required=["app_control"]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return AppInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return AppOutput

    async def execute(self, inputs: AppInput) -> AppOutput:
        try:
            print(f"[AppSkill] {inputs.action.upper()} {inputs.app_name}")
            if inputs.action == "open":
                app_open(inputs.app_name, match_closest=True, output=False)
                return AppOutput(success=True, message=f"Opened {inputs.app_name}")
            elif inputs.action == "close":
                app_close(inputs.app_name, match_closest=True, output=False)
                return AppOutput(success=True, message=f"Closed {inputs.app_name}")
            else:
                return AppOutput(success=False, message=f"Unknown action: {inputs.action}")
        except Exception as e:
            return AppOutput(success=False, message=f"Error: {e}")
