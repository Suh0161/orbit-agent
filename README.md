# Orbit Agent (v0.9.4)

**Autonomous Desktop Intelligence.**

Orbit is a local-first AI agent designed to operate your computer with the same dexterity and reasoning as a human user. It integrates Vision (GPT-5.1), Planning, and precise Tooling to execute complex workflows—from coding entire applications to rapid web research—while keeping you in the loop (Telegram-first + CLI).

> "A Junior Engineer with Senior Guardrails."

---

## Core Capabilities

### 1. The Architect Protocol (Coding)
Orbit does not just write code; it follows a strict software engineering lifecycle:
1.  **Isolate:** Creates a clean project folder in `~/orbit_workspace/`.
2.  **Design:** Drafts `task.md` (Plan) and `architecture.md` (System Design).
3.  **Consult:** **Pauses** execution to request your approval ("Proceed").
4.  **Implement:** Writes the actual code file-by-file only after the design is approved.

### 2. Universal Computer Control (New!)
Unlike simple chatbots, Orbit has hands.
-   **Desktop Control:** Uses computer vision and OS hooks to click, type, drag, and scroll anywhere on your screen.
-   **Visual Awareness:** Can verify if an action succeeded by "looking" at the screen (e.g., hovering over a button and verifying the tooltip).
-   **Feedback Loop:** "Click -> Look -> Correction." If it misses a button, it detects the failure and retries.

### 3. Digital Nomad Skills
-   **Web Surfing:** Uses a headless browser (Playwright) to search DuckDuckGo, visit pages, and extract content without CAPTCHA blocks.
-   **App Control:** "Open Spotify", "Close Calculator". Manages local windows natively.

### 4. Active Intelligence
-   **Self-Healing:** If a tool fails (e.g. "File not found"), Orbit detects the error, re-plans, and executes recovery steps autonomously.
-   **Muscle Memory:** Learns from successful tasks. If you ask it to do something it has done before, it retrieves the optimized routine, skipping the planning phase.
-   **Privacy First:** All configuration and keys are stored locally in `.env` (never committed).

---

## Installation

**Prerequisites:** Python 3.11+

### One-liner install (like OpenClaw)

**Windows (PowerShell):**

```powershell
# Safer: download first, inspect, then run
iwr -useb https://raw.githubusercontent.com/Start-Orbit/orbit/main/install.ps1 -OutFile .\install-orbit.ps1
.\install-orbit.ps1

# Optional: custom ASCII banner
# .\install-orbit.ps1 -BannerPath .\my-banner.txt
# Optional: disable banner
# .\install-orbit.ps1 -NoBanner

# Optional: skip the confirmation prompt
# .\install-orbit.ps1 -Yes
```

**macOS/Linux:**

```bash
# Safer: download first, inspect, then run
curl -fsSL https://raw.githubusercontent.com/Start-Orbit/orbit/main/install.sh -o install-orbit.sh
chmod +x install-orbit.sh
./install-orbit.sh

# Optional: custom ASCII banner
# ORBIT_BANNER_PATH=./my-banner.txt ./install-orbit.sh
# Optional: disable banner
# ORBIT_NO_BANNER=1 ./install-orbit.sh

# Optional: skip the confirmation prompt
# ORBIT_YES=1 ./install-orbit.sh
```

```bash
# 1. Clone
git clone https://github.com/Start-Orbit/orbit.git
cd orbit

# 2. Install Dependencies
pip install -r requirements.txt
playwright install

# 3. Setup Secrets
# Create a .env file based on the example
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY
```

---

## Usage

### 1. The Ghost Shell (CLI)
Low-latency command line interface.

```bash
orbit chat
```

### 2. Orbit Uplink (Mobile Access)
Control your desktop from your phone via Telegram.

```bash
# 1. Configure Telegram Bot
# - Message @BotFather on Telegram to create a bot
# - Get your token

# 2. Update Configuration
# Add to your .env file:
# TELEGRAM_BOT_TOKEN=your_token_here
# ORBIT_UPLINK_USERS=your_telegram_id

# 3. Run onboarding (writes .env + orbit_config.yaml)
orbit onboard
# Optional: install autostart (Windows Scheduled Task)
# orbit onboard --install-daemon

# 4. Run the Gateway (owns channels + health/status)
orbit gateway

# 5. (Legacy) Run Telegram-only uplink
# orbit uplink
```

