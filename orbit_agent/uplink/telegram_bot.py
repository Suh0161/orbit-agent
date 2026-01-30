"""
Orbit Uplink - Telegram Bot Module

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
import re
import logging
from datetime import datetime, timedelta
from uuid import uuid4
from pathlib import Path
from typing import Optional, List, Set, Dict
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

from orbit_agent.config.config import OrbitConfig
from orbit_agent.core.agent import Agent
from orbit_agent.uplink.scheduler import JobStore, ScheduledJob, compute_next_run

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

        # Remember chat_id per user for proactive messages (reminders/heartbeats).
        self.user_chat_ids: Dict[int, int] = {}

        # Scheduler (JSON-backed)
        self.job_store = JobStore()
        self.jobs: Dict[str, ScheduledJob] = {}
        self._jobs_lock = asyncio.Lock()
        self._scheduler_task: Optional[asyncio.Task] = None
        
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
        self.app.add_handler(CommandHandler("remind", self.cmd_remind))
        self.app.add_handler(CommandHandler("daily", self.cmd_daily))
        self.app.add_handler(CommandHandler("jobs", self.cmd_jobs))
        self.app.add_handler(CommandHandler("cancel", self.cmd_cancel))
        self.app.add_handler(CommandHandler("heartbeat", self.cmd_heartbeat))
        
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

        # Load persisted jobs
        self.jobs = self.job_store.load()

    async def _verify_discord_voice_state(self, user_id: int, expect_connected: bool) -> Optional[bool]:
        """
        Lightweight post-check to reduce 'joined voice' / 'left voice' hallucinations.
        Returns True/False if verification ran, None if it couldn't run.
        """
        try:
            if not self.agent:
                return None
            # Need vision skill registered
            vision = self.agent.skills.get_skill("vision_analyze")

            from orbit_agent.skills.desktop import DesktopSkill, DesktopInput
            screenshots_dir = Path("screenshots")
            screenshots_dir.mkdir(exist_ok=True)
            image_path = screenshots_dir / f"verify_voice_{user_id}.png"

            desk = DesktopSkill()
            out = await desk.execute(DesktopInput(action="screenshot", save_path=str(image_path)))
            if not out.success:
                return None

            query = "Am I currently connected to a Discord voice channel? Look for 'Voice Connected' or a disconnect control. Answer YES or NO."
            expect = "yes" if expect_connected else "no"
            inp = vision.input_schema(image_path=str(image_path), query=query, expect=expect)
            res = await vision.execute(inp)
            return bool(getattr(res, "success", False))
        except Exception:
            return None

    async def _send_text(self, chat_id: int, text: str, parse_mode: Optional[str] = None):
        return await self.app.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)

    async def _edit_text(self, chat_id: int, message_id: int, text: str):
        return await self.app.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)

    async def _delete_message(self, chat_id: int, message_id: int):
        try:
            await self.app.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

    async def _scheduler_loop(self):
        # Runs forever; checks due jobs and triggers them.
        while True:
            try:
                now = datetime.now().timestamp()
                due: List[ScheduledJob] = []

                async with self._jobs_lock:
                    for job in self.jobs.values():
                        if job.enabled and job.next_run and job.next_run <= now:
                            due.append(job)

                for job in due:
                    # If user currently running something, delay a bit
                    if job.user_id in self.active_tasks:
                        async with self._jobs_lock:
                            job.next_run = datetime.now().timestamp() + 60
                            self.job_store.save(self.jobs)
                        continue

                    await self._run_scheduled_goal(job)

                    async with self._jobs_lock:
                        if job.kind in ("interval", "daily"):
                            job.next_run = compute_next_run(job)
                        else:
                            job.enabled = False
                        self.job_store.save(self.jobs)

            except Exception as e:
                logger.warning(f"[Scheduler] loop error: {e}")

            await asyncio.sleep(1.0)

    async def _run_scheduled_goal(self, job: ScheduledJob):
        # Minimal runner: executes a task and sends chat outputs. No auto screenshots.
        chat_id = job.chat_id
        user_id = job.user_id

        await self._send_text(chat_id, f"⏰ {job.goal}")

        try:
            task = await self.agent.create_task(job.goal)
            if not task.steps:
                resp = await self.agent.chat(job.goal)
                await self._send_text(chat_id, resp)
                return

            self.active_tasks[user_id] = str(task.id)
            status = await self._send_text(chat_id, "On it. ⚡")

            prev_output = None
            task_failed = False
            for step in task.steps:
                # guardrails/permissions (same as interactive path)
                skill = self.agent.skills.get_skill(step.skill_name)
                skill_config = dict(step.skill_config)

                if getattr(self.config, "safe_mode", True):
                    approved = bool(step.skill_config.get("approved"))
                    for perm in getattr(skill.config, "permissions_required", []):
                        if self.agent.permissions.requires_approval(perm) and not approved:
                            await self._edit_text(chat_id, status.message_id, f"🔒 Blocked '{step.skill_name}' (needs approval for '{perm}').")
                            raise RuntimeError("Blocked by permission policy")

                if step.skill_name in {"shell_command", "file_write", "file_edit", "skill_create"}:
                    ok, reason = await self.agent.guardrail.check(step.skill_name, skill_config)
                    if not ok:
                        await self._edit_text(chat_id, status.message_id, f"🔒 Guardrail REJECT for '{step.skill_name}': {reason}")
                        raise RuntimeError("Blocked by guardrail")

                if (step.skill_name == "computer_control" and skill_config.get("action") == "click"
                        and prev_output and getattr(prev_output, "success", True)
                        and getattr(prev_output, "coordinates", None)):
                    skill_config["x"], skill_config["y"] = prev_output.coordinates[0], prev_output.coordinates[1]

                input_model = skill.input_schema(**skill_config)
                output = await skill.execute(input_model)
                prev_output = output
                failed = False
                if getattr(output, "success", None) is False:
                    failed = True
                if getattr(output, "exit_code", 0) not in (0, None):
                    failed = True
                if getattr(output, "error", None):
                    failed = True
                if getattr(output, "stderr", None) and getattr(output, "exit_code", 0) not in (0, None):
                    failed = True

                if failed:
                    task_failed = True
                    error_msg = (
                        getattr(output, 'error', None)
                        or getattr(output, 'stderr', None)
                        or getattr(output, 'message', None)
                        or getattr(output, 'data', None)
                        or str(output)
                    )
                    await self._edit_text(chat_id, status.message_id, f"❌ Snag hit on '{step.skill_name}': {error_msg}")
                    return

                output_text = getattr(output, 'message', None) or getattr(output, 'data', None) or ""
                if step.skill_name == 'chat' or (isinstance(output_text, str) and output_text.startswith("[CHAT]")):
                    clean_msg = output_text.replace("[CHAT] ", "")
                    await self._send_text(chat_id, clean_msg)

            if not task_failed:
                await self._delete_message(chat_id, status.message_id)

        finally:
            if user_id in self.active_tasks:
                del self.active_tasks[user_id]
    
    def is_authorized(self, user_id: int) -> bool:
        """Check if user is authorized to use the bot."""
        if not self.uplink_config.require_auth:
            return True
        return user_id in self.authorized_users
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        user = update.effective_user
        user_id = user.id
        self.user_chat_ids[user_id] = update.effective_chat.id
        
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
            # Use the same screenshot path as desktop control (more reliable than mss on Windows)
            from orbit_agent.skills.desktop import DesktopSkill, DesktopInput
            from PIL import Image
            import io
            import os

            screenshots_dir = Path("screenshots")
            screenshots_dir.mkdir(exist_ok=True)
            save_path = str(screenshots_dir / f"uplink_screenshot_{update.effective_user.id}.png")

            desktop = DesktopSkill()
            out = await desktop.execute(DesktopInput(action="screenshot", save_path=save_path))
            if not out.success:
                raise RuntimeError(out.error or "Unknown screenshot failure")

            # Load and resize for Telegram
            img = Image.open(os.path.normpath(save_path)).convert("RGB")
            max_width = 1280
            if img.width > max_width:
                ratio = max_width / img.width
                new_size = (max_width, int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)

            bio = io.BytesIO()
            img.save(bio, format="JPEG", quality=85)
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

    async def cmd_remind(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Schedule a one-off reminder that runs a goal later. Usage: /remind <minutes> <goal...>"""
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized.")
            return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        self.user_chat_ids[user_id] = chat_id

        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Usage: /remind <minutes> <what to do>")
            return

        try:
            minutes = int(context.args[0])
        except Exception:
            await update.message.reply_text("First argument must be minutes (e.g. /remind 30 ...)")
            return

        goal = " ".join(context.args[1:]).strip()
        job_id = str(uuid4())[:8]
        job = ScheduledJob(
            id=job_id,
            user_id=user_id,
            chat_id=chat_id,
            kind="once",
            goal=goal,
            created_at=datetime.now().isoformat(),
            next_run=(datetime.now() + timedelta(minutes=max(1, minutes))).timestamp(),
        )

        async with self._jobs_lock:
            self.jobs[job_id] = job
            self.job_store.save(self.jobs)

        await update.message.reply_text(f"✅ Scheduled in {minutes} min. Job id: `{job_id}`", parse_mode="Markdown")

    async def cmd_daily(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Schedule a daily job. Usage: /daily HH:MM <goal...>"""
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized.")
            return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        self.user_chat_ids[user_id] = chat_id

        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Usage: /daily HH:MM <what to do>")
            return

        hhmm = context.args[0].strip()
        goal = " ".join(context.args[1:]).strip()
        job_id = str(uuid4())[:8]
        job = ScheduledJob(
            id=job_id,
            user_id=user_id,
            chat_id=chat_id,
            kind="daily",
            goal=goal,
            created_at=datetime.now().isoformat(),
            daily_time=hhmm,
        )
        job.next_run = compute_next_run(job)

        async with self._jobs_lock:
            self.jobs[job_id] = job
            self.job_store.save(self.jobs)

        await update.message.reply_text(f"✅ Scheduled daily at {hhmm}. Job id: `{job_id}`", parse_mode="Markdown")

    async def cmd_jobs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List scheduled jobs."""
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized.")
            return

        user_id = update.effective_user.id
        async with self._jobs_lock:
            mine = [j for j in self.jobs.values() if j.user_id == user_id and j.enabled]

        if not mine:
            await update.message.reply_text("No active jobs.")
            return

        lines = []
        for j in sorted(mine, key=lambda x: x.next_run or 0):
            when = datetime.fromtimestamp(j.next_run).strftime("%Y-%m-%d %H:%M:%S") if j.next_run else "n/a"
            lines.append(f"- `{j.id}` [{j.kind}] next: {when} — {j.goal}")

        await update.message.reply_text("🗓️ Jobs:\n" + "\n".join(lines), parse_mode="Markdown")

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel a scheduled job. Usage: /cancel <job_id>"""
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /cancel <job_id>")
            return

        job_id = context.args[0].strip()
        async with self._jobs_lock:
            job = self.jobs.get(job_id)
            if not job:
                await update.message.reply_text("Job not found.")
                return
            if job.user_id != update.effective_user.id:
                await update.message.reply_text("Not your job.")
                return
            job.enabled = False
            self.job_store.save(self.jobs)

        await update.message.reply_text(f"✅ Cancelled `{job_id}`", parse_mode="Markdown")

    async def cmd_heartbeat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Enable/disable a heartbeat check-in.
        Usage:
          /heartbeat off
          /heartbeat <minutes>
        """
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized.")
            return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        self.user_chat_ids[user_id] = chat_id

        if not context.args:
            await update.message.reply_text("Usage: /heartbeat off | /heartbeat <minutes>")
            return

        arg = context.args[0].strip().lower()
        # Use a dedicated interval job named by fixed id per user
        job_id = f"hb_{user_id}"

        if arg in {"off", "0", "false", "no"}:
            async with self._jobs_lock:
                if job_id in self.jobs:
                    self.jobs[job_id].enabled = False
                    self.job_store.save(self.jobs)
            await update.message.reply_text("✅ Heartbeat disabled.")
            return

        try:
            minutes = int(arg)
        except Exception:
            await update.message.reply_text("Heartbeat must be minutes (e.g. /heartbeat 30) or 'off'")
            return

        goal = "Heartbeat check-in: reply with anything you want me to do."
        job = ScheduledJob(
            id=job_id,
            user_id=user_id,
            chat_id=chat_id,
            kind="interval",
            goal=goal,
            enabled=True,
            created_at=datetime.now().isoformat(),
            interval_seconds=max(60, minutes * 60),
        )
        job.next_run = compute_next_run(job)

        async with self._jobs_lock:
            self.jobs[job_id] = job
            self.job_store.save(self.jobs)

        await update.message.reply_text(f"✅ Heartbeat enabled every {minutes} min.")
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular text messages - fully agentic execution."""
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized. Use /start")
            return
        
        user_id = update.effective_user.id
        user_message = update.message.text
        self.user_chat_ids[user_id] = update.effective_chat.id
        
        # Fast-path: model/version questions should be answered deterministically from config,
        # not by the LLM (which may guess/hallucinate).
        try:
            lm = (user_message or "").strip().lower()
            if any(k in lm for k in ["what model", "which model", "model r u", "model ru", "model are you", "gpt-4.1", "gpt 4.1", "gpt-5.1", "gpt 5.1"]):
                model_name = getattr(getattr(self.config, "model", None), "model_name", None) or "unknown"
                # In this repo we route planning/chat and Vision/SoM through the same configured model_name.
                await update.message.reply_text(
                    "Configured models:\n"
                    f"- Planner/Text: {model_name}\n"
                    f"- Vision: {model_name}\n"
                    f"- SoM Vision: {model_name}\n\n"
                    "If I said something else earlier, that was the LLM guessing. This is the real config."
                )
                return
        except Exception:
            # Never let status/help queries break execution.
            pass

        # Show typing indicator
        await update.message.chat.send_action('typing')
        
        try:
            if not self.agent:
                await update.message.reply_text("❌ Agent not initialized yet.")
                return

            # 0) Fast-path for simple direct controls (avoid planner overthinking)
            #
            # Games (DirectX/fullscreen) often ignore PyAutoGUI input. Our DesktopSkill can use
            # pydirectinput automatically (see ORBIT_DESKTOP_INPUT_BACKEND).
            lower_msg = re.sub(r"\s+", " ", (user_message or "").strip().lower())

            # 0b) Fast-path for flight searches: don't rely on planner guessing, and don't inherit old
            # Google Flights origin (e.g. "Manchester") from browser state.
            if any(k in lower_msg for k in ["cheapest", "cheap"]) and any(k in lower_msg for k in ["flight", "flights", "ticket", "tickets"]):
                # Minimal parser: recognize KL/KUL → Japan + date range like "11 feb to 20 feb"
                months = {
                    "jan": 1, "january": 1,
                    "feb": 2, "february": 2,
                    "mar": 3, "march": 3,
                    "apr": 4, "april": 4,
                    "may": 5,
                    "jun": 6, "june": 6,
                    "jul": 7, "july": 7,
                    "aug": 8, "august": 8,
                    "sep": 9, "sept": 9, "september": 9,
                    "oct": 10, "october": 10,
                    "nov": 11, "november": 11,
                    "dec": 12, "december": 12,
                }

                def _parse_day_month(s: str):
                    m = re.search(r"\b(\d{1,2})\s*(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\b", s)
                    if not m:
                        return None
                    day = int(m.group(1))
                    mon = months[m.group(2)]
                    return (day, mon)

                # defaults
                origin = "KUL"
                if any(k in lower_msg for k in ["singapore", "sin"]):
                    origin = "SIN"
                if any(k in lower_msg for k in ["kuala lumpur", "kul", "kl", "malaysia"]):
                    origin = "KUL"

                dest = "TYO"  # Tokyo area as default for "Japan"
                if "osaka" in lower_msg or "kix" in lower_msg:
                    dest = "OSA"
                if "japan" in lower_msg or "tokyo" in lower_msg:
                    dest = "TYO"

                # passengers
                pax = 2 if re.search(r"\b2\s*(pax|people|persons|adults)?\b", lower_msg) else 1

                # date range
                # supports: "11 feb to 20 feb"
                dm1 = _parse_day_month(lower_msg)
                dm2 = None
                m2 = re.search(r"\bto\b\s*(\d{1,2})\s*(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\b", lower_msg)
                if m2:
                    dm2 = (int(m2.group(1)), months[m2.group(2)])

                if not dm1 or not dm2:
                    await update.message.reply_text(
                        "Give me your date range so I can actually price it.\n"
                        "Example: `11 feb to 20 feb` (and optional: 1 or 2 people)."
                    )
                    return

                # Use current year unless user provided a year.
                year = datetime.now().year

                def yymmdd(day: int, mon: int) -> str:
                    return f"{year % 100:02d}{mon:02d}{day:02d}"

                out_date = yymmdd(dm1[0], dm1[1])
                in_date = yymmdd(dm2[0], dm2[1])

                status_msg = await update.message.reply_text("Searching flights… ⚡")

                browser = self.agent.skills.get_skill("browser_control")

                # Use multiple sources in separate tabs
                google_url = (
                    "https://www.google.com/travel/flights?q="
                    + __import__("urllib.parse").parse.quote(
                        f"Flights from {origin} to {dest} {dm1[0]} {list(months.keys())[list(months.values()).index(dm1[1])][:3]} {year} to {dm2[0]} {list(months.keys())[list(months.values()).index(dm2[1])][:3]} {year} round trip {pax} adults"
                    )
                )
                sky_url = f"https://www.skyscanner.com/transport/flights/{origin.lower()}/{dest.lower()}/{out_date}/{in_date}/?adultsv2={pax}&cabinclass=economy"

                # Execute browser actions directly (no planner)
                nav0 = await browser.execute(browser.input_schema(action="navigate", url=google_url, tab_index=0))
                if not getattr(nav0, "success", False):
                    await status_msg.edit_text(f"❌ Couldn't open Google Flights: {getattr(nav0,'error','')}")
                    return

                tab1 = await browser.execute(browser.input_schema(action="new_tab", url=sky_url))
                if not getattr(tab1, "success", False):
                    # still proceed with Google tab only
                    pass

                # Read tabs
                all_page_content = []
                for tab_idx in range(3):
                    try:
                        read_out = await browser.execute(browser.input_schema(action="read", tab_index=tab_idx))
                        if getattr(read_out, "success", False) and getattr(read_out, "data", None):
                            all_page_content.append(f"[Tab {tab_idx}]\n{read_out.data[:2500]}")
                    except Exception:
                        break

                if not all_page_content:
                    await status_msg.edit_text("❌ I opened the pages but couldn't read results. Try `/screenshot` or try again.")
                    return

                # Summarize with LLM
                from orbit_agent.models.base import Message
                client = self.agent.planner.router.get_client("planning")
                combined = "\n\n---\n\n".join(all_page_content)[:6000]
                prompt = (
                    f"User request: {user_message}\n\n"
                    f"I opened flight results for {origin} -> {dest} ({out_date} to {in_date}) for {pax} passenger(s).\n"
                    "From the content below, identify the cheapest price you can see and the site/tab it is from.\n"
                    "If you can't see a numeric price, say that and tell the user what field is missing.\n\n"
                    "CONTENT:\n" + combined
                )
                resp = await client.generate([Message(role="user", content=prompt)], temperature=0.2)
                await update.message.reply_text(resp.content)
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                return

            # Direct "press <key|combo>" (e.g. "press enter", "press ctrl+k", "try to press any button")
            press_match = re.search(r"\b(press|hit|tap)\b\s+(.+)$", lower_msg)
            if press_match and len(lower_msg) <= 80:
                rest = press_match.group(2).strip()

                # Normalize fluff
                rest = re.sub(r"^(the|a|an)\s+", "", rest)
                rest = rest.replace("+", " ")
                rest = rest.replace(" key", "").replace(" button", "")

                any_button = ("any button" in rest) or ("any key" in rest)

                key_map = {
                    "return": "enter",
                    "escape": "esc",
                    "control": "ctrl",
                    "windows": "win",
                }

                tokens = [t for t in rest.split(" ") if t and t not in {"the", "a", "an", "to", "please", "pls"}]
                if len(tokens) >= 2 and tokens[-1] == "arrow":
                    tokens = tokens[:-1]
                keys = [key_map.get(t, t) for t in tokens]

                # Candidate attempts for "any button"
                candidates: List[List[str]]
                if any_button:
                    candidates = [["enter"], ["space"], ["esc"]]
                else:
                    candidates = [keys] if keys else []

                if candidates:
                    status_msg = await update.message.reply_text("On it. ⚡")

                    from orbit_agent.skills.desktop import DesktopInput

                    desktop_skill = self.agent.skills.get_skill("computer_control")
                    vision_skill = self.agent.skills.get_skill("vision_analyze")

                    screenshots_dir = Path("screenshots")
                    screenshots_dir.mkdir(exist_ok=True)
                    before_path = str(screenshots_dir / f"uplink_direct_{user_id}_before.png")
                    after_path = str(screenshots_dir / f"uplink_direct_{user_id}_after.png")

                    press_any_query = "Is the text 'PRESS ANY BUTTON' visible on screen? Answer YES or NO."
                    was_press_any = False

                    # Best-effort: detect the common "press any button" gate so we can verify success.
                    try:
                        out_before = await desktop_skill.execute(DesktopInput(action="screenshot", save_path=before_path))
                        if out_before.success:
                            v = await vision_skill.execute(
                                vision_skill.input_schema(image_path=before_path, query=press_any_query, expect="yes")
                            )
                            was_press_any = bool(getattr(v, "success", False))
                    except Exception:
                        was_press_any = False

                    ok = False
                    last_err = None

                    for key_list in candidates:
                        # Use auto: pydirectinput if installed, else PyAutoGUI. Force PyAutoGUI for Win-key combos.
                        backend = "pyautogui" if any(k in {"win", "windows", "super", "command"} for k in key_list) else "auto"
                        out_press = await desktop_skill.execute(
                            DesktopInput(action="press", keys=key_list, backend=backend)
                        )
                        if not out_press.success:
                            last_err = out_press.error or out_press.data
                            continue

                        await desktop_skill.execute(DesktopInput(action="wait", duration=0.6))

                        if was_press_any:
                            out_after = await desktop_skill.execute(
                                DesktopInput(action="screenshot", save_path=after_path)
                            )
                            if out_after.success:
                                v2 = await vision_skill.execute(
                                    vision_skill.input_schema(image_path=after_path, query=press_any_query, expect="no")
                                )
                                if bool(getattr(v2, "success", False)):
                                    ok = True
                                    break
                        else:
                            ok = True
                            break

                    if not ok:
                        msg = "❌ Tried pressing keys but it didn't seem to take effect."
                        if last_err:
                            msg += f" ({last_err})"
                        if was_press_any:
                            msg += " The screen still shows 'PRESS ANY BUTTON'."
                        msg += " If this is a fullscreen/DirectX game, install `pydirectinput` and set `ORBIT_DESKTOP_INPUT_BACKEND=direct`."
                        await status_msg.edit_text(msg)
                        return

                    # Optional proof screenshot (if enabled)
                    if self.uplink_config.screenshot_on_task:
                        try:
                            from PIL import Image
                            import io

                            proof_path = after_path if os.path.exists(os.path.normpath(after_path)) else before_path
                            img = Image.open(os.path.normpath(proof_path)).convert("RGB")
                            max_width = 1280
                            if img.width > max_width:
                                ratio = max_width / img.width
                                img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)

                            bio = io.BytesIO()
                            img.save(bio, format="JPEG", quality=85)
                            bio.seek(0)
                            await update.message.reply_photo(photo=bio, caption="Done. ✨")
                            await status_msg.delete()
                        except Exception:
                            await status_msg.edit_text("Done. ✨")
                    else:
                        await status_msg.edit_text("Done. ✨")

                    return

            # Direct "click <thing>" (e.g. "click career")
            click_match = re.match(r"^\s*(?:can you\s+|please\s+|pls\s+)?click\s+(.+)$", lower_msg)
            if click_match and len(lower_msg) <= 80:
                target = click_match.group(1).strip()
                # Avoid stealing complex multi-step intents; let the planner handle those.
                if " and " not in target and " then " not in target:
                    target = re.sub(r"^(the|that|this)\s+", "", target).strip()
                    if target:
                        status_msg = await update.message.reply_text("On it. ⚡")
                        visual = self.agent.skills.get_skill("visual_interact")

                        # Make the description specific to avoid stale UI cache collisions.
                        desc = f"the '{target}' option/button on the current screen"
                        inp = visual.input_schema(description=desc, action="click")
                        out = await visual.execute(inp)

                        if not getattr(out, "success", False):
                            # Small game-friendly fallback: many menus are keyboard-navigable.
                            if "career" in target.lower():
                                try:
                                    from orbit_agent.skills.desktop import DesktopInput
                                    desktop = self.agent.skills.get_skill("computer_control")
                                    await desktop.execute(DesktopInput(action="press", keys=["down"], backend="auto"))
                                    await desktop.execute(DesktopInput(action="press", keys=["enter"], backend="auto"))
                                    await desktop.execute(DesktopInput(action="wait", duration=1.5))
                                except Exception:
                                    await status_msg.edit_text(f"❌ Couldn't click '{target}': {getattr(out, 'error', '')}")
                                    return
                            else:
                                await status_msg.edit_text(f"❌ Couldn't click '{target}': {getattr(out, 'error', '')}")
                                return

                        # Optional proof screenshot (if enabled)
                        if self.uplink_config.screenshot_on_task:
                            try:
                                from PIL import Image
                                import io
                                from orbit_agent.skills.desktop import DesktopInput

                                screenshots_dir = Path("screenshots")
                                screenshots_dir.mkdir(exist_ok=True)
                                save_path = str(screenshots_dir / f"uplink_click_{user_id}.png")

                                desktop = self.agent.skills.get_skill("computer_control")
                                ss = await desktop.execute(DesktopInput(action="screenshot", save_path=save_path))
                                if not ss.success:
                                    raise RuntimeError(ss.error or "Screenshot failed")

                                img = Image.open(os.path.normpath(save_path)).convert("RGB")
                                max_width = 1280
                                if img.width > max_width:
                                    ratio = max_width / img.width
                                    img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)

                                bio = io.BytesIO()
                                img.save(bio, format="JPEG", quality=85)
                                bio.seek(0)
                                await update.message.reply_photo(photo=bio, caption="Done. ✨")
                                await status_msg.delete()
                            except Exception:
                                await status_msg.edit_text("Done. ✨")
                        else:
                            await status_msg.edit_text("Done. ✨")

                        return

            # 1. Vision Analysis (if requested)
            vision_context = ""
            vision_keywords = ['screen', 'see', 'look', 'what is', "what's", 'show', 'display']
            if any(kw in user_message.lower() for kw in vision_keywords):
                from orbit_agent.skills.desktop import DesktopSkill, DesktopInput
                
                # Capture screen for context
                screenshots_dir = Path("screenshots")
                screenshots_dir.mkdir(exist_ok=True)
                image_path = screenshots_dir / f"uplink_{user_id}.png"
                desktop = DesktopSkill()
                out = await desktop.execute(DesktopInput(action="screenshot", save_path=str(image_path)))
                if out.success:
                    # Store path for the agent to use if needed
                    vision_context = f"\n[Context] User's current screen captured at: {image_path}"
                    # Send photo to user so they know we looked
                    with open(image_path, 'rb') as f:
                        await update.message.reply_photo(photo=f, caption="👀 Checking this screen...")
                else:
                    vision_context = "\n[Context] Screenshot capture failed."

            # 2. Planning
            goal = f"{user_message}{vision_context}"
            await update.message.reply_text("🧠 Thinking...")
            
            # Use the Planner to create a Task
            task = await self.agent.create_task(goal)
            
            if not task.steps:
                # No steps needed? Just a chat.
                response = await self.agent.chat(user_message, image_path=str(image_path) if 'image_path' in locals() else None)
                await update.message.reply_text(response)
                return

            self.active_tasks[user_id] = task.id
            
            # 3. Execution Loop
            # Pick a casual confirmation
            import random
            confirmations = ["On it.", "Working on that.", "Sure thing.", "Executing now.", "Got it."]
            status_msg = await update.message.reply_text(f"{random.choice(confirmations)} ⚡")
            
            last_step_was_chat = False
            prev_output = None  # For chaining: som_vision coords -> next click step
            visual_skills_used = False  # Track if we used desktop/browser/vision skills
            task_failed = False
            pending_verification = False  # Did we do state-changing actions without an explicit expect-check?
            replans_done = 0
            max_replans = int(os.environ.get("ORBIT_UPLINK_REPLAN_MAX", "1") or "1")
            history: List[str] = []
            steps = list(task.steps)
            
            i = 0
            while i < len(steps):
                step = steps[i]
                # Check for cancellation
                if user_id not in self.active_tasks:
                    await status_msg.edit_text("🛑 Cancelled.")
                    return

                try:
                    skill = self.agent.skills.get_skill(step.skill_name)
                    skill_config = dict(step.skill_config)
                    # --- Guardrails / Permissions (Uplink) ---
                    # NOTE: Telegram Uplink executes steps directly, so we must enforce safety here.
                    # 1) Permission policy (safe_mode blocks ASK permissions unless explicitly approved)
                    if getattr(self.config, "safe_mode", True):
                        approved = bool(step.skill_config.get("approved"))
                        for perm in getattr(skill.config, "permissions_required", []):
                            if self.agent.permissions.requires_approval(perm) and not approved:
                                # Small allowlist for safe launches (Steam URI)
                                allow = False
                                if step.skill_name == "shell_command" and perm == "shell_exec":
                                    cmd = str(skill_config.get("command", "")).strip().lower()
                                    if cmd.startswith("start steam://") or cmd.startswith('start "" steam://') or cmd.startswith("explorer steam://"):
                                        allow = True

                                if not allow:
                                    await status_msg.edit_text(
                                        f"🔒 Blocked step '{step.skill_name}' (needs approval for '{perm}'). "
                                        "This is safe_mode. To allow: set safe_mode=false or implement approvals."
                                    )
                                    raise RuntimeError("Blocked by permission policy")

                    # 2) LLM guardrail for high-risk skills
                    if step.skill_name in {"shell_command", "file_write", "file_edit", "skill_create"}:
                        ok, reason = await self.agent.guardrail.check(step.skill_name, skill_config)
                        if not ok:
                            await status_msg.edit_text(f"🔒 Guardrail REJECT for '{step.skill_name}': {reason}")
                            raise RuntimeError("Blocked by guardrail")

                    # Chain: if previous step was som_vision and returned coordinates, inject into this step if it's a click
                    if (step.skill_name == "computer_control" and skill_config.get("action") == "click" 
                            and prev_output and getattr(prev_output, "success", True) 
                            and getattr(prev_output, "coordinates", None)):
                        skill_config["x"], skill_config["y"] = prev_output.coordinates[0], prev_output.coordinates[1]
                    input_model = skill.input_schema(**skill_config)
                    output = await skill.execute(input_model)
                    prev_output = output
                    
                    # Unified failure detection across skills (some outputs don't have `success`)
                    failed = False
                    if getattr(output, "success", None) is False:
                        failed = True
                    if getattr(output, "exit_code", 0) not in (0, None):
                        failed = True
                    if getattr(output, "error", None):
                        failed = True
                    if getattr(output, "stderr", None) and getattr(output, "exit_code", 0) not in (0, None):
                        failed = True

                    if failed:
                        task_failed = True
                        error_msg = (
                            getattr(output, 'error', None)
                            or getattr(output, 'stderr', None)
                            or getattr(output, 'message', None)
                            or getattr(output, 'data', None)
                            or str(output)
                        )
                        # Try replanning before giving up.
                        if replans_done < max_replans:
                            replans_done += 1
                            try:
                                # Build compact history for the recovery planner.
                                hist = "\n".join(history[-12:])
                                error_context = (
                                    f"FAILED STEP: {step.id} ({step.skill_name})\n"
                                    f"CONFIG: {skill_config}\n"
                                    f"ERROR: {error_msg}\n\n"
                                    f"EXECUTION HISTORY:\n{hist}"
                                )
                                recovery_steps = await self.agent.planner.replan(goal, hist, error_context)
                                if recovery_steps:
                                    steps = list(recovery_steps)
                                    i = 0
                                    prev_output = None
                                    pending_verification = False
                                    task_failed = False
                                    await status_msg.edit_text(f"♻️ Hit a snag — trying a recovery plan (attempt {replans_done}/{max_replans})...")
                                    continue
                            except Exception:
                                pass

                        await status_msg.edit_text(f"❌ Snag hit on step '{step.skill_name}': {error_msg}")
                        break
                    
                    # Track visual/desktop skills (for screenshot decision)
                    if step.skill_name in ['computer_control', 'browser_control', 'app_control', 
                                            'vision_analyze', 'visual_interact', 'som_vision']:
                        visual_skills_used = True

                    # Track whether we performed a verifiable check after state-changing actions.
                    if step.skill_name in {"computer_control", "app_control", "browser_control", "visual_interact"}:
                        act = str(skill_config.get("action", "")).lower()
                        if step.skill_name != "computer_control" or act not in {"screenshot", "wait"}:
                            pending_verification = True
                    if step.skill_name == "vision_analyze" and skill_config.get("expect") in {"yes", "no"}:
                        pending_verification = False
                    
                    # Handle Chat Output specifically (ChatSkill uses 'message', others use 'data')
                    output_text = getattr(output, 'message', None) or getattr(output, 'data', None) or ""
                    if step.skill_name == 'chat' or (isinstance(output_text, str) and output_text.startswith("[CHAT]")):
                        clean_msg = output_text.replace("[CHAT] ", "")
                        if pending_verification:
                            clean_msg = (
                                f"{clean_msg}\n\n"
                                "⚠️ I executed the actions but didn’t run an on-screen verification step. "
                                "If it didn’t work, send `/screenshot` and I’ll debug from the current frame."
                            )
                        await update.message.reply_text(clean_msg)
                        last_step_was_chat = True
                    else:
                        last_step_was_chat = False

                    # Append history (compact) for potential recovery replans.
                    try:
                        out_summary = getattr(output, "error", None) or getattr(output, "data", None) or getattr(output, "message", None) or str(output)
                        history.append(f"- {step.skill_name} {str(skill_config)[:180]} => {str(out_summary)[:180]}")
                    except Exception:
                        pass
                        
                except Exception as step_err:
                    task_failed = True
                    # Try replanning before giving up.
                    if replans_done < max_replans:
                        replans_done += 1
                        try:
                            hist = "\n".join(history[-12:])
                            error_context = (
                                f"FAILED STEP: {step.id} ({step.skill_name})\n"
                                f"CONFIG: {skill_config if 'skill_config' in locals() else step.skill_config}\n"
                                f"ERROR: {step_err}\n\n"
                                f"EXECUTION HISTORY:\n{hist}"
                            )
                            recovery_steps = await self.agent.planner.replan(goal, hist, error_context)
                            if recovery_steps:
                                steps = list(recovery_steps)
                                i = 0
                                prev_output = None
                                pending_verification = False
                                task_failed = False
                                await status_msg.edit_text(f"♻️ Hit a snag — trying a recovery plan (attempt {replans_done}/{max_replans})...")
                                continue
                        except Exception:
                            pass

                    await status_msg.edit_text(f"💥 Error: {step_err}")
                    break

                i += 1
            
            if user_id in self.active_tasks:
                del self.active_tasks[user_id]

                # Do not overwrite an error/snags with "Done".
                if task_failed:
                    return

                # Post-verify Discord voice join/leave intents (avoid claiming success incorrectly)
                lm = user_message.lower()
                wants_voice = "voice" in lm and ("discord" in lm or "vc" in lm)
                if wants_voice:
                    # Heuristic intent
                    expect_connected = ("leave" not in lm and "disconnect" not in lm and "quit" not in lm)
                    verified = await self._verify_discord_voice_state(user_id=user_id, expect_connected=expect_connected)
                    if verified is False:
                        await update.message.reply_text(
                            "⚠️ I couldn't verify the Discord voice state changed on screen. "
                            "If you're not connected/disconnected, try again or send /screenshot so we can debug."
                        )
                
                # AGENTIC REFLECTION: If browser was used, read page and summarize
                browser_was_used = any(s.skill_name == 'browser_control' for s in task.steps)
                
                if browser_was_used and not last_step_was_chat:
                    try:
                        # Step 1: Read ALL open tabs
                        browser_skill = self.agent.skills.get_skill('browser_control')
                        all_page_content = []
                        
                        # Try reading up to 5 tabs
                        for tab_idx in range(5):
                            try:
                                read_input = browser_skill.input_schema(action='read', tab_index=tab_idx)
                                read_output = await browser_skill.execute(read_input)
                                if read_output.success and read_output.data:
                                    # Limit each tab's content
                                    tab_content = read_output.data[:2000]
                                    all_page_content.append(f"[Tab {tab_idx}]:\n{tab_content}")
                            except:
                                break  # No more tabs
                        
                        if all_page_content:
                            combined_content = "\n\n---\n\n".join(all_page_content)[:5000]
                            
                            # Step 2: Ask LLM to compare and extract the best answer
                            from orbit_agent.models.base import Message
                            client = self.agent.planner.router.get_client("planning")
                            
                            num_tabs = len(all_page_content)
                            reflection_prompt = f"""You are a helpful assistant. The user asked: "{user_message}"

I browsed {num_tabs} website(s) and found this content:
---
{combined_content}
---

Based on ALL sources, provide a helpful, conversational answer.
If comparing prices, mention the BEST option and where it's from.
Be specific with numbers, names, times. Keep it concise (2-4 sentences).
Answer naturally as if you're a helpful friend."""

                            messages = [Message(role="user", content=reflection_prompt)]
                            response = await client.generate(messages, temperature=0.3)
                            
                            # Step 3: Send the answer to user
                            await update.message.reply_text(response.content)
                            last_step_was_chat = True
                            
                    except Exception as reflect_err:
                        logger.warning(f"Reflection failed: {reflect_err}")
                        # Fall through to screenshot
                
                # If we just chatted, clean up status message
                if last_step_was_chat:
                    try:
                        await status_msg.delete()
                    except:
                        pass
                
                # Smart screenshot: Only send if visual/desktop skills were used OR user explicitly asked
                user_asked_for_screenshot = any(kw in user_message.lower() for kw in [
                    'screenshot', 'show screen', "what's on screen", 'show me', 'capture', 
                    'take a picture', 'what do you see', 'look at', 'check screen'
                ])
                
                should_send_screenshot = (
                    self.uplink_config.screenshot_on_task and 
                    (visual_skills_used or user_asked_for_screenshot) and
                    not (last_step_was_chat and not visual_skills_used)  # Don't screenshot pure Q&A
                )
                
                # Screenshot as proof (only when visual actions happened or user asked)
                if should_send_screenshot:
                    try:
                        from PIL import Image
                        import io
                        
                        await asyncio.sleep(0.5)

                        # Use DesktopSkill screenshot (more reliable than mss for GPU-accelerated apps/games on Windows)
                        from orbit_agent.skills.desktop import DesktopSkill, DesktopInput
                        screenshots_dir = Path("screenshots")
                        screenshots_dir.mkdir(exist_ok=True)
                        save_path = str(screenshots_dir / f"uplink_done_{user_id}.png")

                        desktop = DesktopSkill()
                        out = await desktop.execute(DesktopInput(action="screenshot", save_path=save_path))
                        if not out.success:
                            raise RuntimeError(out.error or "Screenshot failed")

                        img = Image.open(os.path.normpath(save_path)).convert("RGB")
                        max_width = 1280
                        if img.width > max_width:
                            ratio = max_width / img.width
                            img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)

                        bio = io.BytesIO()
                        img.save(bio, format="JPEG", quality=85)
                        bio.seek(0)
                        
                        # If we already chatted, just send screenshot silently
                        caption = "📸" if last_step_was_chat else ("Done. ✨" if not pending_verification else "Done (not verified). ⚠️")
                        await update.message.reply_photo(photo=bio, caption=caption)
                        
                        if not last_step_was_chat:
                            await status_msg.delete()
                    except Exception as ss_err:
                        if not last_step_was_chat:
                            await status_msg.edit_text("Done. ✨" if not pending_verification else "Done (not verified). ⚠️")
                elif not last_step_was_chat:
                    await status_msg.edit_text("Done. ✨" if not pending_verification else "Done (not verified). ⚠️")

        except Exception as e:
            # Safe logging for Windows consoles
            try:
                msg = str(e).encode('ascii', 'replace').decode('ascii')
                logger.error(f"Error handling message: {msg}")
            except:
                logger.error("Error handling message (encoding failed)")
                
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

        # Start scheduler (proactive jobs)
        if not self._scheduler_task:
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        
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
