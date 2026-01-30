"""
Multi-Awareness: Workspace Context Manager

Maintains a global view of the user's workspace:
- Active windows and their titles
- Open browser tabs (if accessible)
- Recent file edits
- Running processes
- Session history with summarization

This enables Orbit to maintain context across multiple applications
and long-running sessions.
"""

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from collections import deque
import subprocess


@dataclass
class WindowInfo:
    """Information about an open window."""
    title: str
    process_name: str
    hwnd: Optional[int] = None
    is_active: bool = False
    last_seen: float = field(default_factory=time.time)


@dataclass 
class FileActivity:
    """Track file read/write activity."""
    path: str
    action: str  # 'read', 'write', 'edit'
    timestamp: float
    summary: Optional[str] = None


@dataclass
class SessionMemory:
    """Summarized memory of past interactions."""
    summary: str
    key_facts: List[str]
    timestamp: float
    interaction_count: int


class WorkspaceContext:
    """
    Maintains awareness of the user's workspace state.
    
    Features:
    - Track open windows/applications
    - Monitor file activity
    - Maintain session history with summarization
    - Persist context across restarts
    """
    
    def __init__(self, context_path: Path = None, max_history: int = 50):
        self.context_path = context_path or Path("data/workspace_context.json")
        self.context_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.max_history = max_history
        
        # Current state
        self.windows: Dict[str, WindowInfo] = {}
        self.file_activities: deque = deque(maxlen=100)
        self.interaction_history: deque = deque(maxlen=max_history)
        self.session_memories: List[SessionMemory] = []
        
        # Session tracking
        self.session_start = time.time()
        self.interaction_count = 0
        self.current_focus: Optional[str] = None
        
        # Load persisted context
        self._load_context()
    
    def _load_context(self):
        """Load context from disk."""
        if self.context_path.exists():
            try:
                with open(self.context_path, 'r', encoding="utf-8") as f:
                    data = json.load(f)
                    self.session_memories = [
                        SessionMemory(**m) for m in data.get('session_memories', [])
                    ]
                    # Restore recent file activities
                    for fa in data.get('recent_files', []):
                        self.file_activities.append(FileActivity(**fa))
            except Exception as e:
                print(f"[WorkspaceContext] Failed to load context: {e}")
    
    def _save_context(self):
        """Persist context to disk."""
        try:
            data = {
                'session_memories': [asdict(m) for m in self.session_memories[-10:]],
                'recent_files': [asdict(fa) for fa in list(self.file_activities)[-20:]],
                'last_saved': time.time()
            }
            with open(self.context_path, 'w', encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[WorkspaceContext] Failed to save context: {e}")
    
    def get_open_windows(self) -> List[WindowInfo]:
        """
        Get list of currently open windows.
        Uses Windows-specific APIs via ctypes.
        """
        windows = []
        
        try:
            import ctypes
            from ctypes import wintypes
            
            user32 = ctypes.windll.user32
            
            # Get foreground window
            foreground_hwnd = user32.GetForegroundWindow()
            
            def enum_callback(hwnd, results):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        title = buff.value
                        
                        if title and not title.startswith('Microsoft Text Input'):
                            # Get process name
                            pid = wintypes.DWORD()
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            
                            process_name = "unknown"
                            try:
                                import psutil
                                proc = psutil.Process(pid.value)
                                process_name = proc.name()
                            except:
                                pass
                            
                            windows.append(WindowInfo(
                                title=title[:100],  # Truncate long titles
                                process_name=process_name,
                                hwnd=hwnd,
                                is_active=(hwnd == foreground_hwnd)
                            ))
                return True
            
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
            
        except Exception as e:
            print(f"[WorkspaceContext] Failed to enumerate windows: {e}")
        
        self.windows = {w.title: w for w in windows}
        return windows
    
    def get_active_window(self) -> Optional[WindowInfo]:
        """Get the currently focused window."""
        windows = self.get_open_windows()
        for w in windows:
            if w.is_active:
                self.current_focus = w.title
                return w
        return None
    
    def record_file_activity(self, path: str, action: str, summary: str = None):
        """Record a file read/write/edit action."""
        activity = FileActivity(
            path=path,
            action=action,
            timestamp=time.time(),
            summary=summary
        )
        self.file_activities.append(activity)
    
    def record_interaction(self, user_input: str, agent_response: str, task_summary: str = None):
        """Record an interaction for context building."""
        self.interaction_count += 1
        self.interaction_history.append({
            'timestamp': time.time(),
            'user': user_input[:200],  # Truncate
            'agent': agent_response[:500],
            'task': task_summary
        })
        
        # Auto-save periodically
        if self.interaction_count % 5 == 0:
            self._save_context()
    
    def get_context_summary(self, max_tokens: int = 500) -> str:
        """
        Generate a context summary for the LLM.
        
        This is the key output - a compressed view of:
        - Current workspace state
        - Recent activity
        - Relevant memories
        """
        parts = []
        
        # 1. Active Window
        active = self.get_active_window()
        if active:
            parts.append(f"**Active Window:** {active.title} ({active.process_name})")
        
        # 2. Open Applications (summary)
        app_counts = {}
        for w in self.windows.values():
            app_counts[w.process_name] = app_counts.get(w.process_name, 0) + 1
        
        if app_counts:
            apps = [f"{name}({count})" for name, count in sorted(app_counts.items(), key=lambda x: -x[1])[:5]]
            parts.append(f"**Open Apps:** {', '.join(apps)}")
        
        # 3. Recent File Activity
        recent_files = list(self.file_activities)[-5:]
        if recent_files:
            file_strs = [f"{fa.action}: {Path(fa.path).name}" for fa in recent_files]
            parts.append(f"**Recent Files:** {', '.join(file_strs)}")
        
        # 4. Session Info
        session_duration = (time.time() - self.session_start) / 60
        parts.append(f"**Session:** {self.interaction_count} interactions, {session_duration:.0f} min")
        
        # 5. Recent Interaction Summary (last 3)
        recent = list(self.interaction_history)[-3:]
        if recent:
            summaries = []
            for r in recent:
                task = r.get('task') or r.get('user', '')[:50]
                summaries.append(f"- {task}")
            parts.append(f"**Recent Tasks:**\n" + "\n".join(summaries))
        
        return "\n".join(parts)
    
    async def create_session_summary(self, llm_client=None) -> SessionMemory:
        """
        Create a compressed summary of the current session.
        Uses LLM if available, otherwise uses heuristics.
        """
        interactions = list(self.interaction_history)
        
        if not interactions:
            return None
        
        # Simple heuristic summary (no LLM needed)
        key_facts = []
        tasks_mentioned = set()
        files_touched = set()
        
        for interaction in interactions:
            if interaction.get('task'):
                tasks_mentioned.add(interaction['task'])
            user_msg = interaction.get('user', '')
            # Extract key phrases
            for keyword in ['create', 'build', 'fix', 'update', 'add', 'remove', 'search']:
                if keyword in user_msg.lower():
                    key_facts.append(user_msg[:100])
                    break
        
        for fa in self.file_activities:
            files_touched.add(Path(fa.path).name)
        
        summary = f"Session with {len(interactions)} interactions. "
        if tasks_mentioned:
            summary += f"Tasks: {', '.join(list(tasks_mentioned)[:5])}. "
        if files_touched:
            summary += f"Files: {', '.join(list(files_touched)[:5])}."
        
        memory = SessionMemory(
            summary=summary,
            key_facts=list(key_facts)[:10],
            timestamp=time.time(),
            interaction_count=len(interactions)
        )
        
        self.session_memories.append(memory)
        self._save_context()
        
        return memory
    
    def get_relevant_memories(self, query: str, limit: int = 3) -> List[SessionMemory]:
        """
        Retrieve memories relevant to the current query.
        Simple keyword matching for now (could use embeddings later).
        """
        if not self.session_memories:
            return []
        
        query_words = set(query.lower().split())
        
        scored = []
        for mem in self.session_memories:
            # Score by keyword overlap
            mem_words = set(mem.summary.lower().split())
            mem_words.update(word.lower() for fact in mem.key_facts for word in fact.split())
            
            overlap = len(query_words & mem_words)
            if overlap > 0:
                scored.append((overlap, mem))
        
        scored.sort(key=lambda x: -x[0])
        return [mem for _, mem in scored[:limit]]
    
    def snapshot(self) -> Dict[str, Any]:
        """Take a complete snapshot of current workspace state."""
        return {
            'timestamp': time.time(),
            'active_window': asdict(self.get_active_window()) if self.get_active_window() else None,
            'window_count': len(self.windows),
            'open_apps': list(set(w.process_name for w in self.windows.values())),
            'recent_files': [asdict(fa) for fa in list(self.file_activities)[-10:]],
            'interaction_count': self.interaction_count,
            'session_duration_min': (time.time() - self.session_start) / 60
        }