Then message your bot on Telegram:
-   **"What's on my screen?"** - Takes screenshot, analyzes, responds
-   **"Open VS Code"** - Controls your desktop remotely
-   **"/screenshot"** - Sends you your current screen
-   **"/status"** - Shows workspace status
-   **"what model ru it"** - Reports the real configured model (deterministic, not LLM guessing)

**Game control tip (DirectX/fullscreen):**
- Install `pydirectinput` (included in `requirements.txt`)
- Set `ORBIT_DESKTOP_INPUT_BACKEND=direct` in your `.env`

**Flights tip (actually reads the tabs):**
- Example: `find me cheapest flight from KL to japan 11 feb to 20 feb 2 people`
- Uplink opens Google Flights + Skyscanner in separate tabs, reads them, and summarizes the cheapest option it can see.

#### Proactive mode (reminders / daily jobs / heartbeat)
Uplink can run scheduled jobs and check-ins (Telegram-only for now):
- `/remind <minutes> <goal...>` — one-off reminder that runs a goal later
- `/daily HH:MM <goal...>` — run a goal every day at a time
- `/jobs` — list your scheduled jobs
- `/cancel <job_id>` — cancel a job
- `/heartbeat <minutes>` / `/heartbeat off` — periodic check-in messages

Jobs are persisted to `data/uplink/jobs.json`.

#### Always-on (Windows autostart)
You can install a Windows Scheduled Task to start Uplink on login:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\scripts\install_uplink_autostart.ps1
```

---

## Configuration

Orbit looks for `.env` for secrets and `orbit_config.yaml` for preferences.

```yaml
# orbit_config.yaml

# Set to 'false' to allow fully autonomous execution (no "Allow?" popups)
safe_mode: false 

model:
  provider: "openai"          # Options: openai, deepseek, xai (grok), local (ollama)
  model_name: "gpt-5.1"       # Used for planning + vision (and SoM Vision)
  api_key_env_var: "OPENAI_API_KEY"
  # base_url: "https://api.x.ai/v1"        # Uncomment for Grok
  # base_url: "http://localhost:11434/v1" # Uncomment for Ollama/Local
```

---

## Cost & Efficiency

-   **High Efficiency:** Orbit uses a "Planning" architecture. It pays for thinking once, then executes 10-20 steps (coding, file ops) for free using local Python code.
-   **Heavy Usage:** **Vision** features (Screen Analysis) send screenshots to GPT-5.1. This is token-heavy.
    -   *Recommendation:* Use the CLI/Chat for coding tasks. For visual debugging, use Telegram Uplink (`/screenshot`) or let the agent capture screenshots when needed.

---

## System Architecture

![Orbit Architecture](image/architecture.png)

-   **Core:** `orbit_agent.core` (Agent Loop, Planner, Router)
-   **Skills:** `orbit_agent.skills` (Vision, Desktop, Browser, file I/O)
 
-   **Memory:** Ephemeral (RAM) + Persistent Routines (`jsonl`).
-   **Run traces (debugging):** `memory/runs/<task_id>.jsonl` (planning + step execution events).

---

## What's new in v0.9.4 (Reliability + Uplink)

- **Game input reliability**: `computer_control` supports `backend=auto|pyautogui|direct` (DirectX/fullscreen games work best with `pydirectinput`).
- **Uplink flight searches actually work end-to-end**: opens Google Flights + Skyscanner, reads tabs, summarizes cheapest option it can see.
- **No more "Done" hallucinations**: state-changing actions are tracked; if there's no verification, Uplink will say `Done (not verified). ⚠️`.
- **Recovery replanning**: when a step fails, Uplink can request a recovery plan (bounded by `ORBIT_UPLINK_REPLAN_MAX`).
- **Windows robustness**: all task + memory JSON persistence is now UTF-8, avoiding `charmap codec can't encode …` crashes.
- **Deterministic model reporting**: asking “what model…” returns the configured model, not an LLM guess.

---

## License
Apache 2.0 - See `LICENSE` for details.

*Built by NVDY.*
