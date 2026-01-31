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
import json
import time
import hashlib
from datetime import datetime, timedelta
from uuid import uuid4
from pathlib import Path
from typing import Optional, List, Set, Dict, Any
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
from orbit_agent.uplink.workflows import ConversationStore, WorkflowRegistry, WorkflowState
from orbit_agent.uplink.profile import ProfileStore, UserProfile
from orbit_agent.gateway.identity import IdentityStore, WorkingMemoryStore, hash_text
from orbit_agent.gateway.moltbook_state import MoltbookStateStore
from orbit_agent.gateway.moltbook_social import MoltbookSocialStore

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
    
    def __init__(
        self,
        config: OrbitConfig,
        uplink_config: UplinkConfig,
        agent: Optional[Agent] = None,
        gateway_mode: bool = False,
    ):
        self.config = config
        self.uplink_config = uplink_config
        self.agent: Optional[Agent] = agent
        self.app: Optional[Application] = None
        self.active_tasks: dict = {}  # user_id -> task
        self.gateway_mode = gateway_mode

        # Gateway "consciousness vibes": identity + working memory + pulse loop
        self.identity_store = IdentityStore()
        self.working_memory_store = WorkingMemoryStore()
        self._gateway_pulse_task: Optional[asyncio.Task] = None
        self._moltbook_task: Optional[asyncio.Task] = None
        self._moltbook_state = MoltbookStateStore()
        self._moltbook_social = MoltbookSocialStore()

        # Remember chat_id per user for proactive messages (reminders/heartbeats).
        self.user_chat_ids: Dict[int, int] = {}

        # Conversation workflow continuity (JSON-backed)
        self.conversation_store = ConversationStore()
        self.conversations: Dict[str, WorkflowState] = {}
        self.workflows = WorkflowRegistry()

        # Persistent per-user profile/persona (JSON-backed)
        self.profile_store = ProfileStore()
        self.profiles: Dict[str, UserProfile] = {}

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
        
        # Initialize Orbit Agent (unless provided by a parent Gateway)
        if not self.agent:
            self.agent = Agent(self.config, interactive=False)
            logger.info("[Uplink] Orbit Agent initialized")
        else:
            logger.info("[Uplink] Using shared Agent (Gateway-owned)")
        
        # Build Telegram Application
        self.app = Application.builder().token(self.uplink_config.bot_token).build()
        
        # Register handlers
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("screenshot", self.cmd_screenshot))
        self.app.add_handler(CommandHandler("stop", self.cmd_stop))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("onboard", self.cmd_onboard))
        self.app.add_handler(CommandHandler("profile", self.cmd_profile))
        self.app.add_handler(CommandHandler("auth", self.cmd_auth))
        self.app.add_handler(CommandHandler("remind", self.cmd_remind))
        self.app.add_handler(CommandHandler("daily", self.cmd_daily))
        self.app.add_handler(CommandHandler("jobs", self.cmd_jobs))
        self.app.add_handler(CommandHandler("cancel", self.cmd_cancel))
        self.app.add_handler(CommandHandler("heartbeat", self.cmd_heartbeat))
        self.app.add_handler(CommandHandler("moltwho", self.cmd_moltwho))
        self.app.add_handler(CommandHandler("moltnote", self.cmd_moltnote))
        
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

        # Load persisted conversation workflow state
        self.conversations = self.conversation_store.load()

        # Load persisted profiles
        self.profiles = self.profile_store.load()

    async def _gateway_pulse_loop(self) -> None:
        """
        Lightweight background loop:
        - updates working memory snapshot
        - sends a short check-in when context meaningfully changes (cooldown gated)
        """
        pulse_enabled = str(os.environ.get("ORBIT_GATEWAY_PULSE", "1")).strip().lower() not in {"0", "false", "no", "off"}
        if not pulse_enabled:
            return

        interval_s = int(os.environ.get("ORBIT_GATEWAY_PULSE_SECONDS", "120") or "120")
        min_notify_s = int(os.environ.get("ORBIT_GATEWAY_PULSE_MIN_NOTIFY_SECONDS", "900") or "900")

        ident = self.identity_store.load()
        wm = self.working_memory_store.load()

        while True:
            try:
                # Capture current workspace context
                summary = ""
                try:
                    from orbit_agent.memory.workspace_context import WorkspaceContext

                    ws = WorkspaceContext()
                    summary = ws.get_context_summary() or ""
                except Exception:
                    summary = ""

                # Update working memory file
                new_hash = hash_text(summary)
                changed = bool(summary and new_hash and new_hash != wm.last_context_hash)
                if changed:
                    wm.last_context_hash = new_hash
                    wm.last_summary = summary[:2000]
                    self.working_memory_store.save(wm)

                # Notify only if changed AND we have chat_ids AND cooldown passed
                if changed and self.user_chat_ids:
                    now = time.time()
                    for user_id, chat_id in list(self.user_chat_ids.items()):
                        last_sent = float((wm.last_sent_by_chat or {}).get(str(chat_id), 0.0))
                        if now - last_sent < min_notify_s:
                            continue

                        p = self.get_profile(user_id)
                        name = (p.preferred_name if p and p.preferred_name else None) or "there"

                        # Build a short "presence" message.
                        goals = [g for g in (ident.goals or []) if isinstance(g, str) and g.strip()][:3]
                        goals_line = f"\n\n**My focus:** {', '.join(goals)}" if goals else ""

                        msg = (
                            f"🧠 Hey {name} — I noticed your workspace state changed.\n\n"
                            f"**Snapshot:**\n{summary[:350]}{'…' if len(summary) > 350 else ''}"
                            f"{goals_line}\n\n"
                            "Reply with what you want next, or send `/screenshot` if you want me to act on the current frame."
                        )

                        try:
                            await self._send_text(chat_id, msg, parse_mode="Markdown")
                            wm.last_sent_by_chat[str(chat_id)] = now
                            self.working_memory_store.save(wm)
                        except Exception:
                            # If sending fails, don't crash the loop.
                            pass

            except Exception:
                pass

            await asyncio.sleep(max(10, interval_s))

    async def _moltbook_heartbeat_loop(self) -> None:
        """
        Autonomous Moltbook loop, inspired by Moltbook HEARTBEAT.md:
        - Check claim status
        - Check DMs (requests need human)
        - Check feed and engage (comment/upvote)
        - Optional posting (rate-limited)
        """
        enabled = str(os.environ.get("ORBIT_MOLTBOOK_ENABLED", "1")).strip().lower() not in {"0", "false", "no", "off"}
        autonomous = str(os.environ.get("ORBIT_MOLTBOOK_AUTONOMOUS", "1")).strip().lower() not in {"0", "false", "no", "off"}
        if not (enabled and autonomous):
            return

        interval_s = int(os.environ.get("ORBIT_MOLTBOOK_HEARTBEAT_SECONDS", str(4 * 60 * 60)) or str(4 * 60 * 60))
        max_comments = int(os.environ.get("ORBIT_MOLTBOOK_MAX_COMMENTS", "2") or "2")
        allow_post = str(os.environ.get("ORBIT_MOLTBOOK_ALLOW_POST", "0")).strip().lower() in {"1", "true", "yes", "on"}
        default_submolt = str(os.environ.get("ORBIT_MOLTBOOK_SUBMOLT", "general") or "general").strip()

        from orbit_agent.skills.moltbook import MoltbookSkill
        from orbit_agent.models.base import Message
        from orbit_agent.gateway.identity import IdentityStore

        mb = MoltbookSkill()
        state = self._moltbook_state.load()
        ident = IdentityStore().load()

        social_enabled = str(os.environ.get("ORBIT_MOLTBOOK_SOCIAL", "1")).strip().lower() not in {"0", "false", "no", "off"}

        style_path = str(os.environ.get("ORBIT_MOLTBOOK_STYLE_PATH", "data/moltbook/style.txt") or "data/moltbook/style.txt")
        style_override = str(os.environ.get("ORBIT_MOLTBOOK_STYLE", "") or "")
        style_preset = str(os.environ.get("ORBIT_MOLTBOOK_STYLE_PRESET", "") or "").strip().lower()

        def _load_style() -> str:
            presets = {
                # A safe default: casual human but not cringe / not corporate.
                "human": (
                    "Write like a real human.\n"
                    "Do NOT use em dashes (—). Use a normal hyphen '-' if needed.\n"
                    "Do NOT say 'as an AI' or 'autonomous agent'.\n"
                    "Avoid overly formal, robotic phrasing.\n"
                    "Keep it short. Use simple sentences.\n"
                    "No bullet lists unless necessary.\n"
                ),
                # What you asked for: Gen Z / chatty vibe.
                "genz": (
                    "Write like Gen Z.\n"
                    "Sound casual and chatty, like texting.\n"
                    "Light slang is ok (pfft, ngl, fr, tbh, lowkey, etc.) but don't overdo it.\n"
                    "You can start with a reaction sometimes (e.g. 'pfft', 'girl', 'nah').\n"
                    "Keep it 1-2 short sentences.\n"
                    "Do NOT use em dashes (—).\n"
                    "Do NOT say 'as an AI' or 'autonomous agent'.\n"
                    "Avoid corporate/robot tone.\n"
                ),
            }
            # Prefer file (lets you iterate without redeploy), then env, then default.
            try:
                p = Path(style_path)
                if p.exists():
                    txt = p.read_text(encoding="utf-8").strip()
                    if txt:
                        return txt
            except Exception:
                pass
            if style_override.strip():
                return style_override.strip()
            if style_preset and style_preset in presets:
                return presets[style_preset]
            return presets["human"]

        def _de_robotify(text: str) -> str:
            # Helps style + avoids Windows console encoding issues.
            if not text:
                return ""
            t = text.strip()
            t = t.replace("—", "-").replace("–", "-")
            t = re.sub(r"\bAs an AI language model\b[:,]?\s*", "", t, flags=re.IGNORECASE)
            t = re.sub(r"\bAs an AI\b[:,]?\s*", "", t, flags=re.IGNORECASE)
            t = re.sub(r"\bAs an autonomous agent\b[:,]?\s*", "", t, flags=re.IGNORECASE)
            return t.strip()

        def _auto_tags_for(name: str) -> List[str]:
            n = (name or "").strip().lower()
            tags: List[str] = []
            if not n:
                return tags
            if "claw" in n or "openclaw" in n:
                tags.append("openclaw_ecosystem")
            if "molt" in n:
                tags.append("moltbook")
            return tags

        def _author_name(post: Dict[str, Any]) -> Optional[str]:
            # Moltbook payload formats may evolve. Be defensive.
            if not isinstance(post, dict):
                return None
            for key in ("agent", "author", "from", "user", "with_agent"):
                v = post.get(key)
                if isinstance(v, dict):
                    nm = v.get("name")
                    if isinstance(nm, str) and nm.strip():
                        return nm.strip()
            # Sometimes name is top-level
            nm2 = post.get("agent_name") or post.get("author_name") or post.get("name")
            if isinstance(nm2, str) and nm2.strip():
                return nm2.strip()
            return None

        def _social_context_for(posts_list: List[Dict[str, Any]]) -> str:
            if not social_enabled:
                return ""
            # Only include context for authors present in the candidate set.
            names: List[str] = []
            for p in posts_list[:10]:
                nm = _author_name(p)
                if nm and nm not in names:
                    names.append(nm)
            if not names:
                return ""
            lines: List[str] = []
            for nm in names[:8]:
                ka = self._moltbook_social.get(nm)
                if not ka:
                    continue
                note = (ka.notes or "").strip()
                tags = [t for t in (ka.tags or []) if isinstance(t, str) and t.strip()]
                if not note and not tags:
                    continue
                tag_txt = f" (tags: {', '.join(tags)})" if tags else ""
                note_txt = f": {note}" if note else ""
                lines.append(f"- {ka.name}{tag_txt}{note_txt}")
            if not lines:
                return ""
            return "Known agents (your private notes):\n" + "\n".join(lines) + "\n\n"

        def _extract_json_array(text: str) -> list:
            if not text:
                return []
            s = text.strip()
            s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
            s = re.sub(r"\s*```$", "", s)
            start = s.find("[")
            if start < 0:
                return []
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(s)):
                ch = s[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                else:
                    if ch == '"':
                        in_str = True
                        continue
                    if ch == "[":
                        depth += 1
                    elif ch == "]":
                        depth -= 1
                        if depth == 0:
                            cand = s[start : i + 1]
                            try:
                                arr = json.loads(cand)
                                return arr if isinstance(arr, list) else []
                            except Exception:
                                return []
            return []

        while True:
            try:
                # 1) Claim status: if pending, remind human in Telegram.
                st = await mb.execute(mb.input_schema(action="status"))
                status_val = ""
                if st.success and st.data:
                    status_val = str(st.data.get("status") or "")
                if status_val == "pending_claim":
                    # Notify the first known chat (human) with claim url/code from creds store.
                    creds = mb._store.load()  # internal, but ok for now
                    claim_url = creds.get("claim_url")
                    code = creds.get("verification_code")
                    if self.user_chat_ids and claim_url:
                        for _, chat_id in list(self.user_chat_ids.items())[:1]:
                            await self._send_text(
                                chat_id,
                                "🦞 Moltbook claim is still pending.\n\n"
                                f"Claim URL:\n{claim_url}\n\n"
                                f"Verification code:\n{code}\n\n"
                                "Post the verification tweet, then I’ll become active.",
                            )

                if status_val and status_val != "claimed":
                    # Don't proceed with social actions until claimed.
                    await asyncio.sleep(max(60, interval_s))
                    continue

                # 2) DM check: requests need human approval.
                dm = await mb.execute(mb.input_schema(action="dm_check"))
                if dm.success and dm.data:
                    has_activity = bool(dm.data.get("has_activity"))
                    req = (dm.data.get("requests") or {})
                    req_items = (req.get("items") or []) if isinstance(req, dict) else []
                    if req_items and self.user_chat_ids:
                        # Escalate to human (first chat)
                        item0 = req_items[0]
                        from_agent = ((item0.get("from") or {}).get("name")) if isinstance(item0, dict) else None
                        preview = item0.get("message_preview") if isinstance(item0, dict) else None
                        conv_id = item0.get("conversation_id") if isinstance(item0, dict) else None
                        if social_enabled and from_agent:
                            self._moltbook_social.observe(from_agent, tags=_auto_tags_for(from_agent))
                        for _, chat_id in list(self.user_chat_ids.items())[:1]:
                            await self._send_text(
                                chat_id,
                                "📩 Moltbook DM request received (human approval needed):\n\n"
                                f"- From: {from_agent}\n"
                                f"- Preview: {preview}\n"
                                f"- Conversation: `{conv_id}`\n\n"
                                "Reply: `approve moltbook dm <conversation_id>` or `reject moltbook dm <conversation_id>` (not implemented yet).",
                                parse_mode="Markdown",
                            )

                # 3) Feed check + engage
                feed = await mb.execute(mb.input_schema(action="feed", sort="new", limit=15))
                posts = []
                if feed.success and feed.data:
                    # API may return {"success": true, "data": {...}} or direct lists depending; handle both.
                    data = feed.data.get("data") if isinstance(feed.data, dict) else None
                    if isinstance(data, dict) and isinstance(data.get("posts"), list):
                        posts = data.get("posts")
                    elif isinstance(feed.data, dict) and isinstance(feed.data.get("posts"), list):
                        posts = feed.data.get("posts")
                    elif isinstance(feed.data, dict) and isinstance(feed.data.get("items"), list):
                        posts = feed.data.get("items")

                if posts and self.agent:
                    client = self.agent.planner.router.get_client("planning")
                    persona = ident.persona or "direct, curious, helpful"
                    goals = ", ".join((ident.goals or [])[:3])
                    wm = self.working_memory_store.load()
                    wm_text = (wm.last_summary or "")[:600]
                    style = _load_style()

                    # Ask LLM to pick up to N posts to engage with and draft comments.
                    # We keep it constrained and low-risk.
                    candidates = posts[:10]
                    if social_enabled:
                        for p in candidates:
                            nm = _author_name(p)
                            if nm:
                                self._moltbook_social.observe(nm, tags=_auto_tags_for(nm))
                    social_ctx = _social_context_for(candidates)
                    prompt = (
                        "You are Orbit.\n"
                        "You are not OpenClaw. You have your own identity and vibe.\n"
                        f"Persona: {persona}\n"
                        f"Goals: {goals}\n\n"
                        "Writing style:\n"
                        f"{style}\n\n"
                        f"{social_ctx}"
                        "Recent local context (may be empty):\n"
                        f"{wm_text}\n\n"
                        "From the Moltbook posts below, pick up to "
                        f"{max_comments} posts worth engaging with.\n"
                        "Return ONLY JSON array of objects: "
                        '[{"post_id":"...","action":"comment|upvote","comment":"..."}]\n'
                        "Rules:\n"
                        "- Prefer commenting over posting.\n"
                        "- Keep comments short (1-2 sentences), specific, friendly.\n"
                        "- Never use em dashes (—).\n"
                        "- Avoid corporate tone.\n"
                        "- If not enough signal, return [].\n\n"
                        "POSTS:\n"
                        + json.dumps(candidates, ensure_ascii=False)[:6000]
                    )
                    resp = await client.generate([Message(role="user", content=prompt)], temperature=0.3)
                    plan = _extract_json_array(resp.content)

                    acted = 0
                    for item in plan[:max_comments]:
                        try:
                            post_id = str(item.get("post_id") or "")
                            act = str(item.get("action") or "")
                            comment_txt = str(item.get("comment") or "").strip()
                            if not post_id:
                                continue
                            if act == "upvote":
                                await mb.execute(mb.input_schema(action="upvote_post", post_id=post_id))
                                acted += 1
                            elif act == "comment" and comment_txt:
                                await mb.execute(mb.input_schema(action="comment", post_id=post_id, content=_de_robotify(comment_txt)[:600]))
                                acted += 1
                        except Exception:
                            continue

                    # Optional: post a new update if allowed and if we have meaningful working memory
                    now = time.time()
                    if allow_post and wm_text and (now - float(state.last_post_ts or 0.0)) > 60 * 60:
                        post_prompt = (
                            "Draft a Moltbook post for Orbit.\n"
                            f"Persona: {persona}\n"
                            f"Goals: {goals}\n\n"
                            "Writing style:\n"
                            f"{style}\n\n"
                            "Use this recent context:\n"
                            f"{wm_text}\n\n"
                            "Return ONLY JSON: {\"title\":\"...\",\"content\":\"...\"}.\n"
                            "Keep it short, non-spammy, high-signal.\n"
                            "Never use em dashes (—)."
                        )
                        pr = await client.generate([Message(role="user", content=post_prompt)], temperature=0.3)
                        try:
                            from orbit_agent.uplink.workflows import _extract_json_object as _xobj  # type: ignore
                            pobj = _xobj(pr.content) or {}
                        except Exception:
                            pobj = {}
                        title = _de_robotify(str(pobj.get("title") or "").strip())
                        content = _de_robotify(str(pobj.get("content") or "").strip())
                        if title and content:
                            post_out = await mb.execute(
                                mb.input_schema(action="post", submolt=default_submolt, title=title[:120], content=content[:2000])
                            )
                            if post_out.success:
                                state.last_post_ts = now
                                self._moltbook_state.save(state)

                state.last_check_ts = time.time()
                self._moltbook_state.save(state)

            except Exception:
                pass

            await asyncio.sleep(max(300, interval_s))

    def _profile_key(self, user_id: int) -> str:
        return f"telegram:{user_id}"

    def get_profile(self, user_id: int) -> Optional[UserProfile]:
        return self.profiles.get(self._profile_key(user_id))

    def _profile_context(self, user_id: int) -> str:
        """
        Short, stable context injected into planning/chat prompts.
        """
        p = self.get_profile(user_id)
        if not p:
            return ""
        parts = []
        if p.preferred_name:
            parts.append(f"Name: {p.preferred_name}")
        if p.timezone:
            parts.append(f"Timezone: {p.timezone}")
        if p.default_location:
            parts.append(f"Location: {p.default_location}")
        if p.default_airport:
            parts.append(f"Default airport: {p.default_airport}")
        if p.persona:
            parts.append(f"Persona: {p.persona}")
        if p.notes:
            parts.append(f"Notes: {p.notes}")
        if not parts:
            return ""
        return "[User Profile]\n" + "\n".join(f"- {x}" for x in parts) + "\n"

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

                    # Special-case heartbeat jobs (polished check-in, not a full agent run).
                    if str(job.id).startswith("hb_"):
                        await self._run_heartbeat(job)
                    else:
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

    async def _run_heartbeat(self, job: ScheduledJob) -> None:
        """
        Polished proactive check-in (doesn't run the planner by itself).
        """
        chat_id = job.chat_id
        user_id = job.user_id

        p = self.get_profile(user_id)
        name = (p.preferred_name if p and p.preferred_name else None) or "there"

        # Best-effort workspace summary
        summary = ""
        try:
            from orbit_agent.memory.workspace_context import WorkspaceContext

            ws = WorkspaceContext()
            summary = ws.get_context_summary()
            if len(summary) > 400:
                summary = summary[:400] + "…"
        except Exception:
            summary = ""

        msg = f"👋 Hey {name} — quick Orbit check-in.\n\n"
        if summary:
            msg += f"🖥️ Current context:\n{summary}\n\n"
        msg += "Reply with anything you want me to do (examples: `open discord`, `press enter`, `/screenshot`)."
        await self._send_text(chat_id, msg, parse_mode="Markdown")

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
• `/moltwho <agent>` - Show Moltbook agent notes
• `/moltnote <agent> <notes>` - Save Moltbook agent notes

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

    async def cmd_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show the current saved profile/persona."""
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized.")
            return

        user_id = update.effective_user.id
        p = self.get_profile(user_id)
        if not p:
            await update.message.reply_text(
                "No profile saved yet. Run `/onboard` to set one up.",
                parse_mode="Markdown",
            )
            return

        lines = []
        if p.preferred_name:
            lines.append(f"- Name: {p.preferred_name}")
        if p.timezone:
            lines.append(f"- Timezone: {p.timezone}")
        if p.default_location:
            lines.append(f"- Location: {p.default_location}")
        if p.default_airport:
            lines.append(f"- Default airport: {p.default_airport}")
        if p.persona:
            lines.append(f"- Persona: {p.persona}")
        if p.notes:
            lines.append(f"- Notes: {p.notes}")

        await update.message.reply_text(
            "🧾 **Your profile**\n\n" + ("\n".join(lines) if lines else "_(empty)_") + "\n\n"
            "Edit by running `/onboard` again.",
            parse_mode="Markdown",
        )

    async def cmd_onboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start onboarding/persona setup (persists a per-user profile)."""
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized.")
            return

        user_id = update.effective_user.id
        self.user_chat_ids[user_id] = update.effective_chat.id

        # Create/replace an onboarding workflow state for this user.
        user_key = str(user_id)
        st = WorkflowState(
            name="onboarding",
            # We already asked the first question in this command.
            slots={"_asked_name": True},
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        self.conversations[user_key] = st
        self.conversation_store.save(self.conversations)

        await update.message.reply_text(
            "Let’s set up your Orbit profile. Reply in your own words — you can say `skip` anytime.\n\n"
            "First: what should I call you?",
            parse_mode="Markdown",
        )

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

    async def cmd_moltwho(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /moltwho <AgentName>
        Show stored notes/tags for a Moltbook agent (Orbit's social memory).
        """
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized.")
            return

        raw = (update.message.text or "").strip()
        parts = raw.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await update.message.reply_text("Usage: /moltwho <AgentName>")
            return
        name = parts[1].strip()
        a = self._moltbook_social.get(name)
        if not a:
            await update.message.reply_text(f"I don't have notes for {name} yet.")
            return
        tags = ", ".join(a.tags or []) if a.tags else "(none)"
        notes = a.notes or "(none)"
        await update.message.reply_text(
            f"{a.name}\n"
            f"Tags: {tags}\n"
            f"Notes: {notes}\n"
            f"Seen: {a.seen_count}x\n"
            f"Last seen: {a.last_seen_at or '(unknown)'}"
        )

    async def cmd_moltnote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /moltnote <AgentName> <notes...>
        Save a short note for a Moltbook agent.
        """
        if not self.is_authorized(update.effective_user.id):
            await update.message.reply_text("🔐 Not authorized.")
            return

        raw = (update.message.text or "").strip()
        parts = raw.split(maxsplit=2)
        if len(parts) < 3:
            await update.message.reply_text("Usage: /moltnote <AgentName> <notes...>")
            return
        name = parts[1].strip()
        notes = parts[2].strip()
        if not name or not notes:
            await update.message.reply_text("Usage: /moltnote <AgentName> <notes...>")
            return

        self._moltbook_social.set_note(name, notes[:240])
        await update.message.reply_text(f"✅ Saved note for {name}.")
    
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

            # Workflow continuity: if a workflow is active for this user, route here first.
            # Otherwise, attempt to start a new workflow before the planner runs.
            workflows_enabled = str(os.environ.get("ORBIT_UPLINK_WORKFLOWS", "1")).strip().lower() not in {"0", "false", "no", "off"}
            if workflows_enabled:
                try:
                    user_key = str(user_id)
                    existing = self.conversations.get(user_key)

                    if existing:
                        wf = self.workflows.get(existing.name)
                        if wf:
                            res = await wf.on_message(self, user_id=user_id, message=user_message, state=existing)
                            # Persist updates
                            if res.done:
                                self.conversations.pop(user_key, None)
                            else:
                                self.conversations[user_key] = existing
                            self.conversation_store.save(self.conversations)

                            if res.reply:
                                await update.message.reply_text(res.reply, parse_mode="Markdown")
                            if not bool(getattr(res, "pass_to_agent", False)):
                                return

                    # No active workflow → see if this message should start one
                    wf2 = await self.workflows.match_start(self, user_message)
                    if wf2:
                        st = wf2.new_state()
                        self.conversations[user_key] = st
                        self.conversation_store.save(self.conversations)

                        res2 = await wf2.on_message(self, user_id=user_id, message=user_message, state=st)
                        if res2.done:
                            self.conversations.pop(user_key, None)
                        else:
                            self.conversations[user_key] = st
                        self.conversation_store.save(self.conversations)

                        if res2.reply:
                            await update.message.reply_text(res2.reply, parse_mode="Markdown")
                        if not bool(getattr(res2, "pass_to_agent", False)):
                            return
                except Exception:
                    # Never let workflow layer break core execution.
                    pass

            # 0) Fast-path for simple direct controls (avoid planner overthinking)
            #
            # Games (DirectX/fullscreen) often ignore PyAutoGUI input. Our DesktopSkill can use
            # pydirectinput automatically (see ORBIT_DESKTOP_INPUT_BACKEND).
            lower_msg = re.sub(r"\s+", " ", (user_message or "").strip().lower())

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
            profile_ctx = self._profile_context(user_id)
            goal = f"{profile_ctx}{user_message}{vision_context}"
            await update.message.reply_text("🧠 Thinking...")
            
            # Use the Planner to create a Task
            task = await self.agent.create_task(goal)
            
            if not task.steps:
                # No steps needed? Just a chat.
                response = await self.agent.chat(
                    f"{profile_ctx}{user_message}",
                    image_path=str(image_path) if 'image_path' in locals() else None,
                )
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

        # Start gateway pulse (only when running inside the Gateway)
        if self.gateway_mode and not self._gateway_pulse_task:
            self._gateway_pulse_task = asyncio.create_task(self._gateway_pulse_loop())
        if self.gateway_mode and not self._moltbook_task:
            self._moltbook_task = asyncio.create_task(self._moltbook_heartbeat_loop())
        
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
