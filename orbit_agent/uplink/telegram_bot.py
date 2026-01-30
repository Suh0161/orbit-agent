"""
Orbit Uplink - Telegram Bot Module

This is the "Clawd Killer" - mobile access to your desktop agent.
Unlike Clawd, Orbit can SEE your screen and CLICK buttons.

Architecture:
1. TelegramBot listens for messages from your phone
2. Forwards to OrbitAgent (the same brain as the desktop GUI)
3. Returns text response + optional screenshot
4. You have full desktop control from anywhere!

Usage:
    python -m orbit_agent.uplink.main
    
Then message your bot on Telegram!
"""

import asyncio
import base64
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Set
from dataclasses import dataclass

try:
    from telegram import Update, Bot
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        ContextTypes,
        filters,
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    print("[Uplink] python-telegram-bot not installed. Run: pip install python-telegram-bot")

from orbit_agent.config.config import OrbitConfig, load_config
from orbit_agent.core.agent import Agent

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


@dataclass
class UplinkConfig:
    """Configuration for Orbit Uplink."""
    enabled: bool = True
    platform: str = "telegram"
    bot_token: Optional[str] = None
    allowed_users: Set[int] = None  # Telegram user IDs allowed to control
    require_auth: bool = True
    screenshot_on_task: bool = True  # Auto-attach screenshot for task completions


