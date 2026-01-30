import asyncio
from typing import Type, List, Optional
from pydantic import BaseModel, Field
from duckduckgo_search import DDGS
from orbit_agent.skills.base import BaseSkill, SkillConfig

class SearchInput(BaseModel):
    query: str = Field(description="The search query")
    max_results: int = Field(default=5, description="Number of results to return")

class SearchResult(BaseModel):
    title: str
    href: str
    body: str

class SearchOutput(BaseModel):
    results: List[SearchResult]
    error: str = ""

class WebSearchSkill(BaseSkill):
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="web_search",
            description="Searches the web using DuckDuckGo. Returns titles, links, and snippets.",
            permissions_required=["net_access"] # Considered safe enough for broad net_access or make specific
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return SearchInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return SearchOutput

    async def execute(self, inputs: SearchInput) -> SearchOutput:
        try:
            # helper to run sync DDGS in async executor
            def _search():
                results = []
                with DDGS() as ddgs:
                    # 'text' is the basic search method
                    # In newer versions it might be chat/text.
                    # ddgs.text returns an iterator
                    for r in ddgs.text(inputs.query, max_results=inputs.max_results):
                        results.append(SearchResult(
                            title=r.get("title", ""),
                            href=r.get("href", ""),
                            body=r.get("body", "")
                        ))
                return results

            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(None, _search)
            
            return SearchOutput(results=results)

        except Exception as e:
            return SearchOutput(results=[], error=str(e))
