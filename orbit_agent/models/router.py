from typing import Dict, Any, Optional
from orbit_agent.models.base import BaseModelClient
from orbit_agent.models.openai_client import OpenAIClient
from orbit_agent.config.config import OrbitConfig

class ModelRouter:
    def __init__(self, config: OrbitConfig):
        self.config = config
        self.clients: Dict[str, BaseModelClient] = {}
        
        # Initialize default client
        api_key = "dummy"
        import os
        
        # Check if the config value is the name of an env var
        env_key_name = config.model.api_key_env_var
        env_key_value = os.environ.get(env_key_name)
        
        if env_key_value:
            api_key = env_key_value
        elif env_key_name and (env_key_name.startswith("sk-") or len(env_key_name) > 20):
             # Assume the user put the key directly in the config
             api_key = env_key_name
             print(f"Warning: API Key found directly in config ('{env_key_name[:5]}...'). usage of env vars is recommended.")
            
        self.default_client = OpenAIClient(
            api_key=api_key,
            model_name=config.model.model_name,
            base_url=config.model.base_url
        )
        self.clients["default"] = self.default_client

    def get_client(self, purpose: str = "general") -> BaseModelClient:
        # For v0.1, we mostly return the default.
        # Future: switch based on purpose (e.g. "coding" -> claude-3, "summary" -> gpt-3.5)
        return self.clients.get(purpose, self.default_client)
