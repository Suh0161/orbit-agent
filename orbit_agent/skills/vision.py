import base64
from pathlib import Path
from typing import Type, Optional, List, Dict, Any
from pydantic import BaseModel, Field

from orbit_agent.skills.base import BaseSkill, SkillConfig
from orbit_agent.models.openai_client import OpenAIClient
from orbit_agent.models.base import Message
from orbit_agent.config.config import OrbitConfig

import json
from enum import Enum

class VisionMode(str, Enum):
    DESCRIBE = "describe"
    LOCATE = "locate"

class VisionInput(BaseModel):
    image_path: str = Field(description="Absolute path to the image file to analyze.")
    query: str = Field(description="Query about the image. If mode is 'locate', describe WHAT to find (e.g. 'the submit button').")
    mode: VisionMode = Field(default=VisionMode.DESCRIBE, description="Mode: 'describe' (text) or 'locate' (returns x,y coordinates).")

class VisionOutput(BaseModel):
    analysis: str
    coordinates: Optional[List[int]] = Field(default=None, description="[x, y] coordinates if mode was 'locate'")
    error: Optional[str] = None

class VisionSkill(BaseSkill):
    def __init__(self, api_key: str):
        super().__init__()
        self.api_key = api_key
        # We use a dedicated client for vision, defaulting to gpt-5.1
        self.client = OpenAIClient(api_key=self.api_key, model_name="gpt-4o")

    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="vision_analyze",
            description="Analyzes an image using GPT-4 Vision. Pass the path to a screenshot/image and a query. Use mode='locate' to get (x,y) coordinates of an element.",
            permissions_required=["vision_analyze"]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return VisionInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return VisionOutput

    async def execute(self, inputs: VisionInput) -> VisionOutput:
        try:
            path = Path(inputs.image_path)
            if not path.exists():
                # Fallback: Check in screenshots folder
                fallback = Path("screenshots") / path.name
                if fallback.exists():
                    path = fallback
                else:
                    return VisionOutput(analysis="", error=f"Image not found at {inputs.image_path} or {fallback}")
            
            # Encode image
            with open(path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')
            
            # Use 'describe' logic by default
            prompt_text = inputs.query
            
            # If LOCATE mode, override prompt
            if inputs.mode == VisionMode.LOCATE:
                prompt_text = f"""
                LOCATE the element described as: '{inputs.query}'.
                
                CRITICAL INSTRUCTION:
                - Find the PRIMARY functional element matching the description.
                - Ignore small icons, profile pictures, or status indicators unless explicitly asked for.
                - If there are multiple matches, choose the most prominent/center one (e.g. the main 'Play' button, not a small play icon in a list).
                
                Return a strictly valid JSON object with the 'box_2d' key containing the [ymin, xmin, ymax, xmax] coordinates.
                Example: {{ "box_2d": [100, 200, 150, 300] }}
                
                Do not add markdown code blocks. Just the JSON.
                """

            content = [
                {"type": "text", "text": prompt_text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{base64_image}"
                    }
                }
            ]
            
            messages = [
                Message(role="user", content=content)
            ]
            
            response = await self.client.generate(messages)
            text_response = response.content.strip()
            
            # Parse output if LOCATE
            if inputs.mode == VisionMode.LOCATE:
                try:
                    # Cleanup markdown if present
                    clean_text = text_response.replace("```json", "").replace("```", "").strip()
                    try:
                        data = json.loads(clean_text)
                    except json.JSONDecodeError:
                        # Attempt to find JSON within the response if it's not strictly JSON
                        start = clean_text.find("{")
                        end = clean_text.rfind("}")
                        if start != -1 and end != -1 and start < end:
                            data = json.loads(clean_text[start : end + 1])
                        else:
                            raise ValueError("Invalid JSON in location response")

                    box = data.get("box_2d")
                    # Support old format just in case
                    point = data.get("point")
                    
                    if box and len(box) == 4:
                        ymin, xmin, ymax, xmax = box
                        # Calculate Center
                        center_x = int((xmin + xmax) / 2)
                        center_y = int((ymin + ymax) / 2)
                        return VisionOutput(analysis=f"Located box {box}, center at {center_x},{center_y}", coordinates=[center_x, center_y])
                        
                    elif point and len(point) == 2:
                         return VisionOutput(analysis=f"Located point at {point}", coordinates=point)
                    else:
                        return VisionOutput(analysis=text_response, error="JSON parsed but no 'box_2d' or 'point' found")
                except Exception as e:
                    return VisionOutput(analysis=text_response, error=f"Failed to parse location JSON: {e}")

            return VisionOutput(analysis=text_response)
 
        except Exception as e:
            return VisionOutput(analysis="", error=str(e))

