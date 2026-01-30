import pytest
import os
from pathlib import Path
from orbit_agent.skills.file import FileReadSkill, FileWriteSkill, FileWriteInput, FileReadInput
from orbit_agent.skills.shell import ShellCommandSkill, ShellInput

@pytest.mark.asyncio
async def test_file_write_and_read(tmp_path):
    # Setup
    write_skill = FileWriteSkill()
    read_skill = FileReadSkill()
    
    test_file = tmp_path / "test.txt"
    content = "Hello Orbit"
    
    # Test Write
    write_input = FileWriteInput(path=str(test_file), content=content)
    write_output = await write_skill.execute(write_input)
    
    assert write_output.success is True
    assert test_file.exists()
    
    # Test Read
    read_input = FileReadInput(path=str(test_file))
    read_output = await read_skill.execute(read_input)
    
    assert read_output.content == content

@pytest.mark.asyncio
async def test_shell_command():
    skill = ShellCommandSkill()
    # Echo command is safe and universal? Powershell: 'echo' is alias for Write-Output
    # But shell=True in windows might need 'cmd /c' or 'powershell -c'.
    # default shell on windows in asyncio is usually cmd or ps?
    # Let's try simple 'echo hello'
    
    inp = ShellInput(command="echo hello")
    output = await skill.execute(inp)
    
    assert output.exit_code == 0
    assert "hello" in output.stdout.lower()

@pytest.mark.asyncio
async def test_file_write_overwrite_check(tmp_path):
    skill = FileWriteSkill()
    p = tmp_path / "protected.txt"
    p.write_text("should remain")
    
    inp = FileWriteInput(path=str(p), content="new content", overwrite=False)
    output = await skill.execute(inp)
    
    assert output.success is False
    assert "exists" in output.error
    assert p.read_text() == "should remain"
