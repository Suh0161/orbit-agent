from __future__ import annotations

import json
import os
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Type, Dict, Any, Literal

import httpx
from pydantic import BaseModel, Field

from orbit_agent.skills.base import BaseSkill, SkillConfig


DEFAULT_API_BASE = "https://www.moltbook.com/api/v1"


class MoltbookInput(BaseModel):
    action: Literal[
        "register",
        "status",
        "me",
        "post",
        "comment",
        "feed",
        "search",
        # voting
        "upvote_post",
        "downvote_post",
        "upvote_comment",
        # DMs
        "dm_check",
        "dm_requests",
        "dm_request",
        "dm_approve",
        "dm_reject",
        "dm_conversations",
        "dm_read",
        "dm_send",
    ] = Field(..., description="Action to perform on Moltbook.")

    # Auth (optional if stored)
    api_key: Optional[str] = Field(default=None, description="Moltbook API key (moltbook_xxx). If omitted, uses stored credentials or MOLTBOOK_API_KEY.")

    # register
    name: Optional[str] = Field(default=None, description="Agent name (register).")
    description: Optional[str] = Field(default=None, description="Agent description (register).")

    # posts
    submolt: Optional[str] = Field(default=None, description="Community name (e.g. 'general').")
    title: Optional[str] = Field(default=None, description="Post title.")
    content: Optional[str] = Field(default=None, description="Post content (markdown).")
    url: Optional[str] = Field(default=None, description="Optional URL for link post.")

    # comments
    post_id: Optional[str] = Field(default=None, description="Post ID for comment/feed/comments.")
    parent_id: Optional[str] = Field(default=None, description="Parent comment ID for replies.")
    comment_id: Optional[str] = Field(default=None, description="Comment ID for comment voting.")

    # DMs
    conversation_id: Optional[str] = Field(default=None, description="DM conversation id.")
    to: Optional[str] = Field(default=None, description="DM request target agent name.")
    to_owner: Optional[str] = Field(default=None, description="DM request target owner handle (with or without @).")
    message: Optional[str] = Field(default=None, description="DM message content.")
    needs_human_input: Optional[bool] = Field(default=None, description="If true, flags DM message for human input escalation.")

    # feed/search
    sort: Optional[str] = Field(default="new", description="Sort order (hot/new/top/rising).")
    limit: int = Field(default=10, description="Max items to fetch.")
    q: Optional[str] = Field(default=None, description="Search query.")


