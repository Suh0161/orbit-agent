import os
import re
from pathlib import Path
from typing import Type, Optional, Any, List

from pydantic import BaseModel, Field

from orbit_agent.skills.base import BaseSkill, SkillConfig
from orbit_agent.models.openai_client import OpenAIClient
from orbit_agent.models.base import Message


class SkillCreateInput(BaseModel):
    name: str = Field(description="New skill name (snake_case). Example: 'todoist_skill'.")
    description: str = Field(description="What the skill should do, in plain English.")
    permissions_required: List[str] = Field(default_factory=list, description="Permissions this skill will require.")
    generate_with_llm: bool = Field(default=True, description="Generate the skill code with the LLM.")
    auto_load: bool = Field(default=True, description="Hot-load the created skill immediately (no restart).")
    overwrite: bool = Field(default=False, description="Allow overwriting an existing skill file.")


class SkillCreateOutput(BaseModel):
    success: bool
    skill_name: Optional[str] = None
    file_path: Optional[str] = None
    error: Optional[str] = None
    note: Optional[str] = None


class SkillCreateSkill(BaseSkill):
    """
    Create and hot-load a new skill at runtime (no restart).

    Safety model:
    - Disabled by default unless ORBIT_ENABLE_SKILL_CREATE=1.
    - Writes only to ./data/skills/<name>.py (never to system paths).
    - Auto-load is additionally gated by ORBIT_ENABLE_SKILL_AUTOLOAD=1.
    """

    def __init__(self, registry: Any, orbit_config: Any = None):
        super().__init__()
        self.registry = registry
        # IMPORTANT: BaseSkill.config is a SkillConfig. Don't overwrite it.
        self.orbit_config = orbit_config

    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="skill_create",
            description="Creates a new Orbit skill (Python) and hot-loads it without restart (if enabled).",
            permissions_required=[],
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return SkillCreateInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return SkillCreateOutput

    def _enabled(self, var: str) -> bool:
        return str(os.environ.get(var, "")).strip().lower() in {"1", "true", "yes", "on"}

    def _sanitize_name(self, name: str) -> Optional[str]:
        n = name.strip().lower().replace("-", "_").replace(" ", "_")
        if not re.fullmatch(r"[a-z_][a-z0-9_]{2,60}", n):
            return None
        return n

    def _skill_dir(self) -> Path:
        # Keep runtime-created skills out of the package code by default.
        base = Path.cwd() / "data" / "skills"
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _get_api_key(self) -> Optional[str]:
        # Prefer configured env-var name if available
        env_name = None
        if self.orbit_config and getattr(self.orbit_config, "model", None):
            env_name = getattr(self.orbit_config.model, "api_key_env_var", None)
        if env_name:
            v = os.environ.get(env_name)
            if v:
                return v
            # Some configs may store the raw key directly
            if env_name.startswith("sk-") and len(env_name) > 20:
                return env_name
        return os.environ.get("OPENAI_API_KEY")

    def _get_model_name(self) -> str:
        if self.orbit_config and getattr(self.orbit_config, "model", None):
            return getattr(self.orbit_config.model, "model_name", "gpt-5.1") or "gpt-5.1"
        return "gpt-5.1"

    async def _generate_code(self, name: str, description: str, permissions_required: List[str]) -> str:
        api_key = self._get_api_key()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set (required for generate_with_llm).")

        client = OpenAIClient(api_key=api_key, model_name=self._get_model_name())
        class_name = "".join([part.capitalize() for part in name.split("_")]) + "Skill"

        system = (
            "You are writing a Python plugin for Orbit Agent.\n"
            "Return ONLY the Python file contents (no markdown).\n"
            "Requirements:\n"
            "- Define exactly ONE BaseSkill subclass.\n"
            "- The skill must be safe by default (no destructive shell/file operations).\n"
            "- The class must be named: " + class_name + "\n"
            "- default_config.name must be: " + name + "\n"
            "- input/output schemas must be Pydantic models.\n"
            "- __init__ must be no-arg and call super().__init__().\n"
            "- Implement execute() and return success flags.\n"
            "- Do NOT use relative imports.\n"
        )

        user = (
            f"Skill description: {description}\n"
            f"permissions_required: {permissions_required}\n"
            "Implement the skill. If external integration would be required, stub it safely and explain in the output.\n"
        )

        resp = await client.generate(
            [Message(role="system", content=system), Message(role="user", content=user)],
            temperature=0.1,
        )
        return resp.content.strip()

    async def execute(self, inputs: SkillCreateInput) -> SkillCreateOutput:
        try:
            if not self._enabled("ORBIT_ENABLE_SKILL_CREATE"):
                return SkillCreateOutput(
                    success=False,
                    error="Skill creation disabled. Set ORBIT_ENABLE_SKILL_CREATE=1 to enable.",
                )

            clean = self._sanitize_name(inputs.name)
            if not clean:
                return SkillCreateOutput(success=False, error="Invalid skill name. Use snake_case (3-60 chars).")

            skill_dir = self._skill_dir()
            file_path = skill_dir / f"{clean}.py"

            if file_path.exists() and not inputs.overwrite:
                return SkillCreateOutput(success=False, error=f"Skill file already exists: {file_path}")

            if inputs.generate_with_llm:
                code = await self._generate_code(clean, inputs.description, inputs.permissions_required)
            else:
                # Minimal safe stub
                code = f'''from typing import Type, Optional\nfrom pydantic import BaseModel, Field\nfrom orbit_agent.skills.base import BaseSkill, SkillConfig\n\n\nclass Input(BaseModel):\n    text: str = Field(..., description="Input text")\n\n\nclass Output(BaseModel):\n    success: bool\n    data: str = ""\n    error: str = ""\n\n\nclass {''.join([p.capitalize() for p in clean.split('_')])}Skill(BaseSkill):\n    @property\n    def default_config(self) -> SkillConfig:\n        return SkillConfig(name="{clean}", description="{inputs.description}", permissions_required={inputs.permissions_required})\n\n    @property\n    def input_schema(self) -> Type[BaseModel]:\n        return Input\n\n    @property\n    def output_schema(self) -> Type[BaseModel]:\n        return Output\n\n    async def execute(self, inputs: Input) -> Output:\n        return Output(success=True, data=f"Stub skill received: {inputs.text}")\n'''

            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(code, encoding="utf-8")

            loaded_name = None
            if inputs.auto_load:
                if not self._enabled("ORBIT_ENABLE_SKILL_AUTOLOAD"):
                    return SkillCreateOutput(
                        success=True,
                        file_path=str(file_path),
                        note="Skill file created. Auto-load is disabled; set ORBIT_ENABLE_SKILL_AUTOLOAD=1 to hot-load.",
                    )
                loaded_name = self.registry.register_skill_from_file(str(file_path))

            return SkillCreateOutput(
                success=True,
                skill_name=loaded_name,
                file_path=str(file_path),
                note="Skill created" + (" and loaded." if loaded_name else "."),
            )

        except Exception as e:
            return SkillCreateOutput(success=False, error=str(e))