class OrbitTelegramBot:
    """
    Telegram interface for Orbit Agent.
    
    Features:
    - /start - Initialize and check connection
    - /status - Get current workspace status
    - /screenshot - Capture and send current screen
    - /stop - Stop any running task
    - Any text - Treated as a command/question for Orbit
    """
    
    def __init__(self, config: OrbitConfig, uplink_config: UplinkConfig):
        self.config = config
        self.uplink_config = uplink_config
        self.agent: Optional[Agent] = None
        self.app: Optional[Application] = None
        self.active_tasks: dict = {}  # user_id -> task
        
        # Security: Track authorized users
        self.authorized_users: Set[int] = uplink_config.allowed_users or set()
        self.pending_auth: Set[int] = set()
    
    async def initialize(self):
        """Initialize the Telegram bot and Orbit agent."""
        if not TELEGRAM_AVAILABLE:
            raise RuntimeError("python-telegram-bot is not installed")
        
        if not self.uplink_config.bot_token:
            raise ValueError("Telegram bot token not configured")
        
        # Initialize Orbit Agent
        self.agent = Agent(self.config, interactive=False)
        logger.info("[Uplink] Orbit Agent initialized")
        
        # Build Telegram Application
        self.app = Application.builder().token(self.uplink_config.bot_token).build()
        
        # Register handlers
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("screenshot", self.cmd_screenshot))
        self.app.add_handler(CommandHandler("stop", self.cmd_stop))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("auth", self.cmd_auth))
        
        # Message handler for general commands
        self.app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self.handle_message
        ))
        
        # Photo handler for vision analysis
        self.app.add_handler(MessageHandler(
            filters.PHOTO,
            self.handle_photo
        ))
        
        logger.info("[Uplink] Telegram handlers registered")
    
    def is_authorized(self, user_id: int) -> bool:
        """Check if user is authorized to use the bot."""
        if not self.uplink_config.require_auth:
            return True
        return user_id in self.authorized_users
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        user = update.effective_user
        user_id = user.id
        
        if self.is_authorized(user_id):
            await update.message.reply_text(
                f"🌐 **Orbit Uplink Active**\n\n"
                f"Welcome back, {user.first_name}!\n"
                f"Your desktop is now in your pocket.\n\n"
                f"**Commands:**\n"
                f"/status - Workspace status\n"
                f"/screenshot - Capture screen\n"
                f"/stop - Cancel running task\n"
                f"/help - All commands\n\n"
                f"Or just type what you want me to do!",
                parse_mode='Markdown'
            )
        else:
            # First-time user
            self.pending_auth.add(user_id)
            await update.message.reply_text(
                f"🔐 **Authorization Required**\n\n"
                f"Your Telegram ID: `{user_id}`\n\n"
                f"To authorize this device, add your ID to the config:\n"
                f"```yaml\n"
                f"uplink:\n"
                f"  allowed_users: [{user_id}]\n"
                f"```\n\n"
                f"Or use `/auth <password>` if configured.",
                parse_mode='Markdown'
            )
    
    async def cmd_auth(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /auth command for runtime authorization."""
        user_id = update.effective_user.id
        
        # Simple PIN-based auth (configurable)
        auth_pin = os.environ.get("ORBIT_UPLINK_PIN")
        
        if not auth_pin:
            await update.message.reply_text(
                "❌ Runtime auth not configured. Add your user ID to config."
            )
            return
        
        if context.args and context.args[0] == auth_pin:
            self.authorized_users.add(user_id)
            await update.message.reply_text(
                "✅ **Authorized!**\n\nYou now have full access to Orbit."
            )
        else:
            await update.message.reply_text("❌ Invalid PIN.")
    
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command - show workspace status."""
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized.")
            return
        
        try:
            # Get workspace context
            from orbit_agent.memory.workspace_context import WorkspaceContext
            workspace = WorkspaceContext()
            summary = workspace.get_context_summary()
            
            await update.message.reply_text(
                f"🖥️ **Workspace Status**\n\n{summary}",
                parse_mode='Markdown'
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
    
    async def cmd_screenshot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /screenshot command - capture and send screen."""
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized.")
            return
        
        await update.message.reply_text("📸 Capturing screen...")
        
        try:
            # Take screenshot using mss
            import mss
            from PIL import Image
            import io
            
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # Primary monitor
                screenshot = sct.grab(monitor)
                
                # Convert to PIL Image
                img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')
                
                # Resize for Telegram (max 1280px width)
                max_width = 1280
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_size = (max_width, int(img.height * ratio))
                    img = img.resize(new_size, Image.LANCZOS)
                
                # Save to bytes
                bio = io.BytesIO()
                img.save(bio, format='JPEG', quality=85)
                bio.seek(0)
                
                await update.message.reply_photo(
                    photo=bio,
                    caption=f"🖥️ Screenshot at {datetime.now().strftime('%H:%M:%S')}"
                )
                
        except Exception as e:
            await update.message.reply_text(f"❌ Screenshot failed: {e}")
    
    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stop command - cancel running task."""
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized.")
            return
        
        user_id = update.effective_user.id
        if user_id in self.active_tasks:
            # Cancel the task
            task = self.active_tasks.pop(user_id)
            await update.message.reply_text("⏹️ Task cancelled.")
        else:
            await update.message.reply_text("ℹ️ No active task to cancel.")
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        help_text = """
🛰️ **Orbit Uplink Commands**

**Control:**
• `/start` - Initialize connection
• `/status` - Workspace status
• `/screenshot` - Capture screen
• `/stop` - Cancel running task

**Usage:**
Just type what you want! Examples:
• "What's on my screen?"
• "Open VS Code"
• "Click the Submit button"
• "How's the render?"
• "Search for flights to Tokyo"

**Vision:**
Send a photo and I'll analyze it!

**Security:**
• `/auth <pin>` - Authorize device
        """
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular text messages - the main interface."""
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized. Use /start")
            return
        
        user_id = update.effective_user.id
        user_message = update.message.text
        
        # Show typing indicator
        await update.message.chat.send_action('typing')
        
        try:
            # Check if this needs vision (screen analysis)
            vision_keywords = ['screen', 'see', 'look', 'what is', "what's", 'show', 'display']
            needs_vision = any(kw in user_message.lower() for kw in vision_keywords)
            
            image_path = None
            if needs_vision:
                # Take a screenshot first
                import mss
                from PIL import Image
                
                screenshots_dir = Path("screenshots")
                screenshots_dir.mkdir(exist_ok=True)
                
                with mss.mss() as sct:
                    screenshot = sct.grab(sct.monitors[1])
                    img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')
                    image_path = screenshots_dir / f"uplink_{user_id}.png"
                    img.save(image_path)
            
            # Send to Orbit Agent
            response = await self.agent.chat(user_message, image_path=str(image_path) if image_path else None)
            
            # Check if response indicates a task was created
            if "task" in response.lower() or "step" in response.lower():
                self.active_tasks[user_id] = True
            
            # Send response (split if too long)
            if len(response) > 4000:
                # Split into chunks
                for i in range(0, len(response), 4000):
                    await update.message.reply_text(response[i:i+4000])
            else:
                await update.message.reply_text(response)
            
            # If vision was used, also send the screenshot
            if needs_vision and image_path and image_path.exists():
                with open(image_path, 'rb') as f:
                    await update.message.reply_photo(
                        photo=f,
                        caption="📸 Current screen"
                    )
                    
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)[:200]}")
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo messages - analyze with vision."""
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized.")
            return
        
        await update.message.chat.send_action('typing')
        
        try:
            # Get the largest photo version
            photo = update.message.photo[-1]
            file = await photo.get_file()
            
            # Download to temp file
            screenshots_dir = Path("screenshots")
            screenshots_dir.mkdir(exist_ok=True)
            image_path = screenshots_dir / f"uplink_photo_{update.effective_user.id}.jpg"
            
            await file.download_to_drive(str(image_path))
            
            # Get caption or default query
            query = update.message.caption or "What is in this image?"
            
            # Analyze with Orbit
            response = await self.agent.chat(query, image_path=str(image_path))
            
            await update.message.reply_text(response)
            
        except Exception as e:
            logger.error(f"Error handling photo: {e}")
            await update.message.reply_text(f"❌ Error: {e}")
    
    async def run(self):
        """Start the bot."""
        await self.initialize()
        
        logger.info("🛰️ Orbit Uplink starting...")
        logger.info("Press Ctrl+C to stop")
        
        # Start polling
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        
        # Keep running
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
    
    def run_blocking(self):
        """Run the bot in blocking mode."""
        asyncio.run(self.run())
