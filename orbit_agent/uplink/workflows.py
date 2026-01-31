from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List


@dataclass
class WorkflowState:
    name: str
    slots: Dict[str, Any]
    created_at: str
    updated_at: str


class ConversationStore:
    """
    Tiny JSON-backed store for per-user workflow continuity.
    Keeps state in ./data/uplink/conversations.json by default.
    """

    def __init__(self, path: str = "data/uplink/conversations.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, WorkflowState]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            out: Dict[str, WorkflowState] = {}
            for user_id, rec in raw.items():
                if not rec:
                    continue
                out[str(user_id)] = WorkflowState(**rec)
            return out
        except Exception:
            return {}

    def save(self, states: Dict[str, WorkflowState]) -> None:
        payload = {str(uid): asdict(st) for uid, st in states.items()}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


class WorkflowResult:
    """
    Return shape for workflow handlers.
    - reply: message to send user (optional)
    - done: if True, clear active workflow state
    """

    def __init__(self, reply: Optional[str] = None, done: bool = False, pass_to_agent: bool = False):
        self.reply = reply
        self.done = done
        # If True, the caller should clear workflow state (if done) and continue
        # routing the same user message through normal agent handling.
        self.pass_to_agent = pass_to_agent


class BaseWorkflow:
    name: str = "base"

    def can_start(self, message: str) -> bool:
        raise NotImplementedError

    async def on_message(self, bot: Any, user_id: int, message: str, state: WorkflowState) -> WorkflowResult:
        raise NotImplementedError

    def new_state(self) -> WorkflowState:
        now = datetime.now().isoformat()
        return WorkflowState(name=self.name, slots={}, created_at=now, updated_at=now)

    def _touch(self, state: WorkflowState) -> None:
        state.updated_at = datetime.now().isoformat()

    async def _llm_extract_slots(
        self,
        bot: Any,
        message: str,
        current_slots: Dict[str, Any],
        schema_hint: str,
    ) -> Dict[str, Any]:
        """
        Universal slot filling using the configured planning model.
        Each workflow provides a schema_hint describing allowed keys and types.
        """
        from orbit_agent.models.base import Message

        client = bot.agent.planner.router.get_client("planning")
        prompt = (
            "You are a workflow slot extractor.\n"
            "Given the user's message and existing slots, output ONLY a single JSON object.\n"
            "Rules:\n"
            "- Only include keys from the schema.\n"
            "- If a value is unknown, omit the key (do not guess).\n"
            "- Do not include explanations or markdown.\n\n"
            f"SCHEMA:\n{schema_hint}\n\n"
            f"EXISTING_SLOTS_JSON:\n{json.dumps(current_slots or {}, ensure_ascii=False)}\n\n"
            f"USER_MESSAGE:\n{message}\n"
        )
        resp = await client.generate([Message(role="user", content=prompt)], temperature=0.0)
        extracted = _extract_json_object(resp.content) or {}
        if not isinstance(extracted, dict):
            return dict(current_slots or {})

        merged = dict(current_slots or {})
        for k, v in extracted.items():
            if v is None:
                continue
            merged[k] = v
        return merged

    async def _llm_pick_missing_question(
        self,
        bot: Any,
        missing_keys: List[str],
        schema_hint: str,
    ) -> str:
        """
        Ask a single clarifying question for the missing keys.
        """
        from orbit_agent.models.base import Message

        client = bot.agent.planner.router.get_client("planning")
        prompt = (
            "You are a concise assistant.\n"
            "Ask ONE short question to collect the missing info.\n"
            "No markdown fences.\n\n"
            f"SCHEMA:\n{schema_hint}\n\n"
            f"MISSING_KEYS: {missing_keys}\n"
        )
        resp = await client.generate([Message(role="user", content=prompt)], temperature=0.2)
        return (resp.content or "").strip() or "What info is missing?"


def _compact_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """
    Robustly extracts the first JSON object {...} from a model response.
    Handles markdown fences and leading/trailing prose.
    """
    if not text:
        return None

    s = text.strip()
    # Remove ```json fences if present
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)

    start = s.find("{")
    if start < 0:
        return None

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
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    cand = s[start : i + 1]
                    try:
                        obj = json.loads(cand)
                        return obj if isinstance(obj, dict) else None
                    except Exception:
                        return None
    return None


