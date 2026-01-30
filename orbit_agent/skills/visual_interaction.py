from typing import Type, Literal, Optional
from pydantic import BaseModel, Field
import asyncio
import os

from orbit_agent.skills.base import BaseSkill, SkillConfig
from orbit_agent.skills.vision import VisionSkill, VisionInput, VisionMode
from orbit_agent.skills.som_vision import SoMVisionSkill, SoMInput
from orbit_agent.skills.desktop import DesktopSkill, DesktopInput, DesktopOutput
from orbit_agent.memory.ui_cache import UICache

class VisualInteractionInput(BaseModel):
    description: str = Field(..., description="Visual description of the element to interact with (e.g. 'the blue submit button', 'the discord server icon with text 03')")
    action: Literal["click", "double_click", "hover", "hover_and_confirm", "verify"] = Field(default="click", description="Action to perform. 'hover_and_confirm' will hover, wait, read tooltip, then click if match.")
    confirm_text: Optional[str] = Field(default=None, description="Text to verify in tooltip/screen if action is 'hover_and_confirm'.")
    
class VisualInteractionOutput(BaseModel):
    success: bool
    data: str = ""
    error: str = ""

class VisualInteractionSkill(BaseSkill):
    def __init__(self, vision_skill: VisionSkill):
        super().__init__()
        self.vision_skill = vision_skill
        self.desktop_skill = DesktopSkill()
        self.cache = UICache()
        # Prefer SoM for small UI targets (higher click accuracy).
        try:
            model_name = getattr(getattr(self.vision_skill, "client", None), "model_name", "gpt-5.1")
            self.som_skill = SoMVisionSkill(self.vision_skill.api_key, model_name=model_name)
        except Exception:
            self.som_skill = None

    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="visual_interact",
            description="Locates an element on screen. Uses caching for speed. 'hover_and_confirm' is verification.",
            permissions_required=["vision_analyze", "desktop_control", "desktop_view"]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return VisualInteractionInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return VisualInteractionOutput

    async def execute(self, inputs: VisualInteractionInput) -> VisualInteractionOutput:
        try:
            # Take a screenshot for grounding.
            fixed_path = os.path.abspath("screenshots/_temp_locate.png")
            os.makedirs(os.path.dirname(fixed_path), exist_ok=True)
            
            # Try cache first (fast path).
            cached_coords = self.cache.get(inputs.description)
            coords = None
            
            if cached_coords:
                print(f"[Cache] Using saved coords for '{inputs.description}': {cached_coords}")
                coords = cached_coords
            
            if not coords:
                view_out = await self.desktop_skill.execute(DesktopInput(action="screenshot"))
                if not view_out.success:
                     return VisualInteractionOutput(success=False, error=f"Screenshot failed: {view_out.error}")
                
                # Extract path from DesktopSkill output ("Screenshot saved to ...").
                try:
                    fixed_path = view_out.data.split("saved to ")[1].strip()
                except:
                     return VisualInteractionOutput(success=False, error="Could not get screenshot path")

                # Use SoM if available (better for small icons/buttons).
                if self.som_skill:
                    som_out = await self.som_skill.execute(SoMInput(
                        image_path=fixed_path,
                        target_description=inputs.description
                    ))
                    if not som_out.success or not som_out.coordinates:
                        return VisualInteractionOutput(success=False, error=f"Could not locate '{inputs.description}': {som_out.error or 'No coordinates found'}")
                    coords = som_out.coordinates
                else:
                    vision_out = await self.vision_skill.execute(VisionInput(
                        image_path=fixed_path,
                        query=inputs.description,
                        mode=VisionMode.LOCATE
                    ))
                    if vision_out.error or not vision_out.coordinates:
                        return VisualInteractionOutput(success=False, error=f"Could not locate '{inputs.description}': {vision_out.error or 'No coordinates found'}")
                    coords = vision_out.coordinates
                # Save for next time.
                self.cache.set(inputs.description, coords)
            
            x, y = coords
            
            # 3. Action Logic
            if inputs.action == "hover_and_confirm":
                if not inputs.confirm_text:
                    return VisualInteractionOutput(success=False, error="confirm_text required for hover_and_confirm action")
                
                # Hover.
                await self.desktop_skill.execute(DesktopInput(action="move", x=x, y=y))
                
                # Wait for tooltip to render.
                await asyncio.sleep(1.0)
                
                # New screenshot for verification.
                view_out_2 = await self.desktop_skill.execute(DesktopInput(action="screenshot"))
                if not view_out_2.success:
                     return VisualInteractionOutput(success=False, error="Failed to take confirmation screenshot")
                fixed_path = view_out_2.data.split("saved to ")[1].strip()

                # Vision check near cursor.
                check_query = f"I am hovering over an element. Is the text '{inputs.confirm_text}' OR an icon/symbol representing '{inputs.confirm_text}' (e.g. a Play Triangle, Gear icon, etc.) visible at or near the cursor position? Answer YES or NO."
                vision_check = await self.vision_skill.execute(VisionInput(
                    image_path=fixed_path,
                    query=check_query,
                    mode=VisionMode.DESCRIBE
                ))
                
                if "yes" in vision_check.analysis.lower():
                    # Confirmed -> Click (and reinforce cache)
                    self.cache.set(inputs.description, coords)
                    act_out = await self.desktop_skill.execute(DesktopInput(action="click", x=x, y=y))
                    return VisualInteractionOutput(success=True, data=f"Confirmed '{inputs.confirm_text}' and clicked at {x},{y}")
                else:
                    # Verification failed; retry by re-locating.
                    if cached_coords:
                         print("[Cache] Cached coords look stale; re-locating.")
                         self.cache.set(inputs.description, None)
                    
                    print("Verification failed. Re-analyzing screen...")
                    cot_query = f"I failed to find '{inputs.description}' at the previous location. Look at the full screen. Is the element visible? detailedly describe its visual appearance and position (e.g. 'top right', 'center'). If it's a 'Play' button, look for triangles or 'Join' text."
                    
                    analysis_out = await self.vision_skill.execute(VisionInput(
                        image_path=fixed_path,
                        query=cot_query,
                        mode=VisionMode.DESCRIBE
                    ))
                    
                    # Re-locate using the analysis.
                    print(f"Screen analysis (snippet): {analysis_out.analysis[:50]}...")
                    new_locate_query = f"Based on this analysis: '{analysis_out.analysis}', LOCATE the target element '{inputs.description}'."
                    
                    recovery_out = await self.vision_skill.execute(VisionInput(
                        image_path=fixed_path,
                        query=new_locate_query,
                        mode=VisionMode.LOCATE
                    ))
                    
                    if recovery_out.coordinates:
                        xr, yr = recovery_out.coordinates
                        print(f"Recovery found target at {xr},{yr}. Retrying...")
                        
                        # Move & verify again.
                        await self.desktop_skill.execute(DesktopInput(action="move", x=xr, y=yr))
                        await asyncio.sleep(1.0)
                        
                        # New screenshot for verification.
                        view_out_rec = await self.desktop_skill.execute(DesktopInput(action="screenshot"))
                        fixed_path = view_out_rec.data.split("saved to ")[1].strip()

                        verify_out = await self.vision_skill.execute(VisionInput(
                            image_path=fixed_path,
                            query=check_query,
                            mode=VisionMode.DESCRIBE
                        ))
                        
                        if "yes" in verify_out.analysis.lower():
                            act_out = await self.desktop_skill.execute(DesktopInput(action="click", x=xr, y=yr))
                            # Update cache with the good coordinates.
                            self.cache.set(inputs.description, [xr, yr])
                            return VisualInteractionOutput(success=True, data=f"Recovered and clicked '{inputs.description}' at {xr},{yr}")
                    
                    # If recovery fails, describe what's under the cursor to help debugging.
                    what_is_there_query = "I still can't find it. Briefly describe what IS at the cursor position (text, icon, color) so I can correct my plan."
                    what_is_there = await self.vision_skill.execute(VisionInput(image_path=fixed_path, query=what_is_there_query, mode=VisionMode.DESCRIBE))
                    
                    return VisualInteractionOutput(success=False, error=f"Verification failed after recovery. Expected '{inputs.confirm_text}', but saw: {what_is_there.analysis}")

            # ... verify logic ...
            elif inputs.action == "verify":
                check_query = f"Does the screen clearly contain the text or element '{inputs.description}'? Answer YES or NO."
                vision_check = await self.vision_skill.execute(VisionInput(
                    image_path=fixed_path,
                    query=check_query,
                    mode=VisionMode.DESCRIBE
                ))
                if "yes" in vision_check.analysis.lower():
                     return VisualInteractionOutput(success=True, data=f"Verified presence of '{inputs.description}'")
                else:
                     return VisualInteractionOutput(success=False, error=f"Verification failed: '{inputs.description}' not found.")

            # Standard Actions
            elif inputs.action == "double_click":
                act_out = await self.desktop_skill.execute(DesktopInput(action="click", x=x, y=y))
                await asyncio.sleep(0.1)
                await self.desktop_skill.execute(DesktopInput(action="click", x=x, y=y))
                
            elif inputs.action == "hover":
                act_out = await self.desktop_skill.execute(DesktopInput(action="move", x=x, y=y))
                
            else: # click or default
                 act_out = await self.desktop_skill.execute(DesktopInput(action="click", x=x, y=y))
            
            if not act_out.success:
                return VisualInteractionOutput(success=False, error=f"Action {inputs.action} failed: {act_out.error}")
                
            return VisualInteractionOutput(success=True, data=f"Located '{inputs.description}' at {x},{y} and performed {inputs.action}")

        except Exception as e:
            return VisualInteractionOutput(success=False, error=str(e))
