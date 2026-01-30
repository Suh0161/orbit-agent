from pathlib import Path
from typing import Dict, Type
from pydantic import BaseModel, Field
import aiofiles
from orbit_agent.skills.base import BaseSkill, SkillConfig

class CodeScaffoldInput(BaseModel):
    base_path: str = Field(description="Root directory for the scaffold")
    structure: Dict[str, str] = Field(
        description="Map of 'relative/path' to 'file content'. Use empty content for folder creation only (if implied)."
    )

class CodeScaffoldOutput(BaseModel):
    created_files: list[str]
    error: str = ""

class CodeScaffoldSkill(BaseSkill):
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="code_scaffold",
            description="Creates a directory structure with file contents.",
            permissions_required=["file_write"]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return CodeScaffoldInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return CodeScaffoldOutput

    async def execute(self, inputs: CodeScaffoldInput) -> CodeScaffoldOutput:
        base = Path(inputs.base_path)
        created = []
        try:
            for rel_path, content in inputs.structure.items():
                full_path = base / rel_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                
                if content:
                    async with aiofiles.open(full_path, mode='w', encoding='utf-8') as f:
                        await f.write(content)
                else:
                    # Just ensure directory exists? Or create empty file?
                    # Assuming empty string means empty file.
                    async with aiofiles.open(full_path, mode='w', encoding='utf-8') as f:
                        pass
                
                created.append(str(full_path))
                
            return CodeScaffoldOutput(created_files=created)
        except Exception as e:
            return CodeScaffoldOutput(created_files=created, error=str(e))