def _looks_like_iata(s: Optional[str]) -> bool:
    if not s:
        return False
    return bool(re.fullmatch(r"[A-Za-z]{3}", s.strip()))


def _iso_to_yymmdd(iso_date: str) -> Optional[str]:
    # Accept YYYY-MM-DD; output YYMMDD.
    try:
        dt = datetime.strptime(iso_date.strip(), "%Y-%m-%d")
        return dt.strftime("%y%m%d")
    except Exception:
        return None


class FlightSearchWorkflow(BaseWorkflow):
    name = "flight_search"

    def can_start(self, message: str) -> bool:
        m = _compact_spaces(message).lower()
        # Cheap heuristic; exact routing is handled by the registry LLM matcher.
        return ("flight" in m or "flights" in m or "ticket" in m or "tickets" in m)

    async def on_message(self, bot: Any, user_id: int, message: str, state: WorkflowState) -> WorkflowResult:
        self._touch(state)
        schema = (
            "{\n"
            '  "origin": "string (city/airport name) optional",\n'
            '  "destination": "string (city/airport name) optional",\n'
            '  "origin_iata": "3-letter IATA optional",\n'
            '  "destination_iata": "3-letter IATA optional",\n'
            '  "depart_date": "YYYY-MM-DD optional",\n'
            '  "return_date": "YYYY-MM-DD optional",\n'
            '  "passengers": "integer optional"\n'
            "}\n"
        )

        state.slots = await self._llm_extract_slots(bot, message, dict(state.slots or {}), schema_hint=schema)

        # If user has a saved profile, use it as a *default* (only when missing).
        try:
            p = bot.get_profile(user_id)
            if p and p.default_airport and not state.slots.get("origin") and not state.slots.get("origin_iata"):
                state.slots["origin_iata"] = p.default_airport
            if p and p.default_location and not state.slots.get("origin") and not state.slots.get("origin_iata"):
                state.slots["origin"] = p.default_location
        except Exception:
            pass

        missing: List[str] = []
        if not state.slots.get("origin") and not state.slots.get("origin_iata"):
            missing.append("origin")
        if not state.slots.get("destination") and not state.slots.get("destination_iata"):
            missing.append("destination")
        if not state.slots.get("depart_date"):
            missing.append("depart_date")

        if missing:
            q = await self._llm_pick_missing_question(bot, missing_keys=missing, schema_hint=schema)
            return WorkflowResult(reply=q, done=False)

        origin = (state.slots.get("origin") or state.slots.get("origin_iata") or "").strip()
        dest = (state.slots.get("destination") or state.slots.get("destination_iata") or "").strip()
        pax = int(state.slots.get("passengers") or 1)
        depart_iso = str(state.slots.get("depart_date") or "").strip()
        return_iso = str(state.slots.get("return_date") or "").strip()

        out_date = _iso_to_yymmdd(depart_iso)
        in_date = _iso_to_yymmdd(return_iso) if return_iso else None
        if not out_date:
            return WorkflowResult(reply="What is your departure date? (example: `2026-02-11`)", done=False)

        browser = bot.agent.skills.get_skill("browser_control")

        # Deterministic URLs: separate sources, separate tabs.
        import urllib.parse

        # Google Flights is the universal fallback (works with cities/airports).
        if return_iso:
            gq = f"Flights from {origin} to {dest} {depart_iso} to {return_iso} round trip {pax} adults"
        else:
            gq = f"Flights from {origin} to {dest} {depart_iso} one way {pax} adults"
        google_url = "https://www.google.com/travel/flights?q=" + urllib.parse.quote(gq)

        # Navigate + read.
        nav0 = await browser.execute(browser.input_schema(action="navigate", url=google_url, tab_index=0))
        if not getattr(nav0, "success", False):
            return WorkflowResult(reply=f"❌ Couldn't open Google Flights: {getattr(nav0,'error','')}", done=True)

        try:
            origin_iata = str(state.slots.get("origin_iata") or "").strip()
            dest_iata = str(state.slots.get("destination_iata") or "").strip()
            if _looks_like_iata(origin_iata) and _looks_like_iata(dest_iata):
                if in_date:
                    sky_url = f"https://www.skyscanner.com/transport/flights/{origin_iata.lower()}/{dest_iata.lower()}/{out_date}/{in_date}/?adultsv2={pax}&cabinclass=economy"
                else:
                    sky_url = f"https://www.skyscanner.com/transport/flights/{origin_iata.lower()}/{dest_iata.lower()}/{out_date}/?adultsv2={pax}&cabinclass=economy"
                await browser.execute(browser.input_schema(action="new_tab", url=sky_url))
        except Exception:
            pass

        all_page_content: List[str] = []
        for tab_idx in range(5):
            try:
                read_out = await browser.execute(browser.input_schema(action="read", tab_index=tab_idx))
                if getattr(read_out, "success", False) and getattr(read_out, "data", None):
                    all_page_content.append(f"[Tab {tab_idx}]\n{read_out.data[:2500]}")
            except Exception:
                break

        if not all_page_content:
            return WorkflowResult(
                reply="❌ I opened the pages but couldn't read results. Try `/screenshot` or run the search again.",
                done=True,
            )

        from orbit_agent.models.base import Message

        client = bot.agent.planner.router.get_client("planning")
        combined = "\n\n---\n\n".join(all_page_content)[:7000]
        prompt = (
            f"Task: find the cheapest flight.\n"
            f"Route: {origin} -> {dest}\n"
            f"Dates: depart={depart_iso} return={return_iso or 'one-way'}\n"
            f"Passengers: {pax}\n\n"
            "From the content, extract the cheapest price you can see and which site/tab it came from. "
            "If no numeric price is visible, say so and what info is missing.\n\n"
            "CONTENT:\n"
            + combined
        )
        resp = await client.generate([Message(role="user", content=prompt)], temperature=0.2)
        return WorkflowResult(reply=resp.content, done=True)


