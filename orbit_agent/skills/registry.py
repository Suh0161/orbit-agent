from typing import Dict, Type, Any, Optional
import importlib.util
import inspect
import sys
import time
from pathlib import Path
from orbit_agent.config.config import OrbitConfig
from orbit_agent.skills.base import BaseSkill
from orbit_agent.skills.file import FileReadSkill, FileWriteSkill
from orbit_agent.skills.shell import ShellCommandSkill

class SkillRegistry:
    def __init__(self, config: Optional[OrbitConfig] = None):
        self.config = config
        self._skills: Dict[str, BaseSkill] = {}
        self._register_defaults()

    def register_skill_from_file(
        self,
        file_path: str,
        class_name: Optional[str] = None,
        init_kwargs: Optional[dict] = None,
        module_name: Optional[str] = None,
    ) -> str:
        """
        Dynamically load a Python file defining a BaseSkill subclass and register it immediately.
        Enables hot-loading new skills WITHOUT restarting the agent.
        """
        p = Path(file_path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(str(p))

        # Use a unique module name to avoid caching & class-identity issues.
        if not module_name:
            module_name = f"orbit_dynamic_skill_{p.stem}_{int(time.time() * 1000)}"

        spec = importlib.util.spec_from_file_location(module_name, str(p))
        if spec is None or spec.loader is None:
            raise ImportError(f"Failed to create import spec for {p}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore[attr-defined]

        # Find a BaseSkill subclass to instantiate
        skill_cls = None
        if class_name:
            skill_cls = getattr(module, class_name, None)
        else:
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if obj is BaseSkill:
                    continue
                try:
                    if issubclass(obj, BaseSkill) and obj.__module__ == module.__name__:
                        skill_cls = obj
                        break
                except Exception:
                    continue

        if skill_cls is None:
            raise ValueError(f"No BaseSkill subclass found in {p} (class_name={class_name})")

        kwargs = init_kwargs or {}
        skill: BaseSkill = skill_cls(**kwargs) if kwargs else skill_cls()
        self.register_skill(skill)
        return skill.config.name

    def _register_defaults(self):
        self.register_skill(FileReadSkill())
        self.register_skill(FileWriteSkill())
        self.register_skill(ShellCommandSkill())
        from orbit_agent.skills.desktop import DesktopSkill
        self.register_skill(DesktopSkill())
        from orbit_agent.skills.coding import CodeScaffoldSkill
        from orbit_agent.skills.web_search import WebSearchSkill
        from orbit_agent.skills.web_browse import WebBrowseSkill
        from orbit_agent.skills.code_analysis import CodeAnalysisSkill
        from orbit_agent.skills.browser import BrowserSkill
        from orbit_agent.skills.apps import AppControlSkill
        from orbit_agent.skills.edit import FileEditSkill

        self.register_skill(CodeScaffoldSkill())
        self.register_skill(WebSearchSkill())
        self.register_skill(WebBrowseSkill())
        self.register_skill(CodeAnalysisSkill())
        self.register_skill(BrowserSkill())
        self.register_skill(AppControlSkill())
        self.register_skill(FileEditSkill())
        
        # Vision Skill
        if self.config:
            # Try to get API Key
            api_key = self.config.model.api_key_env_var 
            # Note: config.model.api_key_env_var holds the VALUE if we fixed it in main, or env var name?
            # In cli/main.py we patched it to hold the value if it started with sk-.
            # But safer to check env?
            # Actually, let's just pass what's in the config object.
            
            import os
            key = os.environ.get("OPENAI_API_KEY") 
            if not key and self.config.model.api_key_env_var and self.config.model.api_key_env_var.startswith("sk-"):
                 key = self.config.model.api_key_env_var
            
            if key:
                from orbit_agent.skills.vision import VisionSkill
                from orbit_agent.skills.visual_interaction import VisualInteractionSkill
                from orbit_agent.skills.som_vision import SoMVisionSkill
                
                model_name = (self.config.model.model_name if self.config else "gpt-5.1")
                vision_skill = VisionSkill(key, model_name=model_name)
                self.register_skill(vision_skill)
                self.register_skill(VisualInteractionSkill(vision_skill))
                
                # Register Set-of-Mark Vision (precision clicking)
                self.register_skill(SoMVisionSkill(key, model_name=model_name))
        
        # Structured Edit (line-based editing)
        from orbit_agent.skills.structured_edit import StructuredEditSkill
        self.register_skill(StructuredEditSkill())
        
        # Codebase Search
        from orbit_agent.skills.code_search import CodeSearchSkill
        self.register_skill(CodeSearchSkill())
        
        # Chat Skill (Agent Voice)
        from orbit_agent.skills.chat import ChatSkill
        self.register_skill(ChatSkill())

        # Self-extension (hot-loaded skills)
        from orbit_agent.skills.skill_create import SkillCreateSkill
        self.register_skill(SkillCreateSkill(self, self.config))

    def register_skill(self, skill: BaseSkill):
        self._skills[skill.config.name] = skill

    def get_skill(self, name: str) -> BaseSkill:
        if name not in self._skills:
            raise ValueError(f"Skill '{name}' not found")
        return self._skills[name]
    
    def list_skills(self) -> Dict[str, Any]:
        info = {}
        for name, skill in self._skills.items():
            schema = skill.input_schema.model_json_schema()
            # Simplify schema for prompt
            props = {k: v.get("description", "") for k, v in schema.get("properties", {}).items()}
            required = schema.get("required", [])
            info[name] = {
                "description": skill.config.description,
                "arguments": props,
                "required": required
            }
        return info
