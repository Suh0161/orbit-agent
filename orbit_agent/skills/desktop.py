import pyautogui
import os
from datetime import datetime
from typing import Type, Optional, List
from pydantic import BaseModel, Field
import asyncio

from orbit_agent.skills.base import BaseSkill, SkillConfig

# Fail-safe: moving mouse to 0,0 triggers error
pyautogui.FAILSAFE = True

class DesktopInput(BaseModel):
    action: str = Field(description="Action: 'move', 'click', 'double_click', 'right_click', 'drag', 'type', 'press', 'scroll', 'screenshot'")
    x: Optional[int] = Field(default=None, description="X coordinate")
    y: Optional[int] = Field(default=None, description="Y coordinate")
    text: Optional[str] = Field(default=None, description="Text to type")
    keys: Optional[List[str]] = Field(default=None, description="Keys to press (e.g. ['ctrl', 'c'])")
    amount: int = Field(default=0, description="Scroll amount")
    duration: float = Field(default=0.5, description="Animation duration in seconds")

class DesktopOutput(BaseModel):
    success: bool
    data: Optional[str] = None
    error: Optional[str] = None

class DesktopSkill(BaseSkill):
    """
    Universal Desktop Control using PyAutoGUI.
    Allows the agent to control Mouse & Keyboard at an OS level.
    """
    
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="computer_control",
            description="Control the mouse and keyboard to interact with any desktop application.",
            permissions_required=["desktop_control"]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return DesktopInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return DesktopOutput

    async def execute(self, inputs: DesktopInput) -> DesktopOutput:
        try:
            # Run blocking PyAutoGUI calls in thread executor to avoid blocking asyncio loop
            return await asyncio.to_thread(self._run_sync, inputs)
        except Exception as e:
            return DesktopOutput(success=False, error=str(e))

    def _run_sync(self, inputs: DesktopInput) -> DesktopOutput:
        screen_width, screen_height = pyautogui.size()
        
        # Coordinate Validation
        if inputs.x is not None and inputs.y is not None:
            if not (0 <= inputs.x <= screen_width and 0 <= inputs.y <= screen_height):
                 return DesktopOutput(success=False, error=f"Coordinates ({inputs.x}, {inputs.y}) out of bounds ({screen_width}x{screen_height})")

        if inputs.action == "move":
            if inputs.x is None or inputs.y is None: return DesktopOutput(success=False, error="X and Y required for move")
            pyautogui.moveTo(inputs.x, inputs.y, duration=inputs.duration)
            return DesktopOutput(success=True, data=f"Moved mouse to {inputs.x}, {inputs.y}")
            
        elif inputs.action == "click":
            if inputs.x and inputs.y:
                pyautogui.click(inputs.x, inputs.y, duration=inputs.duration)
                return DesktopOutput(success=True, data=f"Clicked at {inputs.x}, {inputs.y}")
            else:
                pyautogui.click()
                return DesktopOutput(success=True, data="Clicked at current position")

        elif inputs.action == "double_click":
            if inputs.x and inputs.y:
                pyautogui.doubleClick(inputs.x, inputs.y, duration=inputs.duration)
            else:
                pyautogui.doubleClick()
            return DesktopOutput(success=True, data="Double Clicked")

        elif inputs.action == "right_click":
             if inputs.x and inputs.y:
                pyautogui.rightClick(inputs.x, inputs.y)
             else:
                pyautogui.rightClick()
             return DesktopOutput(success=True, data="Right Clicked")

        elif inputs.action == "drag":
            if inputs.x is None or inputs.y is None: return DesktopOutput(success=False, error="X and Y required for drag")
            pyautogui.dragTo(inputs.x, inputs.y, duration=inputs.duration)
            return DesktopOutput(success=True, data=f"Dragged to {inputs.x}, {inputs.y}")

        elif inputs.action == "type":
            if not inputs.text: return DesktopOutput(success=False, error="Text required")
            pyautogui.write(inputs.text, interval=0.05)
            # Submit optionally? No, let 'press' handle enter
            return DesktopOutput(success=True, data=f"Typed '{inputs.text}'")

        elif inputs.action == "press":
            if not inputs.keys: return DesktopOutput(success=False, error="Keys required")
            # Handle hotkeys vs single key
            if len(inputs.keys) > 1:
                pyautogui.hotkey(*inputs.keys)
                msg = f"Pressed hotkey: {'+'.join(inputs.keys)}"
            else:
                pyautogui.press(inputs.keys[0])
                msg = f"Pressed key: {inputs.keys[0]}"
            return DesktopOutput(success=True, data=msg)

        elif inputs.action == "scroll":
            pyautogui.scroll(inputs.amount)
            return DesktopOutput(success=True, data=f"Scrolled {inputs.amount}")

        elif inputs.action == "screenshot":
            # Redundant to GUI vision but useful for skills
            fname = f"screen_{datetime.now().strftime('%H%M%S')}.png"
            path = os.path.join(os.getcwd(), "screenshots", fname)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            pyautogui.screenshot(path)
            return DesktopOutput(success=True, data=f"Screenshot saved to {path}")

        else:
            return DesktopOutput(success=False, error=f"Unknown action: {inputs.action}")
