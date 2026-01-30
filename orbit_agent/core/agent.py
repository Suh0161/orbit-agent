import asyncio
from pathlib import Path
from typing import Optional, Tuple
from uuid import UUID

from orbit_agent.config.config import OrbitConfig
from orbit_agent.tasks.engine import TaskEngine
from orbit_agent.tasks.models import Task, TaskState, StepState, TaskStep
from orbit_agent.skills.registry import SkillRegistry
from orbit_agent.permissions.manager import PermissionManager
from orbit_agent.models.router import ModelRouter
from orbit_agent.core.planner import Planner
from orbit_agent.memory.long_term import LongTermMemory
from orbit_agent.memory.decision import DecisionLog
from orbit_agent.memory.workspace_context import WorkspaceContext
from orbit_agent.core.guardrail import GuardrailAgent

class Agent:
    def __init__(self, config: OrbitConfig, interactive: bool = True):
        self.config = config
        self.interactive = interactive
        self.engine = TaskEngine(config)
        self.skills = SkillRegistry(config)
        self.permissions = PermissionManager({"shell_exec": "ask", "file_write": "ask"}) 
        self.router = ModelRouter(config)
        self.guardrail = GuardrailAgent(self.router)
        
        self.long_term_memory = LongTermMemory(config.memory.path, config.memory.collection_name)
        self.workspace_context = WorkspaceContext(config.memory.path / "workspace_context.json")
        self.planner = Planner(self.router, self.skills, self.long_term_memory, config.workspace_root)
        self.decision_log = DecisionLog(config.memory.path / "decisions.jsonl")

    async def create_task(self, goal: str) -> Task:
        # Include workspace context in planning
        context_summary = self.workspace_context.get_context_summary()
        enriched_goal = f"{goal}\n\n[Workspace Context]\n{context_summary}"
        
        steps = await self.planner.plan(enriched_goal)
        task = self.engine.create_task(goal, steps)
        await self.decision_log.add(f"Created task {task.id} for goal: {goal}")
        return task
    async def chat(self, user_message: str, image_path: Optional[str] = None) -> str:
        """
        Direct chat with Orbit. Supports Multimodal (Vision).
        """
        from orbit_agent.models.base import Message
        import base64
        
        system_prompt = """You are Orbit, an autonomous AI Agent with FULL CONTROL of this Windows PC.
You can:
1. Open any app (Roblox, Spotify, VS Code).
2. Browse the web (using a Phantom Browser).
3. Control the Mouse & Keyboard.
4. Read the screen (Vision).

CRITICAL INSTRUCTION:
- If the user provides an IMAGE/SCREENSHOT, you CAN SEE IT right now. Do NOT say "I will trigger a task to look". Just ANSWER the question about the image immediately.
- Only trigger a 'Task' if the user asks you to DO something (click, type, browse, search).
- For questions like "What is this?", just answer.
- Be concise, witty, and confident."""

        msgs = [Message(role="system", content=system_prompt)]
        
        if image_path:
            try:
                print(f"[Agent] Reading Image: {image_path}")
                with open(image_path, "rb") as img_file:
                    b64_image = base64.b64encode(img_file.read()).decode('utf-8')
                
                print(f"[Agent] Base64 Size: {len(b64_image)} bytes")
                
                # Multimodal Message
                user_content = [
                    {"type": "text", "text": user_message},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64_image}"
                        }
                    }
                ]
                msgs.append(Message(role="user", content=user_content))
                print("[Agent] Payload constructed.")
                
            except Exception as e:
                # Fallback if image read fails
                print(f"[Agent] Failed to process image: {e}")
                msgs.append(Message(role="system", content=f"CONTEXT: User tried to attach image at {image_path} but it failed to load."))
                msgs.append(Message(role="user", content=user_message))
        else:
            # Text Only
            msgs.append(Message(role="user", content=user_message))
        
        # Use the router to get a client
        client = self.router.get_client("planning")
        try:
            response = await client.generate(msgs)
            
            # Record interaction for multi-awareness
            self.workspace_context.record_interaction(
                user_input=user_message,
                agent_response=response.content[:500],
                task_summary=user_message[:50]
            )
            
            return response.content
        except Exception as e:
            return f"Error interacting with AI: {e}"

    async def run_loop(self, task_id: Optional[UUID] = None):
        """
        Main execution loop.
        If task_id is provided, resumes it.
        Otherwise resumes current loaded task or does nothing.
        """
        if task_id:
            task = self.engine.load_task(task_id)
            if not task:
                print(f"Task {task_id} not found.")
                return
        else:
            task = self.engine._current_task # Or load latest?
            
        if not task:
            print("No active task.")
            return

        print(f"Resuming Task {task.id}: {task.goal}")
        
        while task.state not in [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED]:
            runnable_steps = self.engine.get_runnable_steps(task)
            
            if not runnable_steps:
                # Check completion
                if self.engine.check_task_completion(task):
                    print(f"Task {task.id} finished with state: {task.state}")
                    # Save to Muscle Memory if successful
                    if task.state == TaskState.COMPLETED:
                        self.planner.routines.save_routine(task.goal, task.steps)
                    break
                else:
                    # Waiting for something? Or deadlock?
                    # Could be BLOCKED steps.
                    blocked = [s for s in task.steps if s.state == StepState.BLOCKED] # We don't have BLOCKED in StepState yet, used PENDING
                    # Wait... StepState enum: PENDING, RUNNING, COMPLETED, FAILED, SKIPPED
                    # I should explicitly handle permission blocking.
                    # For now, if no runnable steps and not complete, maybe wait or break.
                    print("No runnable steps. Task might be stuck or waiting.")
                    await asyncio.sleep(2)
                    continue

            for step in runnable_steps:
                # 1. Skill Validation
                try:
                    skill = self.skills.get_skill(step.skill_name)
                except ValueError:
                    self.engine.update_step_state(task, step.id, StepState.FAILED, error="Skill not found")
                    continue

                # 2. Permission Check
                allowed = True
                
                # Check for explicit pre-approval in step metadata
                if step.skill_config.get("approved") is True:
                     # Already approved
                     pass
                else:
                    for perm in skill.config.permissions_required:
                        if self.permissions.requires_approval(perm):
                            # Check if explicitly approved in task/step metadata (e.g. from 'agent approve')
                            # We can check step.skill_config.get("approvals", []) or similar.
                            # For now simplicity: check 'approved' flag again.
                            
                            if self.config.safe_mode: # If safe mode, asking is mandatory unless approved
                                if self.interactive:
                                    print(f"Step '{step.id}' wants to {perm}. [y/N]")
                                    try:
                                        ans = input("> ")
                                        if ans.lower() != 'y':
                                            allowed = False
                                            break
                                    except EOFError:
                                        allowed = False
                                        break
                                else:
                                    # Daemon mode: Wait for external approval
                                    # check if approved via CLI command (which would update the persistence)
                                    # Since we loaded the task from disk at start of loop, we rely on 'step.skill_config'
                                    # But we must RELOAD the task to see changes from 'agent approve' command?
                                    # Yes, concurrent modification.
                                    # For V0.1: simple polling/reload.
                                    latest_task = self.engine.load_task(task.id)
                                    current_step_latest = latest_task.get_step(step.id)
                                    if current_step_latest and current_step_latest.skill_config.get("approved"):
                                        pass # Approved externally
                                        # Update our local object
                                        step.skill_config["approved"] = True
                                    else:
                                        print(f"Step {step.id} waiting for approval via CLI (permission: {perm})...")
                                        allowed = False
                                        break
                
                if not allowed:
                    # In interactive, we already broke and set allowed=False
                    # In daemon, we logged waiting.
                    # We continue the outer loop to retry later?
                    # If we just 'continue', we'll loop rapidly.
                    await asyncio.sleep(2)
                    continue 
                
                # 3. Execution
                print(f"Running step {step.id} ({step.skill_name})...")
                self.engine.update_step_state(task, step.id, StepState.RUNNING)
                
                try:
                    # Validate Inputs
                    input_model = skill.input_schema(**step.skill_config)
                    output = await skill.execute(input_model)
                    
                    # Handle Output
                    if output.model_dump().get("error"): # Helper check
                        # Some skills return error in fields
                        if getattr(output, "error", None):
                             print(f"Step failed: {output.error}")
                             self.engine.update_step_state(task, step.id, StepState.FAILED, error=output.error)
                        else:
                             print(f"Step completed: {output}")
                             self.engine.update_step_state(task, step.id, StepState.COMPLETED, output=str(output))
                    else:
                        print(f"Step completed: {str(output)}")
                        self.engine.update_step_state(task, step.id, StepState.COMPLETED, output=str(output))
                        
                except Exception as e:
                    print(f"Step execution exception: {e}")
                    # self.engine.update_step_state(task, step.id, StepState.FAILED, error=str(e))
                    await self._handle_step_failure(task, step, str(e))
                
                await self.decision_log.add(f"Executed step {step.id}", metadata={"output": str(step.output)})
            
            await asyncio.sleep(0.5)

    async def _handle_step_failure(self, task: Task, step: TaskStep, error: str, retry_count: int = 0):
        """
        Enhanced self-correction loop.
        
        Strategy:
        1. For transient errors: retry up to 2 times
        2. For code edits: run verification command
        3. For persistent errors: replan with detailed context
        """
        MAX_RETRIES = 2
        
        print(f"Step {step.id} failed with: {error}")
        
        # Check if this is a transient/retryable error
        transient_patterns = [
            "timeout", "connection", "network", "rate limit", 
            "temporarily unavailable", "retry", "ECONNRESET"
        ]
        is_transient = any(pattern.lower() in error.lower() for pattern in transient_patterns)
        
        if is_transient and retry_count < MAX_RETRIES:
            print(f"Transient error detected. Retrying ({retry_count + 1}/{MAX_RETRIES})...")
            await asyncio.sleep(2 ** retry_count)  # Exponential backoff
            
            try:
                skill = self.skills.get_skill(step.skill_name)
                input_model = skill.input_schema(**step.skill_config)
                output = await skill.execute(input_model)
                
                if getattr(output, "error", None):
                    # Still failing, try again or escalate
                    await self._handle_step_failure(task, step, output.error, retry_count + 1)
                else:
                    print(f"Retry succeeded: {output}")
                    self.engine.update_step_state(task, step.id, StepState.COMPLETED, output=str(output))
                return
            except Exception as e:
                await self._handle_step_failure(task, step, str(e), retry_count + 1)
                return
        
        print("Initiating SELF-CORRECTION protocol...")
        
        # 1. Gather detailed history
        history = []
        for s in task.steps:
            status = s.state.value
            res = s.output if s.state == StepState.COMPLETED else (s.error or "")
            # Include skill config for context
            config_summary = str(s.skill_config)[:200] if s.skill_config else ""
            history.append(f"Step {s.id} ({s.skill_name}): {status}\n  Config: {config_summary}\n  Result: {res}")
        history_str = "\n".join(history)
        
        # 2. Enhanced error context
        error_context = f"""
FAILED STEP: {step.id} ({step.skill_name})
CONFIG: {step.skill_config}
ERROR: {error}

EXECUTION HISTORY:
{history_str}
"""
        
        # 3. Ask Planner for Recovery
        try:
            recovery_steps = await self.planner.replan(task.goal, history_str, error_context)
            
            if not recovery_steps:
                print("Planner could not find a fix. Task failed.")
                self.engine.update_step_state(task, step.id, StepState.FAILED, error=error)
                return

            print(f"Planner suggests {len(recovery_steps)} recovery steps.")
            
            # Mark current step as replaced
            self.engine.update_step_state(task, step.id, StepState.SKIPPED, error=f"Replaced by recovery: {error}")
            
            # Cancel remaining PENDING steps
            for s in task.steps:
                if s.state == StepState.PENDING:
                    self.engine.update_step_state(task, s.id, StepState.CANCELLED, error="Plan changed")

            # Add recovery steps
            for new_step in recovery_steps:
                self.engine.add_step(task, new_step)
                
            print("Plan updated. Resuming execution.")

        except Exception as replan_err:
            print(f"Self-correction failed: {replan_err}")
            self.engine.update_step_state(task, step.id, StepState.FAILED, error=error)
    
    async def verify_code_change(self, project_path: str, command: str = None) -> Tuple[bool, str]:
        """
        Verify a code change by running a build/test command.
        
        Returns:
            (success: bool, output: str)
        """
        if command is None:
            # Auto-detect verification command
            project = Path(project_path)
            
            if (project / "package.json").exists():
                command = "npm run build"
            elif (project / "pyproject.toml").exists():
                command = "python -m py_compile"  # Basic syntax check
            elif (project / "requirements.txt").exists():
                command = "python -c 'import ast; [ast.parse(open(f).read()) for f in __import__(\"glob\").glob(\"**/*.py\", recursive=True)]'"
            else:
                return True, "No verification command available"
        
        try:
            from orbit_agent.skills.shell import ShellCommandSkill, ShellInput
            shell = ShellCommandSkill()
            result = await shell.execute(ShellInput(cmd=command, cwd=project_path))
            
            if result.exit_code == 0:
                return True, result.stdout
            else:
                return False, f"Exit code {result.exit_code}: {result.stderr}"
        except Exception as e:
            return False, str(e)

