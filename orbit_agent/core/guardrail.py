from typing import Dict, Optional, List
from orbit_agent.skills.base import BaseSkill
from orbit_agent.models.router import ModelRouter
from orbit_agent.models.base import Message

class GuardrailAgent:
    """
    A lightweight supervisor that checks actions before execution.
    It uses a fast LLM (or same LLM) to classify actions as Safe or Dangerous.
    """
    def __init__(self, router: ModelRouter):
        self.router = router
        # Use the router's default client directly so tests can mock planning
        # without accidentally hijacking guardrail decisions.
        self.client = getattr(router, "default_client", None)
        # We use a strict prompt
        self.system_prompt = """
        You are the Safety Guardrail for an Autonomous Desktop Agent.
        Your job is to review a proposed Action and determine if it is SAFE to execute automatically.

        Rules for DANGEROUS actions (Reject):
        - Deleting system files (System32, Windows, etc).
        - Formatting drives.
        - Exfiltrating sensitive passwords/keys.
        - Modifying OS boot settings.
        - Infinite loops of opening apps.
        
        Rules for SAFE actions (Approve):
        - Taking screenshots.
        - Browsing safe websites.
        - creating project based files.
        - Clicking UI elements (unless obviously malicious).
        - Typing standard text.

        Action to Review:
        Skill: {skill_name}
        Input: {input_data}

        Response Format:
        Allows only two responses: "APPROVE" or "REJECT: <reason>"
        """

    async def check(self, skill_name: str, input_data: dict) -> tuple[bool, str]:
        # Skip cheap/safe skills if we want speed
        if skill_name in ["desktop_view", "file_read", "vision_analyze"]:
             return True, "Safe read-only skill"

        # Construct prompt
        msg = self.system_prompt.format(skill_name=skill_name, input_data=str(input_data))
        
        client = self.client or self.router.get_client("default")
        response = await client.generate([Message(role="user", content=msg)], temperature=0.0)
        
        content = response.content.strip()
        if content.startswith("APPROVE"):
            return True, "Approved by Guardrail"
        else:
            return False, content
