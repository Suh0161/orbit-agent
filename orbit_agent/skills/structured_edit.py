"""
Structured Edit Skill - Line-Based File Editing

Line-based file editing with:
1. View file with line numbers
2. Edit specific line ranges
3. Validation before/after edits

This is usually more reliable than pure text replacement because:
- LLMs can reference exact line numbers
- No ambiguity about "which occurrence"
- Easy to show context around the edit
"""

import asyncio
from pathlib import Path
from typing import Type, Optional, List
from pydantic import BaseModel, Field
import aiofiles
from enum import Enum


from orbit_agent.skills.base import BaseSkill, SkillConfig


class EditAction(str, Enum):
    VIEW = "view"           # View file with line numbers
    EDIT = "edit"           # Replace specific lines
    INSERT = "insert"       # Insert lines after a specific line
    DELETE = "delete"       # Delete specific lines
    SEARCH = "search"       # Search for pattern in file


class StructuredEditInput(BaseModel):
    action: EditAction = Field(description="Action to perform: 'view', 'edit', 'insert', 'delete', 'search'")
    path: str = Field(description="Absolute path to the file")
    start_line: Optional[int] = Field(default=None, description="Starting line number (1-indexed)")
    end_line: Optional[int] = Field(default=None, description="Ending line number (1-indexed), inclusive")
    new_content: Optional[str] = Field(default=None, description="New content for edit/insert actions")
    pattern: Optional[str] = Field(default=None, description="Search pattern for 'search' action")
    context_lines: int = Field(default=3, description="Number of context lines to show around matches")


class StructuredEditOutput(BaseModel):
    success: bool
    content: Optional[str] = Field(default=None, description="File content or search results")
    lines_affected: Optional[List[int]] = Field(default=None, description="Line numbers affected by the edit")
    total_lines: Optional[int] = Field(default=None, description="Total lines in the file")
    error: Optional[str] = None


