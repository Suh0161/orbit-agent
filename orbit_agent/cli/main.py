import asyncio
import typer
from typing import Optional
from pathlib import Path

from orbit_agent.config.config import OrbitConfig
from orbit_agent.core.agent import Agent
from orbit_agent.tasks.models import TaskState

app = typer.Typer()

@app.command()
def run(goal: str, interactive: bool = True):
    """
    Start a new task with the given goal.
    """
    config = OrbitConfig.load()
    agent = Agent(config, interactive=interactive)
    
    async def _run():
        print(f"Creating task for: {goal}")
        task = await agent.create_task(goal)
        print(f"Task ID: {task.id}")
        await agent.run_loop()

    asyncio.run(_run())

@app.command()
def chat():
    """
    Start an interactive chat session (Ghost Shell).
    The Agent stays alive, maintaining memory and context between commands.
    """
    config = OrbitConfig.load()
    agent = Agent(config, interactive=True)
    
    async def _chat_loop():
        print("Orbit Agent v0.4 (Ghost Shell)")
        print("Type a command to execute. Type 'exit' to quit.")
        print("-" * 40)
        
        while True:
            try:
                user_input = input("Orbit> ").strip()
                if not user_input:
                    continue
                
                if user_input.lower() in ["exit", "quit"]:
                    print("Shutting down Ghost Shell.")
                    break
                
                # Create and Run Task
                # Note: agent.create_task overwrites the specific "current task" focus,
                # but LongTermMemory and cached skills (like UICache) persist in the 'agent' instance.
                print(f"Executing: {user_input}")
                try:
                    task = await agent.create_task(user_input)
                    await agent.run_loop()
                    print(f"Task finished. Waiting for next command...")
                except Exception as task_error:
                    print(f"Task Error: {task_error}")
                    
            except KeyboardInterrupt:
                print("\nInterrupted.")
                break
            except EOFError:
                break

    asyncio.run(_chat_loop())

@app.command()
def onboard(install_daemon: bool = typer.Option(False, "--install-daemon", help="Install Uplink autostart (Windows Scheduled Task).")):
    """
    Interactive setup: provider/model + keys + (optional) Telegram.
    Writes/updates .env and orbit_config.yaml in the current directory.
    """
    from orbit_agent.cli.onboard import run_onboarding

    run_onboarding(install_daemon=install_daemon)

@app.command()
def uplink():
    """
    Start Orbit Uplink (Telegram bot).
    """
    from orbit_agent.uplink.main import main as uplink_main

    uplink_main()

@app.command()
def gateway():
    """
    Start Orbit Gateway (recommended always-on daemon).
    """
    from orbit_agent.gateway.main import main as gateway_main

    gateway_main()

@app.command()
def daemon():
    """
    Start the agent as a daemon processes (polling).
    """
    config = OrbitConfig.load()
    agent = Agent(config, interactive=False)
    
    async def _run():
        print("Starting Orbit Agent Daemon...")
        # In a real daemon, we'd loop forever and look for tasks.
        # For MVP, we presume the User uses 'resume' or we poll db for any PENDING tasks.
        # Let's just poll for a specific active task or list? 
        # Accessing Engine internals to find tasks:
        # We didn't impl 'list_tasks' in Engine.
        print("Daemon mode active. polling logic would go here.")
        # For demo, just wait.
        while True:
            await asyncio.sleep(5)
    
    asyncio.run(_run())

@app.command()
def approve(task_id: str, step_id: str):
    """
    Approve a blocked step.
    """
    config = OrbitConfig.load()
    from orbit_agent.tasks.engine import TaskEngine
    engine = TaskEngine(config)
    
    from uuid import UUID
    try:
        tid = UUID(task_id)
        task = engine.load_task(tid)
        if not task:
            print("Task not found.")
            return
        
        step = task.get_step(step_id)
        if not step:
            print("Step not found.")
            return
        
        step.skill_config["approved"] = True
        engine.save_task(task)
        print(f"Step {step_id} approved. Daemon should pick it up.")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    app()