class AppLaunchWorkflow(BaseWorkflow):
    name = "app_launch"

    def can_start(self, message: str) -> bool:
        m = _compact_spaces(message).lower()
        return bool(re.search(r"\b(open|launch|start)\b", m))

    async def on_message(self, bot: Any, user_id: int, message: str, state: WorkflowState) -> WorkflowResult:
        self._touch(state)
        schema = '{ "app_name": "string (application name)" }'
        state.slots = await self._llm_extract_slots(bot, message, dict(state.slots or {}), schema_hint=schema)
        app = (state.slots.get("app_name") or "").strip()
        if not app:
            return WorkflowResult(reply="Which app should I open?", done=False)

        app_skill = bot.agent.skills.get_skill("app_control")
        desktop = bot.agent.skills.get_skill("computer_control")
        vision = bot.agent.skills.get_skill("vision_analyze")
        from orbit_agent.skills.desktop import DesktopInput

        # 1) Try app_control open (with its focus retry)
        out = await app_skill.execute(app_skill.input_schema(action="open", app_name=app))
        if not getattr(out, "success", False):
            # We'll still attempt a start-menu fallback below.
            pass

        # 2) Bring to front/maximize (win+up generally works)
        try:
            await desktop.execute(DesktopInput(action="press", keys=["win", "up"], backend="pyautogui"))
        except Exception:
            pass

        # 3) Verify (best-effort): does the screen look like the requested app is visible?
        # If verification fails, fallback to start menu search.
        screenshots_dir = Path("screenshots")
        screenshots_dir.mkdir(exist_ok=True)
        verify_path = str(screenshots_dir / f"uplink_verify_app_{user_id}.png")

        async def _verify() -> bool:
            ss = await desktop.execute(DesktopInput(action="screenshot", save_path=verify_path))
            if not getattr(ss, "success", False):
                return False
            q = f"Is the application '{app}' visible and in the foreground? Look for title text or distinctive UI. Answer YES or NO."
            v = await vision.execute(vision.input_schema(image_path=verify_path, query=q, expect="yes"))
            return bool(getattr(v, "success", False))

        if await _verify():
            return WorkflowResult(reply=f"Opened **{app}**. ✅", done=True)

        # 4) Fallback: start menu search typing
        try:
            await desktop.execute(DesktopInput(action="press", keys=["win"], backend="pyautogui"))
            await desktop.execute(DesktopInput(action="wait", duration=0.4))
            await desktop.execute(DesktopInput(action="type", text=app, backend="pyautogui"))
            await desktop.execute(DesktopInput(action="wait", duration=0.2))
            await desktop.execute(DesktopInput(action="press", keys=["enter"], backend="auto"))
            await desktop.execute(DesktopInput(action="wait", duration=1.2))
            await desktop.execute(DesktopInput(action="press", keys=["win", "up"], backend="pyautogui"))
        except Exception:
            pass

        if await _verify():
            return WorkflowResult(reply=f"Opened **{app}** (fallback). ✅", done=True)

        return WorkflowResult(
            reply=(
                f"I tried opening **{app}**, but I couldn't verify it's on-screen. ⚠️\n"
                "If it's not open, send `/screenshot` and tell me what you see."
            ),
            done=True,
        )