class MoltbookOutput(BaseModel):
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class _CredsStore:
    def __init__(self, path: str = "data/moltbook/credentials.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(self, data: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class MoltbookSkill(BaseSkill):
    """
    Moltbook API skill.
    IMPORTANT: Always use https://www.moltbook.com (www required) or auth headers may be stripped on redirect.
    """

    def __init__(self):
        super().__init__()
        self._store = _CredsStore()

    @property
    def default_config(self) -> SkillConfig:
        return SkillConfig(
            name="moltbook",
            description="Interact with Moltbook (register agent, check status, post/comment/upvote via API).",
            permissions_required=["network"],
        )

    @property
    def input_schema(self) -> Type[BaseModel]:
        return MoltbookInput

    @property
    def output_schema(self) -> Type[BaseModel]:
        return MoltbookOutput

    def _resolve_api_key(self, provided: Optional[str]) -> Optional[str]:
        if provided:
            return provided.strip()
        env = os.environ.get("MOLTBOOK_API_KEY")
        if env:
            return env.strip()
        saved = self._store.load()
        key = saved.get("api_key")
        return str(key).strip() if key else None

    async def _request(
        self,
        method: str,
        path: str,
        api_key: Optional[str],
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
        timeout_s: float = 30.0,
    ) -> MoltbookOutput:
        url = DEFAULT_API_BASE + path
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, read=timeout_s), follow_redirects=False) as client:
                resp = await client.request(method, url, headers=headers, json=json_body, params=params)
            # Moltbook returns JSON with {"success": true/false, ...}
            try:
                payload = resp.json()
            except Exception:
                payload = {"success": False, "error": f"Non-JSON response (status {resp.status_code})"}

            if resp.status_code >= 400:
                err = payload.get("error") if isinstance(payload, dict) else None
                hint = payload.get("hint") if isinstance(payload, dict) else None
                msg = str(err or payload)
                if hint:
                    msg = f"{msg} (hint: {hint})"
                return MoltbookOutput(success=False, data={"status_code": resp.status_code, "response": payload}, error=msg)

            ok = bool(payload.get("success", True))
            if not ok:
                return MoltbookOutput(success=False, data=payload, error=str(payload.get("error") or payload.get("hint") or "request failed"))
            return MoltbookOutput(success=True, data=payload)
        except httpx.TimeoutException as e:
            # httpx timeouts often stringify to "", so include the exception type.
            return MoltbookOutput(success=False, error=f"Timeout contacting Moltbook ({type(e).__name__})")
        except httpx.HTTPError as e:
            return MoltbookOutput(success=False, error=f"HTTP error contacting Moltbook ({type(e).__name__}): {e!r}")
        except Exception as e:
            msg = str(e).strip()
            if not msg:
                msg = repr(e)
            return MoltbookOutput(success=False, error=msg)

    async def execute(self, inputs: MoltbookInput) -> MoltbookOutput:
        action = inputs.action

        if action == "register":
            if not inputs.name or not inputs.description:
                return MoltbookOutput(success=False, error="register requires name and description")
            # Register can be slow/flaky; use longer timeout + a couple retries.
            last: MoltbookOutput = MoltbookOutput(success=False, error="register failed")
            for i in range(3):
                out = await self._request(
                    "POST",
                    "/agents/register",
                    api_key=None,
                    json_body={"name": inputs.name, "description": inputs.description},
                    timeout_s=90.0,
                )
                if out.success:
                    last = out
                    break
                last = out
                # Only retry on timeouts / transient network errors.
                err = (out.error or "").lower()
                if "timeout" not in err:
                    break
                await asyncio.sleep(0.6 + (i * 0.8))
            out = last
            if out.success and out.data:
                agent = (out.data.get("agent") or {}) if isinstance(out.data, dict) else {}
                api_key = agent.get("api_key")
                if api_key:
                    self._store.save(
                        {
                            "api_key": api_key,
                            "agent_name": inputs.name,
                            "claim_url": agent.get("claim_url"),
                            "verification_code": agent.get("verification_code"),
                        }
                    )
            return out

        api_key = self._resolve_api_key(inputs.api_key)
        if not api_key:
            return MoltbookOutput(success=False, error="No Moltbook API key. Register first or set MOLTBOOK_API_KEY.")

        if action == "status":
            return await self._request("GET", "/agents/status", api_key=api_key)

        if action == "me":
            return await self._request("GET", "/agents/me", api_key=api_key)

        if action == "post":
            if not inputs.submolt or not inputs.title:
                return MoltbookOutput(success=False, error="post requires submolt and title")
            body: Dict[str, Any] = {"submolt": inputs.submolt, "title": inputs.title}
            if inputs.url:
                body["url"] = inputs.url
            else:
                body["content"] = inputs.content or ""
            return await self._request("POST", "/posts", api_key=api_key, json_body=body)

        if action == "comment":
            if not inputs.post_id or not inputs.content:
                return MoltbookOutput(success=False, error="comment requires post_id and content")
            body: Dict[str, Any] = {"content": inputs.content}
            if inputs.parent_id:
                body["parent_id"] = inputs.parent_id
            return await self._request("POST", f"/posts/{inputs.post_id}/comments", api_key=api_key, json_body=body)

        if action == "feed":
            params = {"sort": inputs.sort or "new", "limit": int(inputs.limit or 10)}
            if inputs.submolt:
                # convenience endpoint
                return await self._request("GET", f"/submolts/{inputs.submolt}/feed", api_key=api_key, params=params)
            return await self._request("GET", "/feed", api_key=api_key, params=params)

        if action == "search":
            if not inputs.q:
                return MoltbookOutput(success=False, error="search requires q")
            params = {"q": inputs.q, "limit": int(inputs.limit or 10)}
            return await self._request("GET", "/search", api_key=api_key, params=params)

        # Voting
        if action == "upvote_post":
            if not inputs.post_id:
                return MoltbookOutput(success=False, error="upvote_post requires post_id")
            return await self._request("POST", f"/posts/{inputs.post_id}/upvote", api_key=api_key)

        if action == "downvote_post":
            if not inputs.post_id:
                return MoltbookOutput(success=False, error="downvote_post requires post_id")
            return await self._request("POST", f"/posts/{inputs.post_id}/downvote", api_key=api_key)

        if action == "upvote_comment":
            if not inputs.comment_id:
                return MoltbookOutput(success=False, error="upvote_comment requires comment_id")
            return await self._request("POST", f"/comments/{inputs.comment_id}/upvote", api_key=api_key)

        # DMs
        if action == "dm_check":
            return await self._request("GET", "/agents/dm/check", api_key=api_key)

        if action == "dm_requests":
            return await self._request("GET", "/agents/dm/requests", api_key=api_key)

        if action == "dm_request":
            if not inputs.message:
                return MoltbookOutput(success=False, error="dm_request requires message")
            body: Dict[str, Any] = {"message": inputs.message}
            if inputs.to:
                body["to"] = inputs.to
            if inputs.to_owner:
                body["to_owner"] = inputs.to_owner
            if "to" not in body and "to_owner" not in body:
                return MoltbookOutput(success=False, error="dm_request requires to or to_owner")
            return await self._request("POST", "/agents/dm/request", api_key=api_key, json_body=body)

        if action == "dm_approve":
            if not inputs.conversation_id:
                return MoltbookOutput(success=False, error="dm_approve requires conversation_id")
            return await self._request("POST", f"/agents/dm/requests/{inputs.conversation_id}/approve", api_key=api_key)

        if action == "dm_reject":
            if not inputs.conversation_id:
                return MoltbookOutput(success=False, error="dm_reject requires conversation_id")
            return await self._request("POST", f"/agents/dm/requests/{inputs.conversation_id}/reject", api_key=api_key)

        if action == "dm_conversations":
            return await self._request("GET", "/agents/dm/conversations", api_key=api_key)

        if action == "dm_read":
            if not inputs.conversation_id:
                return MoltbookOutput(success=False, error="dm_read requires conversation_id")
            return await self._request("GET", f"/agents/dm/conversations/{inputs.conversation_id}", api_key=api_key)

        if action == "dm_send":
            if not inputs.conversation_id or not inputs.message:
                return MoltbookOutput(success=False, error="dm_send requires conversation_id and message")
            body = {"message": inputs.message}
            if inputs.needs_human_input is True:
                body["needs_human_input"] = True
            return await self._request("POST", f"/agents/dm/conversations/{inputs.conversation_id}/send", api_key=api_key, json_body=body)

        return MoltbookOutput(success=False, error=f"Unknown action: {action}")

