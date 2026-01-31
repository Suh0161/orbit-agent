"""
Orbit Gateway - Main Entry Point

Gateway = long-running daemon that owns:
- Channel connections (Telegram for now)
- Session/workflow/profile stores
- Health/status endpoint (local)
"""

from __future__ import annotations

import os
import sys
import time
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Ensure imports work when run as a module from repo
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@dataclass
class GatewayConfig:
    bind: str = "127.0.0.1"
    port: int = 18789
    token: Optional[str] = None  # bearer token for /status


def _force_utf8_console() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
        except Exception:
            pass


def main() -> None:
    _force_utf8_console()

    from dotenv import load_dotenv

    load_dotenv()

    _force_utf8_console()

    from orbit_agent.config.config import OrbitConfig
    from orbit_agent.uplink.telegram_bot import OrbitTelegramBot, UplinkConfig, TELEGRAM_AVAILABLE
    from orbit_agent.gateway.http_server import GatewayHttpServer, GatewayStatus
    from orbit_agent.core.agent import Agent

    def _sanitize_telegram_token(raw: Optional[str]) -> Optional[str]:
        if not raw:
            return None
        # Strip whitespace and remove non-printable ASCII (can sneak in from paste/clipboard).
        cleaned = "".join(ch for ch in str(raw).strip() if 32 <= ord(ch) < 127)
        # If the value got corrupted (e.g. repeated token), extract the first valid token-looking substring.
        m = re.search(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", cleaned)
        return m.group(0) if m else (cleaned or None)

    # Load Orbit config (model, workspace, safe_mode)
    config = OrbitConfig.load()

    # Gateway config from env
    bind = os.environ.get("ORBIT_GATEWAY_BIND", "127.0.0.1")
    port = int(os.environ.get("ORBIT_GATEWAY_PORT", "18789"))
    token = os.environ.get("ORBIT_GATEWAY_TOKEN") or None
    gw_cfg = GatewayConfig(bind=bind, port=port, token=token)

    # Telegram config from env (reuse uplink/main behavior)
    bot_token = _sanitize_telegram_token(os.environ.get("TELEGRAM_BOT_TOKEN"))
    allowed_users_str = os.environ.get("ORBIT_UPLINK_USERS", "")
    allowed_users = set()
    if allowed_users_str:
        try:
            allowed_users = {int(uid.strip()) for uid in allowed_users_str.split(",") if uid.strip()}
        except Exception:
            allowed_users = set()

    screenshots_env = os.environ.get("ORBIT_UPLINK_SCREENSHOTS", "")
    screenshot_on_task = str(screenshots_env).strip().lower() in {"1", "true", "yes", "on"}

    uplink_config = UplinkConfig(
        enabled=True,
        platform="telegram",
        bot_token=bot_token,
        allowed_users=allowed_users if allowed_users else None,
        require_auth=bool(allowed_users),
        screenshot_on_task=screenshot_on_task,
    )

    # Build agent once (owned by gateway)
    agent = Agent(config, interactive=False)

    # Start Telegram bot (channel adapter) if configured
    telegram_enabled = bool(TELEGRAM_AVAILABLE and bot_token)
    bot: Optional[OrbitTelegramBot] = None
    if telegram_enabled:
        bot = OrbitTelegramBot(config, uplink_config, agent=agent, gateway_mode=True)

    started_at = time.time()

    def get_status() -> GatewayStatus:
        active_tasks = 0
        jobs_enabled = 0
        if bot:
            active_tasks = len(getattr(bot, "active_tasks", {}) or {})
            jobs = getattr(bot, "jobs", {}) or {}
            jobs_enabled = sum(1 for j in jobs.values() if getattr(j, "enabled", False))
        return GatewayStatus(
            started_at=started_at,
            telegram_enabled=telegram_enabled,
            active_tasks=active_tasks,
            jobs_enabled=jobs_enabled,
        )

    # Start HTTP status server
    http = GatewayHttpServer(bind=gw_cfg.bind, port=gw_cfg.port, get_status=get_status, token=gw_cfg.token)
    http.start()

    print("")
    print("🛰️ ORBIT GATEWAY")
    print(f"- Health: http://{gw_cfg.bind}:{gw_cfg.port}/health")
    print(f"- Status: http://{gw_cfg.bind}:{gw_cfg.port}/status" + (" (Bearer token required)" if gw_cfg.token else ""))
    print(f"- Telegram: {'enabled' if telegram_enabled else 'not configured'}")
    print("")

    if not telegram_enabled:
        print("To enable Telegram:")
        print("- Set TELEGRAM_BOT_TOKEN in .env")
        print("- (Optional) set ORBIT_UPLINK_USERS=123,456")
        print("")
        print("Gateway will stay running for health/status, but no channels are connected.")

    # Run forever (Telegram bot is async; use its blocking runner)
    try:
        if bot:
            bot.run_blocking()
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Gateway] Shutting down...")
    finally:
        http.stop()


if __name__ == "__main__":
    main()

