from __future__ import annotations

from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import Optional, Dict, Tuple
import asyncio
import random
import string
import os

import yaml

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table


@dataclass
class OnboardChoices:
    provider: str
    model_name: str
    api_key_env_var: str
    base_url: Optional[str]
    api_key_value: Optional[str]
    setup_telegram: bool
    telegram_bot_token: Optional[str]
    telegram_users: Optional[str]


def _read_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    out: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _ask(prompt: str, default: Optional[str] = None) -> str:
    if default is None:
        return Prompt.ask(prompt).strip()
    return Prompt.ask(prompt, default=str(default)).strip()


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    return bool(Confirm.ask(prompt, default=default))


def _upsert_env(path: Path, key: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines(True)
    out = []
    found = False
    for line in lines:
        if line.lstrip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        k = line.split("=", 1)[0].strip()
        if k == key:
            out.append(f"{key}={value}\n")
            found = True
        else:
            out.append(line)
    if not found:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        out.append(f"{key}={value}\n")
    path.write_text("".join(out), encoding="utf-8")


def _write_orbit_config(path: Path, provider: str, model_name: str, api_key_env_var: str, base_url: Optional[str]) -> None:
    cfg = {
        "model": {
            "provider": provider,
            "model_name": model_name,
            "api_key_env_var": api_key_env_var,
        }
    }
    if base_url:
        cfg["model"]["base_url"] = base_url
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def _channel_status(env: Dict[str, str]) -> Tuple[bool, bool]:
    tg = bool(env.get("TELEGRAM_BOT_TOKEN")) and bool(env.get("ORBIT_UPLINK_USERS"))
    wa = False  # not implemented in this repo yet
    return tg, wa


def run_onboarding(cwd: Optional[Path] = None, install_daemon: bool = False) -> None:
    root = cwd or Path.cwd()
    env_path = root / ".env"
    cfg_path = root / "orbit_config.yaml"
    console = Console()

    env_existing = _read_env_file(env_path)
    tg_ok, wa_ok = _channel_status(env_existing)

    console.print("")
    console.print(Panel.fit(
        "[bold]Orbit onboarding[/bold]\n"
        "This sets up your model + keys + (optional) Telegram.\n\n"
        f"[dim]Writes:[/dim]\n- {env_path}\n- {cfg_path}",
        title="Welcome",
    ))

    console.print(Panel(
        "[bold]Security heads-up — quick read.[/bold]\n\n"
        "Orbit is a local-first agent. If you enable tools, it can read files and take actions.\n"
        "Treat prompts like untrusted input — a bad prompt can trick an agent.\n\n"
        "[bold]Good defaults:[/bold]\n"
        "- Keep safe_mode on until you’re confident.\n"
        "- Keep secrets out of places the agent can freely browse.\n"
        "- Use allowlists / approvals for anything destructive.\n",
        title="Security",
    ))

    if not Confirm.ask("I understand this is powerful and inherently risky. Continue?", default=False):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    # Channel status
    table = Table(title="Channel status", show_header=True, header_style="bold")
    table.add_column("Channel")
    table.add_column("Status")
    table.add_row("Telegram", "[green]configured[/green]" if tg_ok else "[yellow]not configured[/yellow]")
    table.add_row("WhatsApp", "[yellow]not configured[/yellow] (not implemented yet)")
    console.print(table)

    # Gateway identity / goals (consciousness vibes)
    try:
        from orbit_agent.gateway.identity import IdentityStore, GatewayIdentity

        console.print("")
        if Confirm.ask("Set Orbit identity + long-term goals now?", default=True):
            ident_store = IdentityStore()
            ident = ident_store.load()
            ident.persona = _ask(
                "Orbit persona (how I should behave)",
                ident.persona or "direct, concise, verify actions when possible",
            )
            goals_raw = _ask(
                "Top goals (comma-separated)",
                ", ".join([g for g in (ident.goals or []) if g]) or "ship reliably, reduce friction, be proactive",
            )
            ident.goals = [g.strip() for g in goals_raw.split(",") if g.strip()]
            ident.touch()
            ident_store.save(ident)
            console.print("[green]Saved gateway identity/goals.[/green]")
    except Exception:
        pass

    # Optional: Moltbook registration as "Orbit"
    try:
        console.print("")
        if Confirm.ask("Register Orbit on Moltbook now?", default=False):
            from orbit_agent.skills.moltbook import MoltbookSkill

            ident_desc = ""
            try:
                from orbit_agent.gateway.identity import IdentityStore

                ident = IdentityStore().load()
                if ident.persona:
                    ident_desc += f"Persona: {ident.persona}\n"
                if ident.goals:
                    ident_desc += "Goals: " + ", ".join([g for g in ident.goals if g]) + "\n"
            except Exception:
                ident_desc = ""

            def _rand_suffix(n: int = 4) -> str:
                alphabet = string.ascii_lowercase + string.digits
                return "".join(random.choice(alphabet) for _ in range(n))

            def _suggest_names(base: str) -> str:
                b = (base or "Orbit").strip() or "Orbit"
                # Keep suggestions short + readable.
                opts = [
                    f"{b}{_rand_suffix(4)}",
                    f"{b}HQ{_rand_suffix(3)}",
                    f"{b}OS{_rand_suffix(3)}",
                    f"{b}Agent{_rand_suffix(3)}",
                    f"{b}Local{_rand_suffix(3)}",
                ]
                return ", ".join(opts)

            mb = MoltbookSkill()
            # If we already registered before, show existing claim info first.
            try:
                saved = mb._store.load()  # internal, ok for onboarding UX
            except Exception:
                saved = {}
            force_new = str(os.environ.get("ORBIT_MOLTBOOK_FORCE_REGISTER", "0")).strip().lower() in {"1", "true", "yes", "on"}
            if isinstance(saved, dict) and saved.get("api_key") and saved.get("agent_name") and not force_new:
                console.print("")
                console.print(Panel.fit(
                    f"[bold]Already registered on Moltbook.[/bold]\n\n"
                    f"Name: {saved.get('agent_name')}\n\n"
                    f"Claim URL:\n{saved.get('claim_url')}\n\n"
                    f"Verification code:\n{saved.get('verification_code')}\n\n"
                    "If the claim link expired, you need to register a NEW agent name to get a fresh claim URL/code.\n"
                    "Tip: set ORBIT_MOLTBOOK_FORCE_REGISTER=1 to skip this prompt.",
                    title="Moltbook",
                ))
                if not Confirm.ask("Register a NEW Moltbook agent now? (new name + new claim link)", default=False):
                    # Skip the registration attempt.
                    raise RuntimeError("SKIP_MOLTBOOK_REGISTER")

            agent_name = _ask("Moltbook agent name", "Orbit")
            agent_desc = _ask(
                "Short description",
                "Local-first assistant running on the user's machine. " + (ident_desc.strip()[:160] if ident_desc else ""),
            )

            last_err = ""
            for attempt in range(1, 4):
                out = asyncio.run(
                    mb.execute(
                        mb.input_schema(
                            action="register",
                            name=agent_name,
                            description=agent_desc[:280],
                        )
                    )
                )
                if out.success and out.data:
                    agent = (out.data.get("agent") or {}) if isinstance(out.data, dict) else {}
                    claim_url = agent.get("claim_url")
                    verification_code = agent.get("verification_code")
                    console.print("")
                    console.print(Panel.fit(
                        f"[bold green]Registered![/bold green]\n\n"
                        f"Name: {agent_name}\n\n"
                        f"Claim URL:\n{claim_url}\n\n"
                        f"Verification code:\n{verification_code}\n\n"
                        "Verify ownership (tweet), then Orbit can act on Moltbook.",
                        title="Moltbook",
                    ))
                    last_err = ""
                    break

                last_err = str(out.error or "unknown error").strip()
                try:
                    if isinstance(out.data, dict):
                        sc = out.data.get("status_code")
                        resp = out.data.get("response")
                        hint = resp.get("hint") if isinstance(resp, dict) else None
                        if sc:
                            last_err = f"{last_err} [status {sc}]"
                        if hint and hint not in last_err:
                            last_err = f"{last_err}\nHint: {hint}"
                except Exception:
                    pass
                # Most common issue: name taken. Make this painless.
                if "taken" in last_err.lower() or "already" in last_err.lower():
                    console.print(f"[yellow]Moltbook name '{agent_name}' is taken.[/yellow]")
                    console.print(f"[dim]Try one of these:[/dim] {_suggest_names(agent_name)}")
                    agent_name = _ask("Pick a new Moltbook name", f"Orbit{_rand_suffix(4)}")
                    continue

                console.print(f"[yellow]Moltbook registration failed: {last_err}[/yellow]")
                break

            if last_err:
                console.print(f"[yellow]Moltbook registration not completed: {last_err}[/yellow]")
    except Exception as e:
        # SKIP_MOLTBOOK_REGISTER is a controlled early-exit from the Moltbook block.
        if str(e) == "SKIP_MOLTBOOK_REGISTER":
            pass
        else:
            # Never fail onboarding, but DO show the error so users aren't confused.
            console.print(f"[yellow]Moltbook step error: {e!r}[/yellow]")

    console.print("")
    console.print(Panel.fit(
        "Providers supported in this repo right now:\n"
        "- [bold]OpenAI[/bold]\n"
        "- [bold]OpenAI-compatible[/bold] (custom base_url; works for many gateways)\n\n"
        "[dim]Claude/Anthropic and WhatsApp can be added next, but aren’t wired in yet.[/dim]",
        title="Model/provider",
    ))
    choice = _ask("Provider [1=OpenAI, 2=OpenAI-compatible]", "1")

    provider = "openai"
    base_url: Optional[str] = None
    api_key_env_var = "OPENAI_API_KEY"

    if choice == "2":
        provider = "openai"
        base_url = _ask("Base URL (OpenAI-compatible)", "")
        api_key_env_var = _ask("API key env var name", "OPENAI_API_KEY")

    model_name = _ask("Model name", "gpt-5.1")

    api_key_value: Optional[str] = None
    if _ask_yes_no(f"Set {api_key_env_var} in .env now?", default=True):
        api_key_value = getpass(f"Enter {api_key_env_var} (hidden): ").strip()
        if not api_key_value:
            api_key_value = None

    console.print("")
    setup_telegram = _ask_yes_no("Configure Telegram now?", default=(not tg_ok))
    telegram_bot_token = None
    telegram_users = None
    if setup_telegram:
        console.print("[dim]Tip: create a bot with @BotFather, then paste the token here.[/dim]")
        telegram_bot_token = getpass("TELEGRAM_BOT_TOKEN (hidden): ").strip() or None
        telegram_users = _ask("ORBIT_UPLINK_USERS (comma-separated Telegram user IDs)", "")
        if not telegram_users:
            telegram_users = None

    # Write files
    if api_key_value:
        _upsert_env(env_path, api_key_env_var, api_key_value)
    if setup_telegram:
        if telegram_bot_token:
            _upsert_env(env_path, "TELEGRAM_BOT_TOKEN", telegram_bot_token)
        if telegram_users:
            _upsert_env(env_path, "ORBIT_UPLINK_USERS", telegram_users)

    # Always keep workflows enabled by default (users can disable later)
    _upsert_env(env_path, "ORBIT_UPLINK_WORKFLOWS", "1")

    _write_orbit_config(cfg_path, provider=provider, model_name=model_name, api_key_env_var=api_key_env_var, base_url=base_url)

    # Optional: install daemon/autostart for uplink
    if install_daemon and setup_telegram:
        try:
            import sys
            import subprocess

            if sys.platform == "win32":
                script = root / "scripts" / "install_uplink_autostart.ps1"
                if script.exists():
                    console.print("")
                    console.print("[bold]Installing Uplink autostart (Scheduled Task)…[/bold]")
                    subprocess.run(
                        [
                            "powershell",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(script),
                            "-ProjectDir",
                            str(root),
                            "-PythonExe",
                            sys.executable,
                        ],
                        check=False,
                    )
                else:
                    console.print("[yellow]Autostart script not found; skipping.[/yellow]")
            else:
                console.print("[yellow]Daemon install not implemented for this OS yet.[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Autostart install failed: {e}[/yellow]")

    console.print("")
    console.print(Panel.fit(
        "[bold green]Onboarding complete.[/bold green]\n\n"
        "Next:\n"
        "- Start Telegram uplink:  [bold]orbit uplink[/bold]\n"
        "- Start CLI chat:         [bold]orbit chat[/bold]\n"
        "- View profile:           [bold]/profile[/bold] (in Telegram)\n",
        title="Done",
    ))

