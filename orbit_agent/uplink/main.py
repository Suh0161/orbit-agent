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
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv

from orbit_agent.config.config import load_config
from orbit_agent.uplink.telegram_bot import OrbitTelegramBot, UplinkConfig, TELEGRAM_AVAILABLE


def main():
    """Main entry point for Orbit Uplink."""
    
    # Load environment variables
    load_dotenv()
    
    # Check dependencies
    if not TELEGRAM_AVAILABLE:
        print("❌ python-telegram-bot is not installed.")
        print("   Run: pip install python-telegram-bot")
        sys.exit(1)
    
    # Get bot token
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    
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
        config = load_config()
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
    uplink_config = UplinkConfig(
        enabled=True,
        platform="telegram",
        bot_token=bot_token,
        allowed_users=allowed_users if allowed_users else None,
        require_auth=bool(allowed_users),  # Require auth only if users specified
        screenshot_on_task=True
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
