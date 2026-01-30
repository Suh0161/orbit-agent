from typing import Type, Optional
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

from orbit_agent.skills.base import BaseSkill, SkillConfig

class BrowserInput(BaseModel):
    action: str = Field(description="Action: 'launch', 'navigate', 'click', 'type', 'read', 'press', 'submit', 'close', 'new_tab', 'switch_tab'")
    url: Optional[str] = Field(default=None, description="URL to navigate to")
    selector: Optional[str] = Field(default=None, description="CSS selector or Text to find element (e.g. 'text=Login' or '#submit-btn').")
    text: Optional[str] = Field(default=None, description="Text to type, or Key name for 'press' action (e.g. 'Enter').")
    headless: bool = Field(default=False, description="Run invisible? Default False (visible window)")
    tab_index: int = Field(default=0, description="Tab index to target (0-indexed). Default 0.")

class BrowserOutput(BaseModel):
    success: bool
    data: Optional[str] = None
    error: Optional[str] = None

class BrowserSkill(BaseSkill):
    def __init__(self):
        super().__init__()
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.pages: list[Page] = []  # List of open pages (tabs)

    # ... (Properties unchanged) ...
    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="browser_control",
            description="Control a phantom browser instance. Supports multiple tabs.",
            permissions_required=["browser_control"]
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return BrowserInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return BrowserOutput

    async def _ensure_browser(self, headless=False):
        if not self.playwright:
            self.playwright = await async_playwright().start()
        
        if not self.browser:
            print("[BrowserSkill] Launching Chromium (visible)...")
            self.browser = await self.playwright.chromium.launch(headless=False, slow_mo=500)
            self.context = await self.browser.new_context()
            page = await self.context.new_page()
            self.pages = [page]
            
    def _get_page(self, index: int) -> Page:
        if not self.pages: 
            raise Exception("Browser not running")
        if index < 0 or index >= len(self.pages):
            # Fallback to last active if index invalid? No, explicit error better.
            # actually, let's just default to last active if out of bounds to be nice?
            # No, strict.
            raise Exception(f"Tab index {index} out of bounds (0-{len(self.pages)-1})")
        return self.pages[index]

    async def execute(self, inputs: BrowserInput) -> BrowserOutput:
        try:
            await self._ensure_browser(inputs.headless)
            
            if inputs.action == "launch":
                return BrowserOutput(success=True, data="Browser launched.")
            
            elif inputs.action == "new_tab":
                page = await self.context.new_page()
                self.pages.append(page)
                idx = len(self.pages) - 1
                
                msg = f"New tab opened. Index: {idx}"
                
                if inputs.url:
                    await page.goto(inputs.url)
                     # Same Auto-Accept logic? Ideally refactor, but for now quick fix:
                    try:
                        await page.wait_for_load_state('domcontentloaded')
                    except: pass
                    msg += f". Navigated to {inputs.url}"
                
                return BrowserOutput(success=True, data=msg)
                
            elif inputs.action == "switch_tab":
                 # Just validation
                self._get_page(inputs.tab_index) 
                return BrowserOutput(success=True, data=f"Switched to tab {inputs.tab_index}") # Logical switch only

            elif inputs.action == "close":
                if self.browser:
                    await self.browser.close()
                    self.browser = None
                    self.pages = []
                return BrowserOutput(success=True, data="Browser closed")

            # --- Page Actions (Use self._get_page(inputs.tab_index)) ---
            page = self._get_page(inputs.tab_index)
            # Bring tab to front visually?
            await page.bring_to_front() 

            if inputs.action == "navigate":
                if not inputs.url: return BrowserOutput(success=False, error="URL required")
                await page.goto(inputs.url)
                
                # Auto-Accept Cookies (Robust)
                try:
                    await page.wait_for_load_state('domcontentloaded') 
                    
                    # CAPTCHA DETECTION
                    if await page.get_by_text("I'm not a robot", exact=False).count() > 0 or \
                       await page.get_by_text("reCAPTCHA", exact=False).count() > 0 or \
                       await page.get_by_text("Unusual traffic", exact=False).count() > 0:
                        
                        print(f"[BrowserSkill] 🚨 CAPTCHA DETECTED in Tab {inputs.tab_index}! 🚨")
                        while True:
                            if await page.get_by_text("Unusual traffic", exact=False).count() == 0 and \
                               await page.get_by_text("reCAPTCHA", exact=False).count() == 0:
                                break
                            await asyncio.sleep(1.0)

                    # Click Cookie Buttons
                    targets = ["Accept all", "Accept", "I agree", "Agree", "Allow all", "Allow", "Consent", "Got it", "Okay"]
                    for t in targets:
                        try:
                            btn = page.get_by_role("button", name=t, exact=False)
                            if await btn.count() > 0:
                                await btn.first.click(timeout=2000)
                                break
                        except: continue
                except: pass

                title = await page.title()
                return BrowserOutput(success=True, data=f"Navigated Tab {inputs.tab_index} to {title}")
                
            elif inputs.action == "click":
                if not inputs.selector: return BrowserOutput(success=False, error="Selector required")
                try:
                    await page.click(inputs.selector, timeout=2000)
                except:
                    await page.click(f"text={inputs.selector}", timeout=2000)   
                return BrowserOutput(success=True, data=f"Clicked '{inputs.selector}'")
            
            elif inputs.action in ["press", "submit"]:
                key = inputs.text if inputs.text else "Enter"
                await page.keyboard.press(key)
                return BrowserOutput(success=True, data=f"Pressed key: {key}")

            elif inputs.action == "type":
                if not inputs.selector or not inputs.text: return BrowserOutput(success=False, error="Selector and Text required")
                
                try:
                    await page.fill(inputs.selector, inputs.text.strip(), timeout=2000)
                except:
                    # Fallback logic simplified for space
                    await page.click(inputs.selector, timeout=1000)
                    await page.keyboard.type(inputs.text.strip())

                # Submit if requested via Newline
                msg = f"Typed '{inputs.text.strip()}'"
                if "\n" in inputs.text or inputs.text.endswith("\\n"):
                    await page.keyboard.press("Enter")
                    msg += " and pressed Enter"
                
                return BrowserOutput(success=True, data=msg)
                
            elif inputs.action == "read":
                content = await page.content()
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(content, "html.parser")
                    for tag in soup(["script", "style", "nav", "footer", "header", "form", "svg", "iframe", "noscript", "aside"]):
                        tag.decompose()
                    text = soup.get_text(separator="\n")
                    lines = [line.strip() for line in text.splitlines() if line.strip()]
                    clean_text = "\n".join(lines)
                    
                    # Sanitize for Windows Console
                    clean_text = clean_text.encode('ascii', 'ignore').decode('ascii')
                    
                    return BrowserOutput(success=True, data=clean_text[:5000] + ("..." if len(clean_text) > 5000 else ""))
                except ImportError:
                    import re
                    text = re.sub(r'<[^>]+>', ' ', content)
                    text = re.sub(r'\s+', ' ', text).strip()
                    return BrowserOutput(success=True, data=text[:2000] + "...") 
                
            return BrowserOutput(success=False, error=f"Unknown action: {inputs.action}")
            
        except Exception as e:
            return BrowserOutput(success=False, error=str(e))
