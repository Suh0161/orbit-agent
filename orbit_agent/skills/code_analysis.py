import ast
from pathlib import Path
from typing import Type, List, Optional, Dict
from pydantic import BaseModel, Field

from orbit_agent.skills.base import BaseSkill, SkillConfig

class AnalysisInput(BaseModel):
    path: str = Field(description="Absolute path to python file")

class CodeSymbol(BaseModel):
    name: str
    type: str # class, function, or async_function
    lineno: int
    docstring: Optional[str] = None

class AnalysisOutput(BaseModel):
    symbols: List[CodeSymbol]
    summary: str
    error: str = ""

class CodeAnalysisSkill(BaseSkill):
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="code_analyze",
            description="Analyzes a python file to extract classes, functions, and docstrings.",
            permissions_required=["file_read"]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return AnalysisInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return AnalysisOutput

    async def execute(self, inputs: AnalysisInput) -> AnalysisOutput:
        path = Path(inputs.path)
        if not path.exists():
            return AnalysisOutput(symbols=[], summary="", error="File not found")
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            
            tree = ast.parse(content)
            symbols = []
            
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if isinstance(node, ast.ClassDef):
                        stype = "class"
                    elif isinstance(node, ast.AsyncFunctionDef):
                        stype = "async_function"
                    else:
                        stype = "function"
                        
                    docstring = ast.get_docstring(node)
                    symbols.append(CodeSymbol(
                        name=node.name,
                        type=stype,
                        lineno=node.lineno,
                        docstring=docstring
                    ))
            
            # Create a textual summary
            summary = f"File {path.name} contains {len(symbols)} definitions.\n"
            for s in symbols:
                summary += f" - {s.type} {s.name} (Line {s.lineno})\n"
                
            return AnalysisOutput(symbols=symbols, summary=summary)

        except Exception as e:
            return AnalysisOutput(symbols=[], summary="", error=str(e))
