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
                "skill_name": "shell_exec",
                "skill_config": {{ "cmd": "mkdir -p project" }},
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
        - Important: If a step produces a file (e.g. screenshot) and a later step needs it (e.g. vision), you MUST manually assign a specific path (e.g. '/workspace/temp_img.png') to 'save_path' in the first step and 'image_path' in the second. Do not rely on defaults or output variables.
        - **Critical:** Do NOT output the content of the app (e.g. a list of groceries). Output the STEPS to build it.
        
        Best Practices:
        - **RELIABILITY FIRST:** Your goal is 100% accuracy.
        - **Opening Apps:** Use action='press', key='win' -> Type Name -> Enter.
        - **Web Browsing:** Use `browser_control` skill. Prefer `duckduckgo.com` for searches to avoid CAPTCHAs. The browser window is separate from the user's mouse.
        - **Flight/Travel:** Do NOT just visit a homepage and stop. Construct a specific URL if possible. Example: `google.com/flights?q=flights+from+KUL+to+CGK`.
        - **Stocks/Finance:** Use direct URLs. `google.com/finance?q=AAPL` or `finance.yahoo.com/quote/TSLA`. Do not rely on generic search snippets for real-time prices.
        - **Multi-Tab Research:** To compare items (e.g. flight prices on different sites), use `action='new_tab'` for subsequent sites. Do not overwrite the current page if you need to compare. Use `tab_index=N` to switch.
        - **Navigating / Selecting:** To click a specific item (e.g. Server, Channel, Button), `visual_interact(action='hover_and_confirm', confirm_text='Unique Text')` is the gold standard for desktop apps.
        - **Visual Fallback:** Use `visual_interact` with 'double_click' for desktop icons.
        - **CRITICAL - WAIT States:** Whenever you perform an action that changes the view (e.g. Click Server, Open App, Open Game Page), you MUST insert a `desktop_control(action='wait', duration=5.0)` step immediately after. Vision is too fast; the UI needs time to render.

        Workflow Protocols (Human-Like Behavior):
        - **SOFTWARE ENGINEERING / CODING (PRIORITY):**
           If asked to "build an app", "write code", "create a website":
           1. **Derive Project Name**: e.g. `todo_app`.
           2. **Create Structure**: `shell_exec(cmd='mkdir -p {root}/<project_name>/orbit-imp')`.
           3. **Write Task Plan**: `file_write(path='{root}/<project_name>/orbit-imp/task.md', content='...')`.
           4. **Write Architecture**: `file_write(path='{root}/<project_name>/orbit-imp/architecture.md', content='...')`.
           5. **Wait for Approval**: `shell_exec(cmd='echo "Plan drafted in {root}/<project_name>/orbit-imp. Review and say Proceed."')`.
           6. **STOP**: Do NOT generate coding steps yet. Wait for user "Proceed".
           
        - **IMPLEMENTATION PHASE:**
           If asked to "Proceed", "Build it", or "Implement" after planning:
           1. **Context**: Assume the project is the one discussed (e.g. `todo_app`).
           2. **Read Specs**: `file_read(path='{root}/<project_name>/orbit-imp/task.md')`.
           3. **Generate Code**: Steps to creating all necessary files files (e.g. `main.py`, `gui.py`) inside `{root}/<project_name>/`.
           4. **Verify**: `shell_exec(cmd='python {root}/<project_name>/main.py')` (if applicable).

        - **BROWSING:** If asked to "book ticket" or "search web":
           1. `browser_control(action='launch')` (or 'navigate')
           2. `browser_control(action='read')` to digest content.
           
        - **APP CONTROL:**
           1. `app_control(action='open', app_name='spotify')`

        - **SELF-TERMINATION:**
           If asked to "close yourself", "quit", "exit", or "stop":
           1. **Preferred:** Use `computer_control(action='press', keys=['alt', 'f4'])` if the window is focused.
           2. **Alternative:** Use `shell_exec(cmd='taskkill /F /IM python.exe /T')` (Force kill).
           
        - **COMPUTER CONTROL (Universal):**
           Use `computer_control` for general OS interaction.
           - `action='type', text='hello'` -> Types text.
           - `action='press', keys=['win']` -> Opens Start Menu.
           - `action='scroll', amount=-500` -> Scrolls down.
           - `action='screenshot'` -> Captures screen (saved to `screenshots/`).
        
        NEW PRECISION SKILLS (v0.9.1):
        - **SET-OF-MARK VISION (High Accuracy Clicking):**
           For clicking UI elements with 95%+ accuracy, use `som_vision`:
           1. `desktop_control(action='screenshot', save_path='screenshots/screen.png')`
           2. `som_vision(image_path='screenshots/screen.png', target_description='the Submit button')`
           3. Use the returned `coordinates` for `desktop_control(action='click', x=X, y=Y)`
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
        
        # Clean markdown code blocks if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
            
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
            print(f"Planning failed to parse JSON: {e}")
            print(f"Raw output: {content}")
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
            # Cleanup markdown
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
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
            print(f"Replanning failed: {e}")
            return []