class DiscordWorkflow(BaseWorkflow):
    name = "discord"

    def can_start(self, message: str) -> bool:
        return "discord" in _compact_spaces(message).lower()

    async def on_message(self, bot: Any, user_id: int, message: str, state: WorkflowState) -> WorkflowResult:
        self._touch(state)
        schema = (
            "{\n"
            '  "channel": "string optional (channel name, without #)",\n'
            '  "go_to_channel": "boolean optional",\n'
            '  "join_voice": "boolean optional"\n'
            "}\n"
        )
        state.slots = await self._llm_extract_slots(bot, message, dict(state.slots or {}), schema_hint=schema)
        channel = (state.slots.get("channel") or "").strip()
        go_to_channel = bool(state.slots.get("go_to_channel") or ("#" in message))
        join_voice = bool(state.slots.get("join_voice"))

        if go_to_channel and not channel:
            return WorkflowResult(reply="Which Discord channel? (example: `#general`)", done=False)

        app_skill = bot.agent.skills.get_skill("app_control")
        desktop = bot.agent.skills.get_skill("computer_control")
        visual = bot.agent.skills.get_skill("visual_interact")
        from orbit_agent.skills.desktop import DesktopInput

        # Open Discord
        await app_skill.execute(app_skill.input_schema(action="open", app_name="discord"))
        try:
            await desktop.execute(DesktopInput(action="press", keys=["win", "up"], backend="pyautogui"))
        except Exception:
            pass
        await desktop.execute(DesktopInput(action="wait", duration=1.2))

        # Go to channel via Quick Switcher (Ctrl+K)
        if channel and go_to_channel:
            await desktop.execute(DesktopInput(action="press", keys=["ctrl", "k"], backend="auto"))
            await desktop.execute(DesktopInput(action="wait", duration=0.3))
            await desktop.execute(DesktopInput(action="type", text=channel, backend="pyautogui"))
            await desktop.execute(DesktopInput(action="wait", duration=0.2))
            await desktop.execute(DesktopInput(action="press", keys=["enter"], backend="auto"))
            await desktop.execute(DesktopInput(action="wait", duration=1.0))

        if join_voice:
            # Try clicking a "Join Voice" style control.
            out = await visual.execute(visual.input_schema(description="the 'Join Voice' button", action="click"))
            await desktop.execute(DesktopInput(action="wait", duration=1.0))
            # Verify voice state using existing helper if available.
            verified = await bot._verify_discord_voice_state(user_id=user_id, expect_connected=True)
            if verified is False:
                return WorkflowResult(
                    reply="I attempted to join voice, but I couldn't verify it on-screen. ⚠️ Try again or send `/screenshot`.",
                    done=True,
                )

        return WorkflowResult(reply="Done. ✅", done=True)


