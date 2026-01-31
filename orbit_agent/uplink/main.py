"""
Orbit Uplink - Main Entry Point

Run this module to start the Telegram bot interface:

    python -m orbit_agent.uplink.main

Prerequisites:
1. pip install python-telegram-bot
2. Create a Telegram bot via @BotFather
3. Set TELEGRAM_BOT_TOKEN in your .env file
4. (Optional) Set ORBIT_UPLINK_PIN for runtime auth
"""

import asyncio
import os
import sys
import logging
import re
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Force UTF-8 for Windows Console as early as possible (before importing modules that may print).
if sys.platform == "win32":
    try:
        # Important: use reconfigure (do NOT wrap streams), otherwise we can lose sys.stderr
        # via double-wrapping/GC on Windows.
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
    except Exception:
        pass


def main():
    """Main entry point for Orbit Uplink."""

    from dotenv import load_dotenv

    from orbit_agent.config.config import OrbitConfig
    from orbit_agent.uplink.telegram_bot import OrbitTelegramBot, UplinkConfig, TELEGRAM_AVAILABLE
    
    # Load environment variables
    load_dotenv()

    # Force UTF-8 for Windows Console (again, after env + imports)
    if sys.platform == "win32":
        # Some Windows consoles still choke on uncommon unicode (e.g. arrows).
        # Use backslashreplace so logging/prints never crash the bot.
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
        except Exception:
            pass

        # Some libraries create StreamHandlers early and capture the old cp1252 stream.
        # Rebind any existing StreamHandlers to the now-sanitized stderr.
        try:
            root = logging.getLogger()
            for h in list(root.handlers):
                if isinstance(h, logging.StreamHandler):
                    h.stream = sys.stderr
        except Exception:
            pass
    
    # Check dependencies
    if not TELEGRAM_AVAILABLE:
        print("❌ python-telegram-bot is not installed.")
        print("   Run: pip install python-telegram-bot")
        sys.exit(1)
    
    # Get bot token
    def _sanitize_telegram_token(raw: str | None) -> str | None:
        if not raw:
            return None
        cleaned = "".join(ch for ch in str(raw).strip() if 32 <= ord(ch) < 127)
        m = re.search(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", cleaned)
        return m.group(0) if m else (cleaned or None)

    bot_token = _sanitize_telegram_token(os.environ.get("TELEGRAM_BOT_TOKEN"))
    
    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN not found in environment.")
        print("")
        print("To set up Orbit Uplink:")
        print("1. Open Telegram and message @BotFather")
        print("2. Send /newbot and follow instructions")
        print("3. Copy the token and add to your .env file:")
        print("")
        print('   TELEGRAM_BOT_TOKEN="your_token_here"')
        print("")
        sys.exit(1)
    
    # Load Orbit config
    try:
        config = OrbitConfig.load()
    except Exception as e:
        print(f"❌ Failed to load config: {e}")
        sys.exit(1)
    
    # Parse allowed users from environment
    allowed_users_str = os.environ.get("ORBIT_UPLINK_USERS", "")
    allowed_users = set()
    if allowed_users_str:
        try:
            allowed_users = {int(uid.strip()) for uid in allowed_users_str.split(",") if uid.strip()}
        except ValueError:
            print("⚠️ Invalid ORBIT_UPLINK_USERS format. Use comma-separated IDs.")
    
    # Create uplink config
    screenshots_env = os.environ.get("ORBIT_UPLINK_SCREENSHOTS", "")
    screenshot_on_task = str(screenshots_env).strip().lower() in {"1", "true", "yes", "on"}
    uplink_config = UplinkConfig(
        enabled=True,
        platform="telegram",
        bot_token=bot_token,
        allowed_users=allowed_users if allowed_users else None,
        require_auth=bool(allowed_users),  # Require auth only if users specified
        # Default OFF. Turn on via ORBIT_UPLINK_SCREENSHOTS=1 if you want proof screenshots.
        screenshot_on_task=screenshot_on_task
    )
    
    # Security warning if no users configured
    if not allowed_users:
        print("⚠️ WARNING: No allowed users configured!")
        print("   Anyone who finds your bot can control your PC.")
        print("   Set ORBIT_UPLINK_USERS in .env for security.")
        print("")
        print("   Example: ORBIT_UPLINK_USERS=123456789,987654321")
        print("")
        
        # Ask for confirmation
        try:
            response = input("Continue anyway? [y/N] ")
            if response.lower() != 'y':
                print("Aborted.")
                sys.exit(0)
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(0)
    
    # Create and run bot
    bot = OrbitTelegramBot(config, uplink_config)
    
    print("")
    print("🛰️ ================================")
    print("   ORBIT UPLINK")
    print("   Mobile access to your desktop")
    print("================================")
    print("")
    print(f"📱 Bot Token: {bot_token[:10]}...{bot_token[-5:]}")
    print(f"🔐 Auth Required: {uplink_config.require_auth}")
    if allowed_users:
        print(f"👥 Allowed Users: {allowed_users}")
    print("")
    print("Open Telegram and message your bot!")
    print("Press Ctrl+C to stop.")
    print("")
    
    try:
        bot.run_blocking()
    except KeyboardInterrupt:
        print("\n[Uplink] Shutting down...")


if __name__ == "__main__":
    main()
