import os
import time
from datetime import datetime
from typing import Type, Optional, List, Literal

import pyautogui
from pydantic import BaseModel, Field
import asyncio

from orbit_agent.skills.base import BaseSkill, SkillConfig

# Fail-safe: moving mouse to 0,0 triggers error
pyautogui.FAILSAFE = True

try:
    import pydirectinput  # type: ignore
    _DIRECT_INPUT_AVAILABLE = True
except Exception:
    pydirectinput = None
    _DIRECT_INPUT_AVAILABLE = False

class DesktopInput(BaseModel):
    action: str = Field(description="Action: 'move', 'click', 'double_click', 'right_click', 'drag', 'type', 'press', 'scroll', 'screenshot', 'wait'")
    x: Optional[int] = Field(default=None, description="X coordinate")
    y: Optional[int] = Field(default=None, description="Y coordinate")
    text: Optional[str] = Field(default=None, description="Text to type")
    keys: Optional[List[str]] = Field(default=None, description="Keys to press (e.g. ['ctrl', 'c'])")
    amount: int = Field(default=0, description="Scroll amount")
    duration: float = Field(default=0.5, description="Animation duration in seconds (also used for 'wait' in seconds)")
    save_path: Optional[str] = Field(default=None, description="For screenshot: save to this path (e.g. 'screenshots/step_1.png'). Next vision step MUST use this exact path.")
    backend: Optional[Literal["auto", "pyautogui", "direct"]] = Field(
        default=None,
        description=(
            "Optional input backend override. "
            "'pyautogui' works for most apps. "
            "'direct' uses pydirectinput (better for DirectX/fullscreen games). "
            "'auto' prefers 'direct' if available. "
            "If omitted, uses ORBIT_DESKTOP_INPUT_BACKEND env var (default: auto)."
        ),
    )

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

    def _resolve_backend(self, inputs: DesktopInput) -> tuple[str, Optional[str]]:
        requested = (inputs.backend or "").strip().lower()
        if not requested:
            requested = os.environ.get("ORBIT_DESKTOP_INPUT_BACKEND", "auto").strip().lower()

        if requested in {"", "auto"}:
            return ("direct" if _DIRECT_INPUT_AVAILABLE else "pyautogui"), None

        if requested in {"pyautogui"}:
            return "pyautogui", None

        if requested in {"direct", "pydirectinput", "game"}:
            if not _DIRECT_INPUT_AVAILABLE:
                return (
                    "pyautogui",
                    "Direct input backend requested but 'pydirectinput' is not installed. "
                    "Install it with: pip install pydirectinput",
                )
            return "direct", None

        # Unknown value -> fallback safely
        return ("direct" if _DIRECT_INPUT_AVAILABLE else "pyautogui"), None

    def _run_sync(self, inputs: DesktopInput) -> DesktopOutput:
        backend, backend_err = self._resolve_backend(inputs)
        if backend_err:
            return DesktopOutput(success=False, error=backend_err)

        screen_width, screen_height = pyautogui.size()
        
        # Coordinate Validation
        if inputs.x is not None and inputs.y is not None:
            if not (0 <= inputs.x <= screen_width and 0 <= inputs.y <= screen_height):
                 return DesktopOutput(success=False, error=f"Coordinates ({inputs.x}, {inputs.y}) out of bounds ({screen_width}x{screen_height})")

        if inputs.action == "move":
            if inputs.x is None or inputs.y is None: return DesktopOutput(success=False, error="X and Y required for move")
            if backend == "direct" and _DIRECT_INPUT_AVAILABLE and pydirectinput is not None:
                pydirectinput.moveTo(inputs.x, inputs.y)  # type: ignore[attr-defined]
                time.sleep(max(0.0, inputs.duration))
            else:
                pyautogui.moveTo(inputs.x, inputs.y, duration=inputs.duration)
            return DesktopOutput(success=True, data=f"Moved mouse to {inputs.x}, {inputs.y} (backend={backend})")
            
        elif inputs.action == "click":
            if inputs.x is not None and inputs.y is not None:
                if backend == "direct" and _DIRECT_INPUT_AVAILABLE and pydirectinput is not None:
                    pydirectinput.moveTo(inputs.x, inputs.y)  # type: ignore[attr-defined]
                    time.sleep(max(0.0, inputs.duration))
                    pydirectinput.click()  # type: ignore[attr-defined]
                else:
                    pyautogui.click(inputs.x, inputs.y, duration=inputs.duration)
                return DesktopOutput(success=True, data=f"Clicked at {inputs.x}, {inputs.y} (backend={backend})")

            if backend == "direct" and _DIRECT_INPUT_AVAILABLE and pydirectinput is not None:
                pydirectinput.click()  # type: ignore[attr-defined]
            else:
                pyautogui.click()
            return DesktopOutput(success=True, data=f"Clicked at current position (backend={backend})")

        elif inputs.action == "double_click":
            if inputs.x is not None and inputs.y is not None:
                if backend == "direct" and _DIRECT_INPUT_AVAILABLE and pydirectinput is not None:
                    pydirectinput.moveTo(inputs.x, inputs.y)  # type: ignore[attr-defined]
                    time.sleep(max(0.0, inputs.duration))
                    dbl = getattr(pydirectinput, "doubleClick", None)
                    if callable(dbl):
                        dbl()  # type: ignore[misc]
                    else:
                        pydirectinput.click()  # type: ignore[attr-defined]
                        time.sleep(0.05)
                        pydirectinput.click()  # type: ignore[attr-defined]
                else:
                    pyautogui.doubleClick(inputs.x, inputs.y, duration=inputs.duration)
            else:
                if backend == "direct" and _DIRECT_INPUT_AVAILABLE and pydirectinput is not None:
                    dbl = getattr(pydirectinput, "doubleClick", None)
                    if callable(dbl):
                        dbl()  # type: ignore[misc]
                    else:
                        pydirectinput.click()  # type: ignore[attr-defined]
                        time.sleep(0.05)
                        pydirectinput.click()  # type: ignore[attr-defined]
                else:
                    pyautogui.doubleClick()
            return DesktopOutput(success=True, data=f"Double clicked (backend={backend})")

        elif inputs.action == "right_click":
            if inputs.x is not None and inputs.y is not None:
                if backend == "direct" and _DIRECT_INPUT_AVAILABLE and pydirectinput is not None:
                    pydirectinput.moveTo(inputs.x, inputs.y)  # type: ignore[attr-defined]
                    time.sleep(max(0.0, inputs.duration))
                    rc = getattr(pydirectinput, "rightClick", None)
                    if callable(rc):
                        rc()  # type: ignore[misc]
                    else:
                        pyautogui.rightClick(inputs.x, inputs.y)
                else:
                    pyautogui.rightClick(inputs.x, inputs.y)
            else:
                if backend == "direct" and _DIRECT_INPUT_AVAILABLE and pydirectinput is not None:
                    rc = getattr(pydirectinput, "rightClick", None)
                    if callable(rc):
                        rc()  # type: ignore[misc]
                    else:
                        pyautogui.rightClick()
                else:
                    pyautogui.rightClick()
            return DesktopOutput(success=True, data=f"Right clicked (backend={backend})")

        elif inputs.action == "drag":
            if inputs.x is None or inputs.y is None: return DesktopOutput(success=False, error="X and Y required for drag")
            if backend == "direct" and _DIRECT_INPUT_AVAILABLE and pydirectinput is not None:
                drag_to = getattr(pydirectinput, "dragTo", None)
                if callable(drag_to):
                    drag_to(inputs.x, inputs.y, duration=max(0.0, inputs.duration))  # type: ignore[misc]
                else:
                    # Best-effort fallback
                    pyautogui.dragTo(inputs.x, inputs.y, duration=inputs.duration)
            else:
                pyautogui.dragTo(inputs.x, inputs.y, duration=inputs.duration)
            return DesktopOutput(success=True, data=f"Dragged to {inputs.x}, {inputs.y} (backend={backend})")

        elif inputs.action == "type":
            if not inputs.text: return DesktopOutput(success=False, error="Text required")
            
            # Awareness: Check active window
            active_window = "Unknown"
            try:
                import ctypes
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                buff = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                active_window = buff.value
            except:
                pass

            # Typing usually targets OS text inputs. Prefer PyAutoGUI unless explicitly forced to direct input.
            requested = (inputs.backend or os.environ.get("ORBIT_DESKTOP_INPUT_BACKEND", "")).strip().lower()
            force_direct = requested in {"direct", "pydirectinput", "game"}
            type_backend = "pyautogui"

            if force_direct and _DIRECT_INPUT_AVAILABLE and pydirectinput is not None:
                write = getattr(pydirectinput, "write", None) or getattr(pydirectinput, "typewrite", None)
                if callable(write):
                    write(inputs.text, interval=0.05)  # type: ignore[misc]
                    type_backend = "direct"
                else:
                    pyautogui.write(inputs.text, interval=0.05)
            else:
                pyautogui.write(inputs.text, interval=0.05)
            # Submit optionally? No, let 'press' handle enter
            return DesktopOutput(success=True, data=f"Typed '{inputs.text}' into window '{active_window}' (backend={type_backend})")

        elif inputs.action == "press":
            if not inputs.keys: return DesktopOutput(success=False, error="Keys required")
            keys = [str(k).strip().lower() for k in inputs.keys if str(k).strip()]
            if not keys:
                return DesktopOutput(success=False, error="Keys required")

            # OS-level keys (like Win) are best handled by PyAutoGUI.
            os_only = {"win", "windows", "super", "command"}
            if any(k in os_only for k in keys):
                backend = "pyautogui"

            # Handle hotkeys vs single key
            if len(keys) > 1:
                if backend == "direct" and _DIRECT_INPUT_AVAILABLE and pydirectinput is not None:
                    # Implement hotkey manually for direct input
                    for k in keys[:-1]:
                        kd = getattr(pydirectinput, "keyDown", None)
                        if callable(kd):
                            kd(k)  # type: ignore[misc]
                        else:
                            pyautogui.keyDown(k)

                    press = getattr(pydirectinput, "press", None)
                    if callable(press):
                        press(keys[-1])  # type: ignore[misc]
                    else:
                        pyautogui.press(keys[-1])

                    for k in reversed(keys[:-1]):
                        ku = getattr(pydirectinput, "keyUp", None)
                        if callable(ku):
                            ku(k)  # type: ignore[misc]
                        else:
                            pyautogui.keyUp(k)
                else:
                    pyautogui.hotkey(*keys)
                msg = f"Pressed hotkey: {'+'.join(keys)}"
            else:
                key = keys[0]
                if backend == "direct" and _DIRECT_INPUT_AVAILABLE and pydirectinput is not None:
                    press = getattr(pydirectinput, "press", None)
                    if callable(press):
                        press(key)  # type: ignore[misc]
                    else:
                        pyautogui.press(key)
                else:
                    pyautogui.press(key)
                msg = f"Pressed key: {key}"
            return DesktopOutput(success=True, data=f"{msg} (backend={backend})")

        elif inputs.action == "scroll":
            if backend == "direct" and _DIRECT_INPUT_AVAILABLE and pydirectinput is not None:
                scr = getattr(pydirectinput, "scroll", None)
                if callable(scr):
                    scr(inputs.amount)  # type: ignore[misc]
                else:
                    pyautogui.scroll(inputs.amount)
            else:
                pyautogui.scroll(inputs.amount)
            return DesktopOutput(success=True, data=f"Scrolled {inputs.amount} (backend={backend})")

        elif inputs.action == "screenshot":
            if inputs.save_path:
                path = os.path.normpath(os.path.join(os.getcwd(), inputs.save_path))
            else:
                fname = f"screen_{datetime.now().strftime('%H%M%S')}.png"
                path = os.path.join(os.getcwd(), "screenshots", fname)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            pyautogui.screenshot(path)
            return DesktopOutput(success=True, data=f"Screenshot saved to {path}")

        elif inputs.action == "wait":
            secs = max(0, inputs.duration)
            time.sleep(secs)
            return DesktopOutput(success=True, data=f"Waited {secs}s")

        else:
            return DesktopOutput(success=False, error=f"Unknown action: {inputs.action}")