class OnboardingWorkflow(BaseWorkflow):
    """
    Interactive persona/profile setup.
    Stores a per-user profile and then exits.
    """

    name = "onboarding"

    def can_start(self, message: str) -> bool:
        m = _compact_spaces(message).lower()
        return any(k in m for k in ["onboard", "onboarding", "set up profile", "setup profile", "set up persona", "setup persona"])

    async def on_message(self, bot: Any, user_id: int, message: str, state: WorkflowState) -> WorkflowResult:
        # If the user left onboarding hanging for a while, don't keep hijacking chat.
        ttl_s = int(os.environ.get("ORBIT_ONBOARDING_TTL_SECONDS", "900") or "900")
        prev_updated = (state.updated_at or "").strip()
        if ttl_s > 0 and prev_updated:
            try:
                prev_dt = datetime.fromisoformat(prev_updated)
                age_s = (datetime.now() - prev_dt).total_seconds()
                if age_s > ttl_s:
                    return WorkflowResult(done=True, pass_to_agent=True)
            except Exception:
                pass

        self._touch(state)
        m = _compact_spaces(message).strip()
        ml = m.lower()
        expected = str((state.slots or {}).get("_expected") or "").strip()

        # If the user is clearly not trying to onboard right now, don't hijack chat.
        if re.search(r"\b(who\s*(are|r)\s*(u|you)|who\s*ru|who\s*r\s*u|what\s*(are|r)\s*you|what\s*is\s*orbit|who\s+is\s+orbit)\b", ml):
            return WorkflowResult(
                reply=(
                    "I’m Orbit — your local-first assistant running on your machine.\n\n"
                    "I was in onboarding (saving your profile). I’ll stop that now so you can just talk to me.\n"
                    "If you want to continue onboarding later, type `onboard` or use `/onboard`."
                ),
                done=True,
            )

        if ml in {"cancel", "stop", "quit", "exit", "later", "never mind", "nevermind", "stop asking", "skip onboarding"}:
            return WorkflowResult(reply="Ok — exiting onboarding. You can type `onboard` anytime to resume. ✅", done=True)

        def _looks_offtopic(text_lc: str) -> bool:
            # Greetings / chit-chat / random messages should not trap the user in onboarding.
            if re.fullmatch(r"(hi|hello|hey|yo|sup|wassup|wsp|bruh|bro|test|ping)\b.*", text_lc):
                return True
            if "?" in text_lc and not expected:
                return True
            # Task-y verbs (user probably wants the agent to do something now).
            if re.search(r"\b(open|launch|start|run|click|press|type|search|find|book|buy|install|fix|help|show|screenshot)\b", text_lc):
                return True
            # If it looks like another workflow intent, bail out of onboarding.
            if re.search(r"\b(flight|flights|ticket|tickets)\b", text_lc):
                return True
            if re.search(r"\b(discord|channel|voice)\b", text_lc):
                return True
            return False

        if ml not in {"skip"} and _looks_offtopic(ml):
            # Cancel onboarding and let the normal agent handle this message.
            return WorkflowResult(done=True, pass_to_agent=True)

        # Allow "skip" to move past optional questions.
        if ml == "skip":
            state.slots["_last_skip"] = True
        else:
            state.slots.pop("_last_skip", None)

        schema = (
            "{\n"
            '  "preferred_name": "string optional",\n'
            '  "timezone": "string optional (IANA TZ like Asia/Kuala_Lumpur, or a city)",\n'
            '  "persona": "string optional (how the assistant should behave/talk)",\n'
            '  "default_location": "string optional (city/country)",\n'
            '  "default_airport": "string optional (3-letter IATA like KUL)",\n'
            '  "notes": "string optional (anything else to remember)"\n'
            "}\n"
        )
        # Only extract when user isn't explicitly skipping.
        if ml != "skip":
            state.slots = await self._llm_extract_slots(bot, message, dict(state.slots or {}), schema_hint=schema)

        # Deterministic question order (so it feels like real onboarding).
        if not state.slots.get("preferred_name") and not state.slots.get("_asked_name"):
            state.slots["_asked_name"] = True
            state.slots["_expected"] = "preferred_name"
            return WorkflowResult(reply="What should I call you? (or reply `skip`)", done=False)

        if not state.slots.get("timezone") and not state.slots.get("_asked_timezone"):
            state.slots["_asked_timezone"] = True
            state.slots["_expected"] = "timezone"
            return WorkflowResult(reply="What’s your timezone? (example: `Asia/Kuala_Lumpur` or `KL`) (or `skip`)", done=False)

        if not state.slots.get("persona") and not state.slots.get("_asked_persona"):
            state.slots["_asked_persona"] = True
            state.slots["_expected"] = "persona"
            return WorkflowResult(
                reply=(
                    "How should I behave?\n"
                    "Example: `direct + concise, no emojis, verify actions when possible` (or `skip`)"
                ),
                done=False,
            )

        if not state.slots.get("default_location") and not state.slots.get("_asked_location"):
            state.slots["_asked_location"] = True
            state.slots["_expected"] = "default_location"
            return WorkflowResult(reply="Where are you usually based? (city/country) (or `skip`)", done=False)

        if not state.slots.get("default_airport") and not state.slots.get("_asked_airport"):
            state.slots["_asked_airport"] = True
            state.slots["_expected"] = "default_airport"
            return WorkflowResult(reply="Default airport? (IATA like `KUL`) (or `skip`)", done=False)

        if not state.slots.get("notes") and not state.slots.get("_asked_notes"):
            state.slots["_asked_notes"] = True
            state.slots["_expected"] = "notes"
            return WorkflowResult(reply="Anything else I should remember about you or your preferences? (or `skip`)", done=False)

        # Save profile
        from orbit_agent.uplink.profile import UserProfile

        state.slots.pop("_expected", None)
        p = UserProfile(
            preferred_name=str(state.slots.get("preferred_name") or "").strip(),
            timezone=str(state.slots.get("timezone") or "").strip(),
            persona=str(state.slots.get("persona") or "").strip(),
            default_location=str(state.slots.get("default_location") or "").strip(),
            default_airport=str(state.slots.get("default_airport") or "").strip(),
            notes=str(state.slots.get("notes") or "").strip(),
        )
        p.touch()

        key = f"telegram:{user_id}"
        bot.profiles[key] = p
        bot.profile_store.save(bot.profiles)

        summary = []
        if p.preferred_name:
            summary.append(f"- Name: {p.preferred_name}")
        if p.timezone:
            summary.append(f"- Timezone: {p.timezone}")
        if p.default_location:
            summary.append(f"- Location: {p.default_location}")
        if p.default_airport:
            summary.append(f"- Default airport: {p.default_airport}")
        if p.persona:
            summary.append(f"- Persona: {p.persona}")
        if p.notes:
            summary.append(f"- Notes: {p.notes}")

        return WorkflowResult(
            reply="✅ Saved your profile.\n\n" + ("\n".join(summary) if summary else "(empty)"),
            done=True,
        )


