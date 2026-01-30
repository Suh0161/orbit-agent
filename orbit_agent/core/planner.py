import json
from typing import List
from uuid import uuid4
from orbit_agent.models.router import ModelRouter
from orbit_agent.models.base import Message
from orbit_agent.skills.registry import SkillRegistry
from orbit_agent.tasks.models import TaskStep

from orbit_agent.memory.base import MemoryInterface
from orbit_agent.memory.routine import RoutineMemory

class Planner:
    def __init__(self, router: ModelRouter, skills: SkillRegistry, memory: MemoryInterface, workspace_root: str):
        self.router = router
        self.skills = skills
        self.memory = memory
        self.workspace_root = str(workspace_root).replace("\\", "/")
        self.routines = RoutineMemory(memory.path.parent)

    async def plan(self, goal: str) -> List[TaskStep]:
        root = self.workspace_root
        
        # 0. Check Muscle Memory
        # cached_plan = self.routines.get_plan(goal)
        # if cached_plan:
        #     return cached_plan

        # 1. Recall relevant memories
        memories = await self.memory.search(goal, limit=3)
        context_str = ""
        if memories:
            context_str = "\nRelevant Past Context:\n"
            for m in memories:
                context_str += f"- {m['content']}\n"
        
        client = self.router.get_client("planning")
        
        skill_descriptions = self.skills.list_skills()
        skill_text = json.dumps(skill_descriptions, indent=2)
        
        system_prompt = f"""
        You are the Planner for Orbit Agent.
        Your job is to break down a user's Goal into a list of execution Steps.
        
        Available Skills:
        {skill_text}
        
        Output Format:
        Return a strictly valid JSON list of step objects.
        
        Example Output:
        [
            {{
                "id": "step_1",
                "skill_name": "shell_command",
                "skill_config": {{ "command": "mkdir project" }},
                "dependencies": []
            }},
            {{
                "id": "step_2",
                "skill_name": "file_write",
                "skill_config": {{ "path": "{root}/project/task.md", "content": "# Plan" }},
                "dependencies": ["step_1"]
            }}
        ]
        
        Rules:
        - Use dependencies to ensure order.
        - Do not invent skills.
        - Be efficient.
        - **COMPLETE DECOMPOSITION (CRITICAL):** You MUST output steps for EVERY part of the user's request. If they say "open Discord and go to LOL channel and join voice channel", you need: (1) open Discord, (2) wait, (3) screenshot with save_path, (4) som_vision to find LOL, (5) click, (6) wait, (7) screenshot, (8) som_vision to find voice channel, (9) click. Never output only the first one or two steps—every sub-goal (open app, go to X, do Y) must have explicit steps.
        - **Screenshot → Vision chain:** When a step takes a screenshot for a following vision step, use the SAME path in both. In the screenshot step use `skill_name: "computer_control"`, `skill_config: {{ "action": "screenshot", "save_path": "screenshots/step_1.png" }}`. In the next step use `som_vision` with `image_path: "screenshots/step_1.png"` (exact same path). The computer_control skill accepts `save_path` and will create that file; som_vision requires that path to exist.
        - Important: If a step produces a file (e.g. screenshot) and a later step needs it (e.g. vision), you MUST assign the SAME specific path to 'save_path' in the screenshot step and 'image_path' in the vision step. Do not invent different paths (e.g. discord_server_list.png) unless you use that EXACT path in the screenshot step too.
        - **Critical:** Do NOT output the content of the app (e.g. a list of groceries). Output the STEPS to build it.
        - **SELF-EXTENSION (New Skills):** If you lack a capability, you MAY create it using `skill_create`, then use the newly created skill later in the SAME plan.
          Example: Step A uses `skill_create(name='foo', description='...')`, Step B uses `foo(...)`. This is allowed even though `foo` was not available at plan start.
          Only create skills when clearly necessary; prefer existing skills first.
        
        Best Practices:
        - **RELIABILITY FIRST:** Your goal is 100% accuracy.
        - **Opening Apps:** Use action='press', key='win' -> Type Name -> Enter.
        - **Window state:** After opening/focusing an app you need to interact with, maximize it to avoid hidden/minimized UI:
          `computer_control(action='press', keys=['win','up'])` then `computer_control(action='wait', duration=1)`.
        - **Web Browsing:** Use `browser_control` skill. Prefer `duckduckgo.com` for searches to avoid CAPTCHAs. The browser window is separate from the user's mouse.
        - **Flight/Travel:** Do NOT just visit a homepage and stop. Construct a specific URL if possible. Example: `google.com/flights?q=flights+from+KUL+to+CGK`.
        - **Stocks/Finance:** Use direct URLs. `google.com/finance?q=AAPL` or `finance.yahoo.com/quote/TSLA`. Do not rely on generic search snippets for real-time prices.
        - **Multi-Tab Research:** To compare items (e.g. flight prices on different sites), use `action='new_tab'` for subsequent sites. Do not overwrite the current page if you need to compare. Use `tab_index=N` to switch.
        - **Navigating / Selecting:** To click a specific item (e.g. Server, Channel, Button), `visual_interact(action='hover_and_confirm', confirm_text='Unique Text')` is the gold standard for desktop apps.
        - **Visual Fallback:** Use `visual_interact` with 'double_click' for desktop icons.
        - **CRITICAL - WAIT States:** Whenever you perform an action that changes the view (e.g. Click Server, Open App, Open Game Page), you MUST insert a `computer_control(action='wait', duration=5.0)` step immediately after. Vision is too fast; the UI needs time to render.

        Workflow Protocols (Human-Like Behavior):
        - **SOFTWARE ENGINEERING / CODING (PRIORITY, END-TO-END):**
           If asked to "build an app", "write code", "create a website", "make a tool", "ship a feature":
           
           **Goal:** produce a REAL repo, not a toy. Minimum deliverable includes:
           - Modular `src/` (or package) with multiple files (not 1-2 files total)
           - `tests/` with at least a smoke/integration test
           - formatter + linter + typecheck (if applicable)
           - runnable commands (dev + test + lint) documented in README
           - `.env.example` if any secrets are needed
           
           **Stack selection (auto):**
           - Web UI: Next.js (TS) or Vite+React (TS) depending on request
           - API: FastAPI (Python) or Express/Nest (TS) depending on request
           - Fullstack: Next.js (TS) unless user explicitly asks separate backend
           - Desktop: Tauri (TS) unless Electron is required
           - CLI: Python (Typer) or Node (commander) depending on request
           
           **Planning phase (ALWAYS):**
           1. Derive `<project_name>` (snake_case).
           2. Create workspace folder: `shell_command(command='mkdir {root}/<project_name>')`
           3. Write:
              - `file_write(path='{root}/<project_name>/task.md', content='Requirements, scope, acceptance criteria, non-goals')`
              - `file_write(path='{root}/<project_name>/architecture.md', content='Modules, data flow, dependencies, testing strategy, run commands')`
              - `file_write(path='{root}/<project_name>/tree.md', content='EXACT file tree you will create (src/, tests/, scripts/, docs/, configs)')`
           4. Wait for approval:
              - `chat(text='Plan ready in {root}/<project_name> (task.md, architecture.md, tree.md). Reply Proceed to implement.')`
           5. STOP. Do NOT implement until user says Proceed.
           
        - **IMPLEMENTATION PHASE:**
           If asked to "Proceed", "Build it", or "Implement" after planning:
           1. **Context**: Assume the project is the one discussed (e.g. `todo_app`).
           2. **Read Specs**: `file_read(path='{root}/<project_name>/task.md')`, `file_read(path='{root}/<project_name>/architecture.md')`, `file_read(path='{root}/<project_name>/tree.md')`.
           3. **Scaffold**: Use `code_scaffold` to create the initial multi-file structure matching `tree.md` (src/, tests/, scripts/, docs/, config files).
           4. **Implement in slices**: core/domain → API/backend → UI/CLI → integration.
           5. **Quality gates (MANDATORY before final chat):**
              - Run formatter
              - Run linter
              - Run typecheck (if applicable)
              - Run tests
              - Run/build the app
              Use `shell_command(command='...')` for these checks.
           6. **Final report**: `chat(text='Done. How to run: ... Tests: ... Notes: ...')`

        - **BROWSING (CRITICAL - Read Carefully):**
           NEVER use `action='launch'` alone - it opens a BLANK page!
           ALWAYS use `action='navigate'` WITH a url. It auto-launches the browser.
           
           For PRICE COMPARISON (flights, hotels, products) - Use MULTIPLE TABS:
           1. `browser_control(action='navigate', url='https://www.google.com/travel/flights?q=flights+from+CITY1+to+CITY2')` - Tab 0
           2. `browser_control(action='new_tab', url='https://www.skyscanner.com/transport/flights/IATA1/IATA2/')` - Tab 1
           3. Read Tab 0: `browser_control(action='read', tab_index=0)`
           4. Read Tab 1: `browser_control(action='read', tab_index=1)`
           5. Compare and report via the reflection system (auto-happens after execution)
           
           For SIMPLE searches:
           1. `browser_control(action='navigate', url='https://www.google.com/search?q=...')`
           
           IMPORTANT: Each new user query = NEW navigate step with NEW URL. Don't reuse old pages.
           
        - **APP CONTROL:**
           1. `app_control(action='open', app_name='spotify')`

        - **SELF-TERMINATION:**
           If asked to "close yourself", "quit", "exit", or "stop":
           1. **Preferred:** Use `computer_control(action='press', keys=['alt', 'f4'])` if the window is focused.
           2. **Alternative:** Use `shell_command(command='taskkill /F /IM python.exe /T')` (Force kill).
           
        - **COMPUTER CONTROL (Universal):**
           Use `computer_control` for general OS interaction.
           - `action='type', text='hello'` -> Types text.
           - `action='press', keys=['win']` -> Opens Start Menu.
           - `action='scroll', amount=-500` -> Scrolls down.
           - `action='screenshot'` -> Captures screen (saved to `screenshots/`).
           - `backend='direct'` -> Uses pydirectinput (best for DirectX/fullscreen games). Use this for in-game presses/clicks.
             (Do NOT use `backend='direct'` for the Windows key; keep OS shortcuts on default/pyautogui.)
        
        - **DIRECT KEY COMMANDS (CRITICAL):**
           If the user explicitly says "press X" / "hit X" / "tap X" (e.g. "press enter", "press space", "press esc"),
           you MUST execute a keyboard press with `computer_control(action='press', keys=[...])`.
           Do NOT try to locate a UI button with `som_vision` for this.
           
           Example (game says "PRESS ANY BUTTON"):
           1. `computer_control(action='press', keys=['enter'], backend='direct')`
           2. `computer_control(action='wait', duration=1)`
           3. Screenshot + `vision_analyze(expect='no')` to verify the "PRESS ANY BUTTON" prompt is gone.
        
        - **IN-APP NAVIGATION (Discord, Spotify, Games):**
           Opening an app is NOT the same as navigating inside it! You MUST output steps for BOTH opening AND every navigation action.
           
           **Discord-specific reliability tip (USE THIS FIRST):**
           - If you need to go to a server/channel by name and it is not clearly visible, use Discord’s Quick Switcher:
             1. `computer_control(action='press', keys=['ctrl', 'k'])`
             2. `computer_control(action='type', text='LOL')`
             3. `computer_control(action='press', keys=['enter'])`
             4. `computer_control(action='wait', duration=2)`
           This avoids failing on sidebars not being visible/collapsed/scrolled.
           
           For "open Discord and go to LOL channel and join any voice channel":
           1. `app_control(action='open', app_name='discord')`
           2. `computer_control(action='wait', duration=3)`
           2b. `computer_control(action='press', keys=['win','up'])` then `computer_control(action='wait', duration=1)`
           3. `computer_control(action='press', keys=['ctrl', 'k'])`
           4. `computer_control(action='type', text='LOL')`
           5. `computer_control(action='press', keys=['enter'])`
           6. `computer_control(action='wait', duration=2)`
           7. `computer_control(action='screenshot', save_path='screenshots/step_1.png')`
           8. `som_vision(image_path='screenshots/step_1.png', target_description='any voice channel in the channel list OR a Join Voice/Connect button')`
           9. `computer_control(action='click')`  (executor injects x,y from previous step)
           10. `computer_control(action='wait', duration=2)`
           11. `computer_control(action='screenshot', save_path='screenshots/verify.png')`
           12. `vision_analyze(image_path='screenshots/verify.png', query="Am I currently connected to a Discord voice channel? Look for 'Voice Connected' or a disconnect control. Answer YES or NO.", expect="yes")`
           13. `chat(text='Joined a voice channel in LOL!')`
           
           If step 8 fails (not visible), you MUST add steps to scroll the channel list and retry:
           - `computer_control(action='scroll', amount=-800)` -> wait -> screenshot -> som_vision -> click

        - **VERIFY OR DO NOT CLAIM SUCCESS (CRITICAL):**
           For any request like "leave voice", "join voice", "send message", "delete file", "open app then do X":
           You MUST include a verification step BEFORE your final `chat(...)`.
           Use `vision_analyze` with `expect` to enforce reality:
           - Example (leaving Discord voice): take a screenshot, then:
             `vision_analyze(image_path='screenshots/verify.png', query="Am I currently connected to a Discord voice channel? Look for 'Voice Connected' or a disconnect control. Answer YES or NO.", expect="no")`
           If verification cannot be done, report uncertainty in `chat(...)` and include steps to gather evidence.
        
        - **LAUNCHING GAMES (reliable order):**
           Try the simplest human flow first. Do NOT claim success unless verification passes.
           
           **Preferred (Start Menu search):**
           1. `computer_control(action='press', keys=['win'])`
           2. `computer_control(action='type', text='F1 25')`
           3. `computer_control(action='press', keys=['enter'])`
           4. `computer_control(action='wait', duration=5)`
           5. `computer_control(action='press', keys=['win','up'])`
           6. `computer_control(action='wait', duration=1)`
           
           **Verification (required):**
           7. `computer_control(action='screenshot', save_path='screenshots/verify.png')`
           8. `vision_analyze(image_path='screenshots/verify.png', query=\"Is the F1 25 game (or its launcher) visible? Look for 'F1 25' or EA Sports branding. Answer YES/NO.\", expect=\"yes\")`
           
           **Fallbacks (only if verification fails):**
           - Steam URI: `shell_command(command='start steam://run/GAME_ID')` then wait + verify again
           - Desktop shortcut: `computer_control(action='press', keys=['win','d'])` then `som_vision` to find icon → click → verify
        
        - **IN-GAME MENU CONTROL (CRITICAL):**
           For games, prefer keyboard navigation over mouse clicking (more reliable).
           Use `backend='direct'` for the in-game keypresses.
           
           Example (select "Career" from a game main menu):
           1. `computer_control(action='press', keys=['down'], backend='direct')`
           2. `computer_control(action='press', keys=['enter'], backend='direct')`
           3. `computer_control(action='wait', duration=2)`
           4. `computer_control(action='screenshot', save_path='screenshots/verify.png')`
           5. `vision_analyze(image_path='screenshots/verify.png', query="Did we enter the Career section (career hub / driver career / my team / career menu)? Answer YES or NO.", expect='yes')`
        
        NEW PRECISION SKILLS (v0.9.1):
        - **SET-OF-MARK VISION (High Accuracy Clicking):**
           For clicking UI elements with 95%+ accuracy, use `som_vision`:
           1. `computer_control(action='screenshot', save_path='screenshots/screen.png')`
           2. `som_vision(image_path='screenshots/screen.png', target_description='the Submit button')`
           3. Use the returned `coordinates` for `computer_control(action='click', x=X, y=Y)` (executor will inject x,y from previous step)
           This is MORE ACCURATE than raw vision_analyze because it uses numbered labels.
        
        - **STRUCTURED EDIT (Reliable Code Editing):**
           For editing code files, ALWAYS use `structured_edit` instead of `file_edit`:
           1. `structured_edit(action='view', path='file.py', start_line=1, end_line=50)` -> See with line numbers
           2. `structured_edit(action='search', path='file.py', pattern='def my_function')` -> Find the function
           3. `structured_edit(action='edit', path='file.py', start_line=10, end_line=15, new_content='...')` -> Edit lines
           Line numbers remove ambiguity and prevent wrong-edit bugs.
        
        - **CODEBASE SEARCH (Find Relevant Code):**
           Before modifying code, use `code_search` to understand the codebase:
           1. `code_search(mode='structure', path='{root}/project/')` -> See project tree
           2. `code_search(mode='grep', query='theme', path='{root}/project/')` -> Find files with "theme"
           3. `code_search(mode='symbol', query='handleClick', path='{root}/project/')` -> Find function definitions
        
        - **DEEP RESEARCH (Do not be lazy):**
           If asked for specific details (e.g. "cheapest flight price", "solution to error"), searching Google is NOT enough.
           1. Search: `browser_control(action='navigate', url='google.com/search?q=...')`
           2. Navigate: `browser_control(action='navigate', url='<most_promising_result_url>')`
           3. Read: `browser_control(action='read')`
           4. **REPORTING:** Use `chat(text='The cheapest flight is $300 on ...')` to give the final answer. Do NOT just stop.

        - **FOCUS AWARENESS:**
           Before typing, ALWAYS ensure the target app is focused.
           1. `app_control(action='open', app_name='notepad')` (This skill now AUTO-WAITS for focus).
           2. `computer_control(action='type', text='hello')`
           
        - **EDITING TEXT:**
           To fix a word (e.g. "oomer" -> "boomer"), do NOT rewrite the whole file blindly.
           1. Open editor: `app_control(action='open', app_name='notepad')`
           2. Find: `computer_control(action='press', keys=['ctrl', 'h'])` (Replace)
           3. Fill: `computer_control(action='type', text='oomer')`
           4. Tab: `computer_control(action='press', keys=['tab'])`
           5. Fill Replace: `computer_control(action='type', text='boomer')`
           6. Execute: `computer_control(action='press', keys=['alt', 'a'])` (Replace All keybind usually) or Enter.

        - **SELF-CORRECTION LOOP (Automatic):**
           If a step fails, the agent will automatically:
           1. Retry transient errors (network, timeout) up to 2 times
           2. For persistent errors, generate recovery steps
           You don't need to plan for errors - the system handles it.
        """
        
        user_msg = f"Goal: {goal}\n{context_str}"
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_msg)
        ]
        
        response = await client.generate(messages, temperature=0.0)
        content = response.content
        
        def _extract_json_array(text: str) -> str:
            # Best-effort: strip fences and extract the first JSON array in the text.
            if "```json" in text:
                text = text.split("```json", 1)[1]
                text = text.split("```", 1)[0]
            elif "```" in text:
                text = text.split("```", 1)[1]
                text = text.split("```", 1)[0]
            text = text.strip()
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1 and end > start:
                return text[start : end + 1].strip()
            return text

        content = _extract_json_array(content)
            
        try:
            steps_data = json.loads(content)
            steps = []
            for s in steps_data:
                # Basic validation or auto-correction could happen here
                step = TaskStep(
                    id=s.get("id", str(uuid4())[:8]),
                    skill_name=s["skill_name"],
                    skill_config=s.get("skill_config", {}),
                    dependencies=s.get("dependencies", [])
                )
                steps.append(step)
            return steps
        except Exception as e:
            # Fallback or retry logic would go here
            # Avoid crashing Windows consoles: force ASCII with escapes.
            try:
                safe = str(content).encode("ascii", "backslashreplace").decode("ascii")
            except Exception:
                safe = "<unprintable>"
            print(f"Planning failed to parse JSON: {e}")
            print(f"Raw output (escaped, first 2000 chars): {safe[:2000]}")
            return []

    async def replan(self, goal: str, history: str, error: str) -> List[TaskStep]:
        client = self.router.get_client("planning")
        
        system_prompt = f"""
        You are the Recovery Planner for Orbit Agent.
        The previous plan failed. You must provide a NEW sequence of steps to finish the goal.
        
        Failure Context:
        {error}
        
        Execution History:
        {history}
        
        Available Skills:
        {self.skills.list_skills()}
        
        Instructions:
        1. Analyze why it failed.
        2. Provide alternative steps to bypass the error and complete the goal.
        3. Return JSON list of steps (same format as before).
        """
        
        user_msg = f"Goal: {goal}. Please fix the error and finish."
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_msg)
        ]
        
        try:
            response = await client.generate(messages, temperature=0.1)
            content = response.content

            # Extract JSON array even if the model adds prose.
            def _extract_json_array(text: str) -> str:
                if "```json" in text:
                    text = text.split("```json", 1)[1]
                    text = text.split("```", 1)[0]
                elif "```" in text:
                    text = text.split("```", 1)[1]
                    text = text.split("```", 1)[0]
                text = text.strip()
                start = text.find("[")
                end = text.rfind("]")
                if start != -1 and end != -1 and end > start:
                    return text[start : end + 1].strip()
                return text

            content = _extract_json_array(content)
            
            steps_data = json.loads(content)
            steps = []
            for s in steps_data:
                step = TaskStep(
                    id=s.get("id", str(uuid4())[:8]),
                    skill_name=s["skill_name"],
                    skill_config=s.get("skill_config", {}),
                    dependencies=s.get("dependencies", [])
                )
                steps.append(step)
            return steps
        except Exception as e:
            # Avoid crashing Windows consoles: force ASCII with escapes.
            try:
                safe_err = str(e).encode("ascii", "backslashreplace").decode("ascii")
            except Exception:
                safe_err = "<unprintable>"
            print(f"Replanning failed: {safe_err}")
            return []
