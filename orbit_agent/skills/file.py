from pathlib import Path
from typing import Type
from pydantic import BaseModel, Field
import aiofiles

from orbit_agent.skills.base import BaseSkill, SkillConfig

class FileReadInput(BaseModel):
    path: str = Field(description="Absolute path to the file to read")

class FileReadOutput(BaseModel):
    content: str
    error: str = ""

class FileReadSkill(BaseSkill):
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="file_read",
            description="Reads the content of a file.",
            permissions_required=["file_read"]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return FileReadInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return FileReadOutput

    async def execute(self, inputs: FileReadInput) -> FileReadOutput:
        path = Path(inputs.path)
        if not path.is_absolute():
            # Security check: basic constraint, though config might allow relative to workspace
            return FileReadOutput(content="", error="Path must be absolute")
        
        if not path.exists():
            return FileReadOutput(content="", error=f"File not found: {inputs.path}")

        try:
            async with aiofiles.open(path, mode='r', encoding='utf-8') as f:
                content = await f.read()
            return FileReadOutput(content=content)
        except Exception as e:
            return FileReadOutput(content="", error=str(e))


class FileWriteInput(BaseModel):
    path: str = Field(description="Absolute path to the file to write")
    content: str = Field(description="Content to write")
    overwrite: bool = Field(default=False, description="Whether to overwrite if exists")

class FileWriteOutput(BaseModel):
    success: bool
    path: str
    error: str = ""

class FileWriteSkill(BaseSkill):
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="file_write",
            description="Writes content to a file.",
            permissions_required=["file_write"]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return FileWriteInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return FileWriteOutput

    async def execute(self, inputs: FileWriteInput) -> FileWriteOutput:
        path = Path(inputs.path)
        
        if path.exists() and not inputs.overwrite:
            return FileWriteOutput(success=False, path=inputs.path, error="File exists and overwrite is False")
            
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(path, mode='w', encoding='utf-8') as f:
                await f.write(inputs.content)
            return FileWriteOutput(success=True, path=str(path))
        except Exception as e:
            return FileWriteOutput(success=False, path=inputs.path, error=str(e))
