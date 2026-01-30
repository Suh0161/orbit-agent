from pathlib import Path
from typing import Optional, Dict, Any
import yaml
from pydantic import BaseModel, Field

class ModelConfig(BaseModel):
    provider: str = "openai"
    model_name: str = "gpt-5.1"
    api_key_env_var: str = "OPENAI_API_KEY"
    base_url: Optional[str] = None

    model_config = {"protected_namespaces": ()}

class MemoryConfig(BaseModel):
    path: Path = Field(default=Path.home() / ".orbit" / "memory")
    collection_name: str = "orbit_memory"

class OrbitConfig(BaseModel):
    workspace_root: Path = Field(default=Path.home() / "orbit_workspace")
    model: ModelConfig = Field(default_factory=ModelConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    safe_mode: bool = True
    
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "OrbitConfig":
        from dotenv import load_dotenv
        load_dotenv()
        
        if not path:
             # Default search paths
             paths = [
                 Path("orbit_config.yaml"),
                 Path.home() / ".orbit" / "config.yaml"
             ]
             for p in paths:
                 if p.exists():
                     path = p
                     break
        
        if path and path.exists():
            with open(path, "r") as f:
                data = yaml.safe_load(f) or {}
            return cls(**data)
        
        return cls()