class WorkflowRegistry:
    def __init__(self):
        self.workflows: Dict[str, BaseWorkflow] = {
            FlightSearchWorkflow.name: FlightSearchWorkflow(),
            AppLaunchWorkflow.name: AppLaunchWorkflow(),
            DiscordWorkflow.name: DiscordWorkflow(),
            OnboardingWorkflow.name: OnboardingWorkflow(),
        }

    async def match_start(self, bot: Any, message: str) -> Optional[BaseWorkflow]:
        """
        Universal workflow selection via LLM. Falls back to heuristics if model output fails.
        """
        from orbit_agent.models.base import Message

        names = list(self.workflows.keys())
        client = bot.agent.planner.router.get_client("planning")
        prompt = (
            "You are a router. Pick which workflow should handle the user's message.\n"
            "Return ONLY a JSON object like {\"workflow\": \"name\"} or {\"workflow\": null}.\n"
            f"Allowed workflow names: {names}\n\n"
            f"USER_MESSAGE:\n{message}\n"
        )
        try:
            resp = await client.generate([Message(role="user", content=prompt)], temperature=0.0)
            obj = _extract_json_object(resp.content) or {}
            chosen = obj.get("workflow", None)
            if isinstance(chosen, str) and chosen in self.workflows:
                return self.workflows[chosen]
            if chosen is None:
                return None
        except Exception:
            pass

        for wf in self.workflows.values():
            if wf.can_start(message):
                return wf
        return None

    def get(self, name: str) -> Optional[BaseWorkflow]:
        return self.workflows.get(name)

