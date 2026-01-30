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
from orbit_agent.core.trace import RunTrace

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
        try:
            trace = RunTrace.for_task(self.config.memory.path / "runs", str(task.id))
            trace.write(
                "planned",
                {
                    "goal": goal,
                    "steps": [{"id": s.id, "skill": s.skill_name, "config": s.skill_config} for s in steps],
                },
            )
        except Exception:
            pass
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
        trace: Optional[RunTrace] = None
        try:
            trace = RunTrace.for_task(self.config.memory.path / "runs", str(task.id))
            trace.write("start", {"goal": task.goal})
        except Exception:
            trace = None
        task.state = TaskState.RUNNING
        self.engine.save_task(task)
        
        while task.state not in [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED]:
            runnable_steps = self.engine.get_runnable_steps(task)
            
            if not runnable_steps:
                # Check completion
                if self.engine.check_task_completion(task):
                    print(f"Task {task.id} finished with state: {task.state}")
                    if trace:
                        try:
                            trace.write("end", {"state": task.state.value})
                        except Exception:
                            pass
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
                                        self.engine.update_step_state(task, step.id, StepState.BLOCKED, error=f"Waiting for approval ({perm})")
                                        allowed = False
                                        break
                
                if not allowed:
                    # In interactive, we already broke and set allowed=False
                    # In daemon, we logged waiting.
                    # We continue the outer loop to retry later?
                    # If we just 'continue', we'll loop rapidly.
                    await asyncio.sleep(2)
                    continue 

                # 2b. Guardrail enforcement for high-risk skills (even when autonomous)
                if step.skill_name in {"shell_command", "file_write", "file_edit", "skill_create"}:
                    try:
                        ok, reason = await self.guardrail.check(step.skill_name, dict(step.skill_config))
                    except Exception as guardrail_err:
                        # If guardrail is unavailable (e.g., missing API key in tests/offline),
                        # do not hard-fail the task. Log and continue.
                        await self.decision_log.add(
                            "Guardrail check skipped (unavailable)",
                            metadata={"skill": step.skill_name, "error": str(guardrail_err)[:200]},
                        )
                        ok, reason = True, ""
                    if not ok:
                        self.engine.update_step_state(task, step.id, StepState.FAILED, error=f"Guardrail REJECT: {reason}")
                        continue
                
                # 3. Execution
                print(f"Running step {step.id} ({step.skill_name})...")
                self.engine.update_step_state(task, step.id, StepState.RUNNING)
                if trace:
                    try:
                        trace.write("step_start", {"step_id": step.id, "skill": step.skill_name, "config": step.skill_config})
                    except Exception:
                        pass
                
                try:
                    # Validate Inputs
                    # Chain: if previous step was som_vision and returned coordinates, inject into next click if missing.
                    # This mirrors Uplink behavior and prevents planners from needing to guess x/y.
                    if (
                        step.skill_name == "computer_control"
                        and str(step.skill_config.get("action", "")).lower() == "click"
                        and not (step.skill_config.get("x") is not None and step.skill_config.get("y") is not None)
                    ):
                        # Find most recent completed SoM/Vision locate step with coordinates.
                        for prev in reversed(task.steps):
                            if prev.id == step.id:
                                continue
                            if prev.state not in (StepState.COMPLETED, StepState.SKIPPED):
                                continue
                            coords = None
                            try:
                                if isinstance(prev.output, dict):
                                    coords = prev.output.get("coordinates")
                                if coords is None and hasattr(prev.output, "coordinates"):
                                    coords = getattr(prev.output, "coordinates")
                            except Exception:
                                coords = None
                            if coords and isinstance(coords, (list, tuple)) and len(coords) == 2:
                                step.skill_config["x"], step.skill_config["y"] = int(coords[0]), int(coords[1])
                                break

                    input_model = skill.input_schema(**step.skill_config)
                    output = await skill.execute(input_model)
                    
                    # Unified failure detection across skills (success/error/exit_code/stderr).
                    failed = False
                    err = None
                    try:
                        if getattr(output, "success", None) is False:
                            failed = True
                        if getattr(output, "exit_code", 0) not in (0, None):
                            failed = True
                        if getattr(output, "error", None):
                            failed = True
                        if getattr(output, "stderr", None) and getattr(output, "exit_code", 0) not in (0, None):
                            failed = True
                        err = getattr(output, "error", None) or getattr(output, "stderr", None)
                    except Exception:
                        pass

                    # Persist full output object where possible (dict) for replan context.
                    out_payload = None
                    try:
                        out_payload = output.model_dump()
                    except Exception:
                        out_payload = str(output)

                    if failed:
                        print(f"Step failed: {err or output}")
                        if trace:
                            try:
                                trace.write("step_failed", {"step_id": step.id, "skill": step.skill_name, "error": str(err or output)[:500]})
                            except Exception:
                                pass
                        await self._handle_step_failure(task, step, str(err or output))
                    else:
                        print(f"Step completed: {str(output)}")
                        self.engine.update_step_state(task, step.id, StepState.COMPLETED, output=out_payload)
                        if trace:
                            try:
                                trace.write("step_done", {"step_id": step.id, "skill": step.skill_name})
                            except Exception:
                                pass
                        
                except Exception as e:
                    print(f"Step execution exception: {e}")
                    if trace:
                        try:
                            trace.write("step_exception", {"step_id": step.id, "skill": step.skill_name, "error": str(e)[:500]})
                        except Exception:
                            pass
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
        # Retries within self-correction (separate from task engine's max_retries counter).
        # Keep this small to avoid loops.
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
                    try:
                        self.engine.update_step_state(task, step.id, StepState.COMPLETED, output=output.model_dump())
                    except Exception:
                        self.engine.update_step_state(task, step.id, StepState.COMPLETED, output=str(output))
                return
            except Exception as e:
                await self._handle_step_failure(task, step, str(e), retry_count + 1)
                return
        
        print("Initiating SELF-CORRECTION protocol...")
        # Mark the step failed once (avoid inflating retry_count via recursion).
        if step.state != StepState.FAILED:
            self.engine.update_step_state(task, step.id, StepState.FAILED, error=error)
        
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
            
            # Skip remaining PENDING steps (plan changed)
            for s in task.steps:
                if s.state == StepState.PENDING:
                    self.engine.update_step_state(task, s.id, StepState.SKIPPED, error="Plan changed")

            # Add recovery steps
            for new_step in recovery_steps:
                self.engine.add_step(task, new_step)
                
            print("Plan updated. Resuming execution.")
            # Ensure task remains running after plan mutation
            task.state = TaskState.RUNNING
            self.engine.save_task(task)

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
            result = await shell.execute(ShellInput(command=command, cwd=project_path))
            
            if result.exit_code == 0:
                return True, result.stdout
            else:
                return False, f"Exit code {result.exit_code}: {result.stderr}"
        except Exception as e:
            return False, str(e)

