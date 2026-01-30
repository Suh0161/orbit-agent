import asyncio
from typing import Type, Optional
from pydantic import BaseModel, Field

from orbit_agent.skills.base import BaseSkill, SkillConfig
import os
import re

class ShellInput(BaseModel):
    command: str = Field(description="Command to execute in the shell")
    cwd: Optional[str] = Field(default=None, description="Working directory")
    timeout_seconds: int = Field(default=30)

class ShellOutput(BaseModel):
    success: bool = True
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
            # Extra safety: block obviously destructive commands unless explicitly allowed.
            # This prevents accidents even if permissions are misconfigured somewhere.
            if str(os.environ.get("ORBIT_ALLOW_DANGEROUS_COMMANDS", "")).strip().lower() not in {"1", "true", "yes", "on"}:
                cmd = inputs.command.strip().lower()
                destructive = [
                    r"\b(del|erase)\b",
                    r"\b(rmdir|rd)\b",
                    r"\bformat\b",
                    r"\bdiskpart\b",
                    r"\bbcdedit\b",
                    r"\breg\s+delete\b",
                    r"\bpowershell\b.*\bremove-item\b",
                    r"\brm\b.*\b-rf\b",
                ]
                protected_hint = r"(\\windows\\|\\system32\\|c:\\windows|c:\\system32|program files|programdata)"
                if any(re.search(pat, cmd) for pat in destructive) and re.search(protected_hint, cmd):
                    return ShellOutput(stdout="", stderr="", exit_code=1, error="Blocked potentially destructive command targeting system paths.")

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
                return ShellOutput(success=False, stdout="", stderr="", exit_code=-1, error="Command timed out")
            
            exit_code = process.returncode or 0
            out_s = stdout.decode().strip()
            err_s = stderr.decode().strip()
            success = (exit_code == 0)
            return ShellOutput(
                success=success,
                stdout=out_s,
                stderr=err_s,
                exit_code=exit_code
            )
        except Exception as e:
            return ShellOutput(success=False, stdout="", stderr="", exit_code=-1, error=str(e))
