import pytest
from unittest.mock import MagicMock, patch
from orbit_agent.skills.desktop import DesktopSkill, DesktopInput

@pytest.mark.asyncio
async def test_desktop_screenshot_mocked(tmp_path):
    skill = DesktopSkill()
    
    # We must patch pyautogui to avoid actual screen capture in CI/Test env
    with patch("orbit_agent.skills.desktop.pyautogui") as mock_gui:
        target = tmp_path / "screen.png"
        inp = DesktopInput(action="screenshot", save_path=str(target))
        
        output = await skill.execute(inp)
        
        assert output.success is True
        mock_gui.screenshot.assert_called_once_with(str(target))

@pytest.mark.asyncio
async def test_desktop_type_mocked():
    skill = DesktopSkill()
    with patch("orbit_agent.skills.desktop.pyautogui") as mock_gui:
        inp = DesktopInput(action="type", text="Hello World")
        output = await skill.execute(inp)
        
        assert output.success is True
        mock_gui.write.assert_called_once_with("Hello World")
