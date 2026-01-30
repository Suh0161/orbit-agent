import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
from orbit_agent.core.agent import Agent
from orbit_agent.config.config import OrbitConfig
from orbit_agent.models.base import ModelResponse, Message
from orbit_agent.tasks.models import TaskState

@pytest.mark.asyncio
async def test_agent_flow_mocked(tmp_path):
    # Setup Config
    # Memory config expects a sub-model or dict compatible with it
    # Pydantic 2.x validation might require correct types.
    config = OrbitConfig(
        workspace_root=tmp_path / "workspace",
    )
    # Patch memory path directly
    config.memory.path = tmp_path / "memory"
    
    # Init Agent
    agent = Agent(config)
    
    # Mock Router & Client for PLANNER
    mock_client = AsyncMock()
    
    target_file = tmp_path / "test_output.txt"
    # Ensure strict path string
    target_path_str = str(target_file).replace("\\", "/") # normalize for json if needed
    
    # Mock Planner Response
    plan_json = f"""
    [
        {{
            "id": "step1",
            "skill_name": "file_write",
            "skill_config": {{
                "path": "{target_path_str}",
                "content": "Hello Orbit Flow",
                "overwrite": true
            }},
            "dependencies": []
        }}
    ]
    """
    mock_client.generate.return_value = ModelResponse(content=plan_json, usage={})
    
    # We need to make sure the router returns this mock client when 'planning' is requested
    # The agent.router.get_client takes arguments.
    agent.router.get_client = MagicMock(return_value=mock_client)
    
    # Modify permissions to allow file_write without interaction
    from orbit_agent.permissions.manager import PermissionLevel
    agent.permissions.policy["file_write"] = PermissionLevel.ALLOW
    
    # Create Task
    goal = "Write a hello file"
    task = await agent.create_task(goal)
    
    assert len(task.steps) == 1
    assert task.steps[0].skill_name == "file_write"
    
    # Run Loop
    await agent.run_loop(task.id)
    
    # Reload task to get latest state from persistence
    task = agent.engine.load_task(task.id)
    
    # Verify execution
    # Task should be COMPLETED
    assert task.state == TaskState.COMPLETED
    
    # Check File Artifact
    assert target_file.exists()
    assert target_file.read_text(encoding='utf-8') == "Hello Orbit Flow"
