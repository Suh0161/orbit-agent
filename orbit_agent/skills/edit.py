from pathlib import Path
from typing import Type
from pydantic import BaseModel, Field
import aiofiles
import os

from orbit_agent.skills.base import BaseSkill, SkillConfig

class FileEditInput(BaseModel):
    path: str = Field(description="Absolute path to the file to edit")
    target_text: str = Field(description="Exact text block to replace. Must match exactly.")
    replacement_text: str = Field(description="New text to insert.")

class FileEditOutput(BaseModel):
    success: bool
    path: str
    diff: str = ""
    error: str = ""

class FileEditSkill(BaseSkill):
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="file_edit",
            description="Replaces a specific block of text in a file with new content.",
            permissions_required=["file_write"]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return FileEditInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return FileEditOutput

    async def execute(self, inputs: FileEditInput) -> FileEditOutput:
        path = Path(inputs.path)

        if not path.is_absolute():
            return FileEditOutput(success=False, path=inputs.path, error="Path must be absolute")

        # Refuse edits in protected system locations
        try:
            p = path.resolve()
        except Exception:
            p = path

        protected_roots = [
            Path(os.environ.get("SystemRoot", r"C:\Windows")),
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        ]
        for root in protected_roots:
            try:
                if p.is_relative_to(root.resolve()):
                    return FileEditOutput(success=False, path=inputs.path, error=f"Refusing to edit protected system path: {root}")
            except Exception:
                if str(p).lower().startswith(str(root).lower()):
                    return FileEditOutput(success=False, path=inputs.path, error=f"Refusing to edit protected system path: {root}")
        
        if not path.exists():
            return FileEditOutput(success=False, path=inputs.path, error="File not found")

        try:
            async with aiofiles.open(path, mode='r', encoding='utf-8') as f:
                content = await f.read()

            if inputs.target_text not in content:
                # Try simple normalization (strip whitespace)
                if inputs.target_text.strip() in content:
                     inputs.target_text = inputs.target_text.strip()
                else:
                    return FileEditOutput(success=False, path=str(path), error="Target text not found in file.")

            new_content = content.replace(inputs.target_text, inputs.replacement_text)
            
            async with aiofiles.open(path, mode='w', encoding='utf-8') as f:
                await f.write(new_content)
                
            return FileEditOutput(success=True, path=str(path), diff="Updated.")
        except Exception as e:
            return FileEditOutput(success=False, path=str(path), error=str(e))