class StructuredEditSkill(BaseSkill):
    """
    Structured file editing.
    
    Key features:
    - View file with line numbers
    - Edit specific line ranges (no ambiguity)
    - Insert/Delete at specific positions
    - Search with context
    """
    
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="structured_edit",
            description="Structured file editing with line numbers. Use 'view' to see file with line numbers, 'edit' to replace lines, 'insert' to add lines, 'delete' to remove lines, 'search' to find patterns.",
            permissions_required=["file_write"]
        )
    
    @property
    def input_schema(self) -> Type[BaseModel]:
        return StructuredEditInput
    
    @property
    def output_schema(self) -> Type[BaseModel]:
        return StructuredEditOutput
    
    async def execute(self, inputs: StructuredEditInput) -> StructuredEditOutput:
        path = Path(inputs.path)
        
        if inputs.action == EditAction.VIEW:
            return await self._view_file(path, inputs.start_line, inputs.end_line)
        elif inputs.action == EditAction.EDIT:
            return await self._edit_lines(path, inputs.start_line, inputs.end_line, inputs.new_content)
        elif inputs.action == EditAction.INSERT:
            return await self._insert_lines(path, inputs.start_line, inputs.new_content)
        elif inputs.action == EditAction.DELETE:
            return await self._delete_lines(path, inputs.start_line, inputs.end_line)
        elif inputs.action == EditAction.SEARCH:
            return await self._search_file(path, inputs.pattern, inputs.context_lines)
        else:
            return StructuredEditOutput(success=False, error=f"Unknown action: {inputs.action}")
    
    async def _view_file(
        self, 
        path: Path, 
        start_line: Optional[int] = None, 
        end_line: Optional[int] = None
    ) -> StructuredEditOutput:
        """View file with line numbers."""
        if not path.exists():
            return StructuredEditOutput(success=False, error=f"File not found: {path}")
        
        try:
            async with aiofiles.open(path, mode='r', encoding='utf-8') as f:
                lines = await f.readlines()
            
            total = len(lines)
            start = (start_line or 1) - 1  # Convert to 0-indexed
            end = end_line or total
            
            # Clamp to valid range
            start = max(0, min(start, total - 1))
            end = max(start + 1, min(end, total))
            
            # Format with line numbers
            numbered_lines = []
            for i, line in enumerate(lines[start:end], start=start + 1):
                numbered_lines.append(f"{i:4d} | {line.rstrip()}")
            
            content = "\n".join(numbered_lines)
            
            return StructuredEditOutput(
                success=True,
                content=content,
                total_lines=total,
                lines_affected=list(range(start + 1, end + 1))
            )
            
        except Exception as e:
            return StructuredEditOutput(success=False, error=str(e))
    
    async def _edit_lines(
        self, 
        path: Path, 
        start_line: Optional[int], 
        end_line: Optional[int], 
        new_content: Optional[str]
    ) -> StructuredEditOutput:
        """Replace lines between start_line and end_line with new_content."""
        if not path.exists():
            return StructuredEditOutput(success=False, error=f"File not found: {path}")
        
        if start_line is None or end_line is None:
            return StructuredEditOutput(success=False, error="start_line and end_line are required for edit")
        
        if new_content is None:
            return StructuredEditOutput(success=False, error="new_content is required for edit")
        
        try:
            async with aiofiles.open(path, mode='r', encoding='utf-8') as f:
                lines = await f.readlines()
            
            total = len(lines)
            start = start_line - 1  # 0-indexed
            end = end_line  # end_line is inclusive, so we use it directly for slicing
            
            if start < 0 or end > total or start >= end:
                return StructuredEditOutput(
                    success=False, 
                    error=f"Invalid line range: {start_line}-{end_line} (file has {total} lines)"
                )
            
            # Create new content lines
            new_lines = new_content.split('\n')
            # Ensure each line ends with newline except possibly the last
            new_lines = [line + '\n' if not line.endswith('\n') else line for line in new_lines]
            
            # Replace the lines
            lines[start:end] = new_lines
            
            # Write back
            async with aiofiles.open(path, mode='w', encoding='utf-8') as f:
                await f.writelines(lines)
            
            return StructuredEditOutput(
                success=True,
                content=f"Replaced lines {start_line}-{end_line} with {len(new_lines)} new lines",
                total_lines=len(lines),
                lines_affected=list(range(start_line, start_line + len(new_lines)))
            )
            
        except Exception as e:
            return StructuredEditOutput(success=False, error=str(e))
    
    async def _insert_lines(
        self, 
        path: Path, 
        after_line: Optional[int], 
        new_content: Optional[str]
    ) -> StructuredEditOutput:
        """Insert new_content after after_line."""
        if not path.exists():
            return StructuredEditOutput(success=False, error=f"File not found: {path}")
        
        if after_line is None:
            return StructuredEditOutput(success=False, error="start_line (insert after) is required")
        
        if new_content is None:
            return StructuredEditOutput(success=False, error="new_content is required")
        
        try:
            async with aiofiles.open(path, mode='r', encoding='utf-8') as f:
                lines = await f.readlines()
            
            # Insert position (0-indexed, after the specified line)
            insert_pos = after_line  # after_line is 1-indexed, so this inserts after that line
            
            # Create new content lines
            new_lines = new_content.split('\n')
            new_lines = [line + '\n' if not line.endswith('\n') else line for line in new_lines]
            
            # Insert
            for i, line in enumerate(new_lines):
                lines.insert(insert_pos + i, line)
            
            # Write back
            async with aiofiles.open(path, mode='w', encoding='utf-8') as f:
                await f.writelines(lines)
            
            return StructuredEditOutput(
                success=True,
                content=f"Inserted {len(new_lines)} lines after line {after_line}",
                total_lines=len(lines),
                lines_affected=list(range(after_line + 1, after_line + 1 + len(new_lines)))
            )
            
        except Exception as e:
            return StructuredEditOutput(success=False, error=str(e))
    
    async def _delete_lines(
        self, 
        path: Path, 
        start_line: Optional[int], 
        end_line: Optional[int]
    ) -> StructuredEditOutput:
        """Delete lines from start_line to end_line (inclusive)."""
        if not path.exists():
            return StructuredEditOutput(success=False, error=f"File not found: {path}")
        
        if start_line is None or end_line is None:
            return StructuredEditOutput(success=False, error="start_line and end_line are required")
        
        try:
            async with aiofiles.open(path, mode='r', encoding='utf-8') as f:
                lines = await f.readlines()
            
            total = len(lines)
            start = start_line - 1
            end = end_line
            
            if start < 0 or end > total or start >= end:
                return StructuredEditOutput(
                    success=False, 
                    error=f"Invalid line range: {start_line}-{end_line}"
                )
            
            deleted_count = end - start
            del lines[start:end]
            
            async with aiofiles.open(path, mode='w', encoding='utf-8') as f:
                await f.writelines(lines)
            
            return StructuredEditOutput(
                success=True,
                content=f"Deleted {deleted_count} lines ({start_line}-{end_line})",
                total_lines=len(lines)
            )
            
        except Exception as e:
            return StructuredEditOutput(success=False, error=str(e))
    
    async def _search_file(
        self, 
        path: Path, 
        pattern: Optional[str], 
        context_lines: int = 3
    ) -> StructuredEditOutput:
        """Search for pattern in file, showing context around matches."""
        if not path.exists():
            return StructuredEditOutput(success=False, error=f"File not found: {path}")
        
        if not pattern:
            return StructuredEditOutput(success=False, error="pattern is required for search")
        
        try:
            async with aiofiles.open(path, mode='r', encoding='utf-8') as f:
                lines = await f.readlines()
            
            matches = []
            matched_lines = []
            
            for i, line in enumerate(lines):
                if pattern.lower() in line.lower():
                    matched_lines.append(i + 1)
                    
                    # Get context
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    
                    context = []
                    for j in range(start, end):
                        prefix = ">>> " if j == i else "    "
                        context.append(f"{j + 1:4d} {prefix}{lines[j].rstrip()}")
                    
                    matches.append("\n".join(context))
            
            if not matches:
                return StructuredEditOutput(
                    success=True,
                    content=f"No matches found for '{pattern}'",
                    total_lines=len(lines)
                )
            
            content = f"Found {len(matches)} matches for '{pattern}':\n\n" + "\n---\n".join(matches)
            
            return StructuredEditOutput(
                success=True,
                content=content,
                total_lines=len(lines),
                lines_affected=matched_lines
            )
            
        except Exception as e:
            return StructuredEditOutput(success=False, error=str(e))
