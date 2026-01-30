from typing import Dict, Type, Any, Optional
from orbit_agent.config.config import OrbitConfig
from orbit_agent.skills.base import BaseSkill
from orbit_agent.skills.file import FileReadSkill, FileWriteSkill
from orbit_agent.skills.shell import ShellCommandSkill

class SkillRegistry:
    def __init__(self, config: Optional[OrbitConfig] = None):
        self.config = config
        self._skills: Dict[str, BaseSkill] = {}
        self._register_defaults()

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
                
                vision_skill = VisionSkill(key)
                self.register_skill(vision_skill)
                self.register_skill(VisualInteractionSkill(vision_skill))

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
