import asyncio
from typing import Type, Optional
from pydantic import BaseModel, Field

from orbit_agent.skills.base import BaseSkill, SkillConfig

class ShellInput(BaseModel):
    command: str = Field(description="Command to execute in the shell")
    cwd: Optional[str] = Field(default=None, description="Working directory")
    timeout_seconds: int = Field(default=30)

class ShellOutput(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    error: str = ""

class ShellCommandSkill(BaseSkill):
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="shell_command",
            description="Executes a shell command. DANGEROUS.",
            permissions_required=["shell_exec"]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return ShellInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return ShellOutput

    async def execute(self, inputs: ShellInput) -> ShellOutput:
        try:
            cwd = inputs.cwd if inputs.cwd else "."
            
            process = await asyncio.create_subprocess_shell(
                inputs.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=inputs.timeout_seconds)
            except asyncio.TimeoutError:
                process.kill()
                return ShellOutput(stdout="", stderr="", exit_code=-1, error="Command timed out")
            
            return ShellOutput(
                stdout=stdout.decode().strip(),
                stderr=stderr.decode().strip(),
                exit_code=process.returncode or 0
            )
        except Exception as e:
            return ShellOutput(stdout="", stderr="", exit_code=-1, error=str(e))
