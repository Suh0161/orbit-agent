"""
Codebase Search Skill

Provides powerful codebase search capabilities:
1. Grep-style pattern search across all files
2. Symbol/function name search
3. File name search
4. Semantic search (future: with embeddings)

This helps the LLM quickly find relevant files and code sections.
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Type, Optional, List
from pydantic import BaseModel, Field
from enum import Enum


from orbit_agent.skills.base import BaseSkill, SkillConfig


class SearchMode(str, Enum):
    GREP = "grep"               # Search file contents
    FILENAME = "filename"       # Search file names
    SYMBOL = "symbol"           # Search for function/class definitions
    STRUCTURE = "structure"     # Get project structure


class CodeSearchInput(BaseModel):
    mode: SearchMode = Field(description="Search mode: 'grep' (contents), 'filename', 'symbol' (definitions), 'structure' (project tree)")
    query: str = Field(description="Search query or pattern")
    path: str = Field(default=".", description="Root path to search from")
    extensions: Optional[List[str]] = Field(default=None, description="File extensions to include (e.g., ['.py', '.js'])")
    max_results: int = Field(default=20, description="Maximum number of results to return")
    case_sensitive: bool = Field(default=False, description="Case-sensitive search")
    context_lines: int = Field(default=2, description="Lines of context around matches (for grep)")


class SearchMatch(BaseModel):
    file: str
    line_number: Optional[int] = None
    content: Optional[str] = None
    context: Optional[str] = None


class CodeSearchOutput(BaseModel):
    success: bool
    matches: Optional[List[SearchMatch]] = Field(default=None)
    total_matches: int = 0
    error: Optional[str] = None


class CodeSearchSkill(BaseSkill):
    """
    Powerful codebase search for helping LLMs find relevant code.
    
    Modes:
    - grep: Search file contents for a pattern
    - filename: Find files by name pattern
    - symbol: Find function/class definitions
    - structure: Get project directory tree
    """
    
    # Directories to always skip
    SKIP_DIRS = {
        '__pycache__', 'node_modules', '.git', '.venv', 'venv', 
        'env', '.env', 'dist', 'build', '.idea', '.vscode',
        '.pytest_cache', '.mypy_cache', 'htmlcov', '.tox',
        'egg-info', '.eggs'
    }
    
    # Binary extensions to skip
    SKIP_EXTENSIONS = {
        '.pyc', '.pyo', '.exe', '.dll', '.so', '.dylib',
        '.png', '.jpg', '.jpeg', '.gif', '.ico', '.bmp',
        '.mp3', '.mp4', '.wav', '.avi', '.mov',
        '.zip', '.tar', '.gz', '.rar', '.7z',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx'
    }
    
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="code_search",
            description="Search the codebase: 'grep' for content, 'filename' for files, 'symbol' for definitions, 'structure' for project tree.",
            permissions_required=["file_read"]
        )
    
    @property
    def input_schema(self) -> Type[BaseModel]:
        return CodeSearchInput
    
    @property
    def output_schema(self) -> Type[BaseModel]:
        return CodeSearchOutput
    
    async def execute(self, inputs: CodeSearchInput) -> CodeSearchOutput:
        root = Path(inputs.path).resolve()
        
        if not root.exists():
            return CodeSearchOutput(success=False, error=f"Path not found: {inputs.path}")
        
        if inputs.mode == SearchMode.GREP:
            return await self._grep_search(root, inputs)
        elif inputs.mode == SearchMode.FILENAME:
            return await self._filename_search(root, inputs)
        elif inputs.mode == SearchMode.SYMBOL:
            return await self._symbol_search(root, inputs)
        elif inputs.mode == SearchMode.STRUCTURE:
            return await self._structure_search(root, inputs)
        else:
            return CodeSearchOutput(success=False, error=f"Unknown mode: {inputs.mode}")
    
    def _should_skip_path(self, path: Path) -> bool:
        """Check if this path should be skipped."""
        for part in path.parts:
            if part in self.SKIP_DIRS:
                return True
        if path.suffix.lower() in self.SKIP_EXTENSIONS:
            return True
        return False
    
    def _matches_extensions(self, path: Path, extensions: Optional[List[str]]) -> bool:
        """Check if file matches extension filter."""
        if not extensions:
            return True
        return path.suffix.lower() in [ext.lower() for ext in extensions]
    
    async def _grep_search(self, root: Path, inputs: CodeSearchInput) -> CodeSearchOutput:
        """Search file contents for pattern."""
        matches = []
        flags = 0 if inputs.case_sensitive else re.IGNORECASE
        
        try:
            pattern = re.compile(inputs.query, flags)
        except re.error as e:
            # Fallback to literal search if regex is invalid
            pattern = re.compile(re.escape(inputs.query), flags)
        
        for file_path in root.rglob('*'):
            if len(matches) >= inputs.max_results:
                break
            
            if not file_path.is_file():
                continue
            
            if self._should_skip_path(file_path):
                continue
            
            if not self._matches_extensions(file_path, inputs.extensions):
                continue
            
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                
                for i, line in enumerate(lines):
                    if pattern.search(line):
                        # Get context
                        start = max(0, i - inputs.context_lines)
                        end = min(len(lines), i + inputs.context_lines + 1)
                        context_lines = []
                        for j in range(start, end):
                            prefix = ">>> " if j == i else "    "
                            context_lines.append(f"{j + 1:4d} {prefix}{lines[j].rstrip()}")
                        
                        matches.append(SearchMatch(
                            file=str(file_path.relative_to(root)),
                            line_number=i + 1,
                            content=line.strip()[:200],  # Truncate long lines
                            context="\n".join(context_lines)
                        ))
                        
                        if len(matches) >= inputs.max_results:
                            break
                            
            except Exception:
                continue  # Skip files that can't be read
        
        return CodeSearchOutput(
            success=True,
            matches=matches,
            total_matches=len(matches)
        )
    
    async def _filename_search(self, root: Path, inputs: CodeSearchInput) -> CodeSearchOutput:
        """Search for files by name pattern."""
        matches = []
        flags = 0 if inputs.case_sensitive else re.IGNORECASE
        
        try:
            pattern = re.compile(inputs.query, flags)
        except re.error:
            pattern = re.compile(re.escape(inputs.query), flags)
        
        for file_path in root.rglob('*'):
            if len(matches) >= inputs.max_results:
                break
            
            if self._should_skip_path(file_path):
                continue
            
            if pattern.search(file_path.name):
                matches.append(SearchMatch(
                    file=str(file_path.relative_to(root)),
                    content=f"{'[DIR]' if file_path.is_dir() else '[FILE]'} {file_path.name}"
                ))
        
        return CodeSearchOutput(
            success=True,
            matches=matches,
            total_matches=len(matches)
        )
    
    async def _symbol_search(self, root: Path, inputs: CodeSearchInput) -> CodeSearchOutput:
        """Search for function/class definitions."""
        matches = []
        flags = 0 if inputs.case_sensitive else re.IGNORECASE
        
        # Patterns for common definition types
        python_patterns = [
            rf"^\s*def\s+{re.escape(inputs.query)}\s*\(",  # Python function
            rf"^\s*class\s+{re.escape(inputs.query)}\s*[:\(]",  # Python class
            rf"^\s*async\s+def\s+{re.escape(inputs.query)}\s*\(",  # Python async function
        ]
        
        js_patterns = [
            rf"^\s*(export\s+)?(async\s+)?function\s+{re.escape(inputs.query)}\s*\(",  # JS function
            rf"^\s*(export\s+)?class\s+{re.escape(inputs.query)}\s*[{{\<]",  # JS class
            rf"^\s*(const|let|var)\s+{re.escape(inputs.query)}\s*=\s*(async\s+)?\(",  # Arrow function
        ]
        
        all_patterns = python_patterns + js_patterns
        compiled_patterns = [re.compile(p, flags) for p in all_patterns]
        
        for file_path in root.rglob('*'):
            if len(matches) >= inputs.max_results:
                break
            
            if not file_path.is_file():
                continue
            
            if self._should_skip_path(file_path):
                continue
            
            # Filter to likely code files
            if file_path.suffix.lower() not in ['.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.go', '.rs']:
                if inputs.extensions is None:
                    continue
            
            if not self._matches_extensions(file_path, inputs.extensions):
                continue
            
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                
                for i, line in enumerate(lines):
                    for pattern in compiled_patterns:
                        if pattern.search(line):
                            # Get a few lines of context
                            start = max(0, i)
                            end = min(len(lines), i + 5)
                            context = "".join(lines[start:end])
                            
                            matches.append(SearchMatch(
                                file=str(file_path.relative_to(root)),
                                line_number=i + 1,
                                content=line.strip()[:200],
                                context=context[:500]
                            ))
                            break
                            
            except Exception:
                continue
        
        return CodeSearchOutput(
            success=True,
            matches=matches,
            total_matches=len(matches)
        )
    
    async def _structure_search(self, root: Path, inputs: CodeSearchInput) -> CodeSearchOutput:
        """Get project structure as a tree."""
        tree_lines = []
        
        def build_tree(path: Path, prefix: str = "", depth: int = 0, max_depth: int = 4):
            if depth >= max_depth:
                return
            
            try:
                entries = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            except PermissionError:
                return
            
            # Filter entries
            entries = [e for e in entries if not self._should_skip_path(e)]
            
            for i, entry in enumerate(entries[:50]):  # Limit entries per directory
                is_last = i == len(entries) - 1
                connector = "└── " if is_last else "├── "
                
                if entry.is_dir():
                    tree_lines.append(f"{prefix}{connector}📁 {entry.name}/")
                    extension = "    " if is_last else "│   "
                    build_tree(entry, prefix + extension, depth + 1, max_depth)
                else:
                    size = entry.stat().st_size
                    size_str = f"{size:,}B" if size < 1024 else f"{size // 1024:,}KB"
                    tree_lines.append(f"{prefix}{connector}📄 {entry.name} ({size_str})")
        
        tree_lines.append(f"📁 {root.name}/")
        build_tree(root)
        
        content = "\n".join(tree_lines[:inputs.max_results * 10])  # Limit total output
        
        return CodeSearchOutput(
            success=True,
            matches=[SearchMatch(file=str(root), content=content)],
            total_matches=len(tree_lines)
        )
