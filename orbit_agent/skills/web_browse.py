import asyncio
import httpx
from typing import Type
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup

from orbit_agent.skills.base import BaseSkill, SkillConfig

class BrowserInput(BaseModel):
    url: str = Field(description="URL to browse")
    extract_main_content: bool = Field(default=True, description="If true, tries to extract only article text.")

class BrowserOutput(BaseModel):
    title: str
    content: str
    links: list[str] = []
    error: str = ""

class WebBrowseSkill(BaseSkill):
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="web_browse",
            description="Visits a webpage and extracts clean content and links.",
            permissions_required=["net_access"]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return BrowserInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return BrowserOutput

    async def execute(self, inputs: BrowserInput) -> BrowserOutput:
        try:
             async with httpx.AsyncClient() as client:
                # Add headers to avoid some bot blocks
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                }
                response = await client.get(inputs.url, follow_redirects=True, timeout=30.0, headers=headers)
                
                if response.status_code >= 400:
                    return BrowserOutput(title="", content="", error=f"HTTP Status {response.status_code}")

                html = response.text
                soup = BeautifulSoup(html, 'html.parser')
                
                # Cleanup
                for script in soup(["script", "style", "nav", "footer", "iframe"]):
                    script.decompose()
                
                title = soup.title.string if soup.title else ""
                
                text = soup.get_text(separator="\n")
                
                # Simple line cleanup
                lines = (line.strip() for line in text.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                clean_text = '\n'.join(chunk for chunk in chunks if chunk)
                
                # Extract links
                links = [a.get('href') for a in soup.find_all('a', href=True)]
                # Filter useful links? Keep all for now.
                links = list(set(links))[:50] # Limit to 50
                
                return BrowserOutput(title=title, content=clean_text[:10000], links=links) # Limit text length

        except Exception as e:
            return BrowserOutput(title="", content="", error=str(e))
