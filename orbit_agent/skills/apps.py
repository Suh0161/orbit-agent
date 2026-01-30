"""
App Control Skill

Opens/closes apps and can discover installed apps by scanning Start Menu shortcuts.
"""

import os
import glob
import subprocess
from pathlib import Path
from typing import Type, List, Optional
from pydantic import BaseModel, Field
from AppOpener import open as app_open, close as app_close

from orbit_agent.skills.base import BaseSkill, SkillConfig


class AppInput(BaseModel):
    action: str = Field(..., description="'open', 'close', 'list', 'search'")
    app_name: Optional[str] = Field(None, description="Name of the application (e.g. 'spotify', 'antigravity'). Required for open/close.")


class AppMatch(BaseModel):
    name: str
    path: str
    match_score: float


class AppOutput(BaseModel):
    success: bool
    message: str
    found_apps: Optional[List[str]] = None


class AppControlSkill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._app_cache = {}
        self._cache_built = False

    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="app_control",
            description="Control local applications. Use action='search' to find installed apps, 'open' to launch, 'list' to see all detected apps.",
            permissions_required=["app_control"]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return AppInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return AppOutput

    async def execute(self, inputs: AppInput) -> AppOutput:
        try:
            # Refresh cache if needed or requested
            if not self._cache_built or inputs.action == "list":
                self._build_app_cache()

            if inputs.action == "open":
                return self._open_app(inputs.app_name)
            elif inputs.action == "close":
                return self._close_app(inputs.app_name)
            elif inputs.action == "list":
                apps = sorted(list(self._app_cache.keys()))
                return AppOutput(success=True, message=f"Found {len(apps)} apps", found_apps=apps[:50]) # Limit output
            elif inputs.action == "search":
                matches = self._fuzzy_search(inputs.app_name)
                names = [m.name for m in matches]
                return AppOutput(success=True, message=f"Found {len(matches)} matches", found_apps=names)
            else:
                return AppOutput(success=False, message=f"Unknown action: {inputs.action}")

        except Exception as e:
            return AppOutput(success=False, message=f"Error: {e}")

    def _build_app_cache(self):
        """Scans Start Menu directories for .lnk and .exe files."""
        # Standard Windows Start Menu locations
        paths = [
            os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
            os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs")
        ]

        self._app_cache = {}
        
        for path in paths:
            p = Path(path)
            if not p.exists():
                continue
                
            # Recursive scan for shortcuts
            for file_path in p.rglob("*.lnk"):
                name = file_path.stem.lower() # Normalize to lowercase
                self._app_cache[name] = str(file_path)
                
                # Also store exact name
                self._app_cache[file_path.stem] = str(file_path)

        self._cache_built = True

    def _fuzzy_search(self, query: str) -> List[AppMatch]:
        """Find apps matching query."""
        if not query:
            return []
            
        q = query.lower()
        matches = []
        
        for name, path in self._app_cache.items():
            if q in name:
                matches.append(AppMatch(name=name, path=path, match_score=1.0))
        
        return matches

    def _open_app(self, app_name: str) -> AppOutput:
        import time
        if not app_name:
             return AppOutput(success=False, message="App name required")

        # 1. Try our cache first (more precise)
        matches = self._fuzzy_search(app_name)
        launch_success = False
        
        if matches:
            # Use the best match (shortest string usually implies exact match)
            best_match = sorted(matches, key=lambda x: len(x.name))[0]
            try:
                os.startfile(best_match.path)
                launch_success = True
            except Exception as e:
                print(f"Failed to launch shortcut: {e}")

        # 2. Key Fallback: Use 'explorer.exe shell:AppsFolder' logic or AppOpener
        if not launch_success:
            print(f"[AppSkill] Cache miss for '{app_name}'. Trying AppOpener...")
            try:
                app_open(app_name, match_closest=True, output=False)
                launch_success = True
            except Exception as e:
                return AppOutput(success=False, message=f"Could not open '{app_name}'. Not found.")
        
        if launch_success:
            # Wait + retry focus: many apps take longer than 2s to create a visible window.
            focused_title = "Not Found"
            for _ in range(8):
                time.sleep(1.0)
                focused_title = self._force_focus(app_name)
                if focused_title == "Found":
                    break
            return AppOutput(success=True, message=f"Opened {app_name} (Focus: {focused_title})")
        
        return AppOutput(success=False, message="Failed to launch.")

    def _force_focus(self, app_name: str) -> str:
        """Finds a window matching the app name and brings it to front."""
        import ctypes
        user32 = ctypes.windll.user32
        
        found_hwnd = None
        target = app_name.lower()
        
        # Simple heuristic mapping for common apps / window titles
        if "notepad" in target: target = "notepad"
        if "chrome" in target: target = "chrome"
        if "edge" in target: target = "edge"
        if "code" in target or "cursor" in target: target = "visual studio code"
        if "discord" in target: target = "discord"
        if "f1" in target: target = "f1"
        
        def callback(hwnd, extra):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value.lower()
            
            if title and (target in title or title in target):
                nonlocal found_hwnd
                found_hwnd = hwnd
                return False # Stop enumeration
            return True
            
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        user32.EnumWindows(WNDENUMPROC(callback), 0)
        
        if found_hwnd:
            # Restore if minimized
            user32.ShowWindow(found_hwnd, 9) # SW_RESTORE
            user32.SetForegroundWindow(found_hwnd)
            # Optional: maximize after focusing (helps remote automation).
            maximize = str(os.environ.get("ORBIT_MAXIMIZE_ON_FOCUS", "1")).strip().lower() in {"1","true","yes","on"}
            if maximize:
                user32.ShowWindow(found_hwnd, 3) # SW_MAXIMIZE
            return "Found"
        return "Not Found"

    def _close_app(self, app_name: str) -> AppOutput:
        try:
            app_close(app_name, match_closest=True, output=False)
            return AppOutput(success=True, message=f"Closed {app_name}")
        except Exception as e:
            return AppOutput(success=False, message=f"Failed to close {app_name}: {e}")
