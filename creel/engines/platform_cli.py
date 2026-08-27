"""Platform-specific engine for URL-shaped requests that hit AUTH_REQUIRED
during normal acquisition (login-walled content on a KNOWN platform). This
is the LAST rung in the escalation matrix — tried only when the local
ladder and remote egress have both failed, and only when Agent-Reach
reports the platform's channel as usable.

Agent-Reach containment, per the design's risk assessment — and
empirically reinforced twice during this investigation, where printing
Agent-Reach's own Chinese-language source/docstrings crashed on Windows'
cp1252 console encoding:
  - doctor() is called LAZILY (first real need), never at import or
    module-construction time, and cached — never re-probed per request.
  - every call into agent_reach is wrapped in try/except, degrading to
    "channel unavailable" rather than letting a crash reach the ladder.
  - availability predicate is `status in ("ok", "warn")`, NEVER
    `active_backend is not None` — several channels (twitter, xiaohongshu)
    deliberately never report an active_backend even when fully working,
    by Agent-Reach's own design (it avoids a live browser-cookie read on a
    bare availability check).

Windows subprocess hygiene, verified directly (not assumed): a scrubbed
child environment breaks basic network connectivity unless SYSTEMROOT (and
WINDIR) are preserved — a naive minimal env of just PATH+credentials
produced "error connecting to api.github.com" from `gh`, because Windows'
network stack (Winsock/TLS) needs SYSTEMROOT to resolve. CREATE_NO_WINDOW
suppresses the console flash per call.

Three channels are wired as concrete dispatchers: v2ex (a direct Python
data method — no subprocess), github (a subprocess CLI call via the
already-authenticated `gh`), and linkedin (a subprocess call to `mcporter`,
which drives the third-party `mcp-server-linkedin` MCP server -- Agent-Reach's
own LinkedInChannel carries no direct data method at all, unlike v2ex/github;
its "backend" is genuinely that external MCP server). All three are
independently verified usable via `agent-reach doctor`/`check()` on this
actual machine. Every other channel (reddit, facebook, instagram,
xiaohongshu, twitter) needs OpenCLI, cookies, or credentials this
environment does not have configured — wiring dispatchers for them now
would be untestable code, not a real capability.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from typing import Optional
from urllib.parse import urlsplit

from creel.core.models import ExecutionModel, FetchOutcome

NAME = "platform_cli"
TIER = 6
NEEDS_BROWSER = False
EXECUTION_MODEL = ExecutionModel.ASYNC

# The minimum a Windows child process needs to do real network I/O. Losing
# SYSTEMROOT/WINDIR breaks DNS/TLS entirely — verified directly, not assumed.
_CHILD_ENV_PASSTHROUGH = (
    "GH_TOKEN",
    "GH_CONFIG_DIR",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "COMSPEC",
)


class _DoctorCache:
    """Lazy, cached, contained. Never probed at import or construction —
    only the first time a platform channel is actually needed — and never
    re-probed afterward within this process."""

    def __init__(self) -> None:
        self._snapshot: Optional[dict] = None

    def snapshot(self) -> dict:
        if self._snapshot is None:
            self._snapshot = self._probe()
        return self._snapshot

    def reset(self) -> None:
        self._snapshot = None

    @staticmethod
    def _probe() -> dict:
        try:
            from agent_reach.config import Config
            from agent_reach.doctor import check_all

            return check_all(Config(read_only=True))
        except Exception:
            return {}

    def available(self, channel: str) -> bool:
        info = self.snapshot().get(channel)
        return bool(info) and info.get("status") in ("ok", "warn")


_doctor = _DoctorCache()


def available(channel: str) -> bool:
    return _doctor.available(channel)


def _child_env() -> dict:
    env = {"PATH": os.environ.get("PATH", "")}
    for key in _CHILD_ENV_PASSTHROUGH:
        if key in os.environ:
            env[key] = os.environ[key]
    return env


async def fetch(url: str, guard_config=None, **_ignored) -> FetchOutcome:
    start = time.monotonic()
    host = (urlsplit(url).hostname or "").lower()

    try:
        if "v2ex.com" in host:
            outcome = await asyncio.to_thread(_fetch_v2ex, url)
        elif "github.com" in host:
            outcome = await _fetch_github(url)
        elif "linkedin.com" in host:
            outcome = await _fetch_linkedin(url)
        else:
            outcome = None
    except Exception as e:
        return FetchOutcome(
            status=None,
            headers={},
            body=b"",
            final_url=url,
            signals=[f"exception:{type(e).__name__}"],
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )

    if outcome is None:
        return FetchOutcome(
            status=None,
            headers={},
            body=b"",
            final_url=url,
            signals=["exception:UnsupportedOrUnavailablePlatform"],
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )
    outcome.elapsed_ms = int((time.monotonic() - start) * 1000)
    return outcome


def _fetch_v2ex(url: str) -> Optional[FetchOutcome]:
    if not available("v2ex"):
        return None
    match = re.search(r"/t/(\d+)", urlsplit(url).path)
    if not match:
        return None

    from agent_reach.channels import get_channel

    channel = get_channel("v2ex")
    topic = channel.get_topic(int(match.group(1)))
    body = json.dumps(topic, ensure_ascii=False).encode("utf-8")
    return FetchOutcome(
        status=200, headers={"content-type": "application/json"}, body=body, final_url=topic.get("url", url)
    )


async def _fetch_github(url: str) -> Optional[FetchOutcome]:
    if not available("github"):
        return None
    parts = [p for p in urlsplit(url).path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]

    if len(parts) >= 4 and parts[2] == "issues" and parts[3].isdigit():
        args = ["issue", "view", parts[3], "--repo", f"{owner}/{repo}", "--json", "title,body,state,url"]
    elif len(parts) >= 4 and parts[2] == "pull" and parts[3].isdigit():
        args = ["pr", "view", parts[3], "--repo", f"{owner}/{repo}", "--json", "title,body,state,url"]
    else:
        args = ["repo", "view", f"{owner}/{repo}", "--json", "name,description,url"]

    return await _run_gh(args, url)


async def _run_gh(args: list[str], url: str) -> FetchOutcome:
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = await asyncio.create_subprocess_exec(
        "gh",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_child_env(),
        **kwargs,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        return FetchOutcome(
            status=None,
            headers={},
            body=stderr,
            final_url=url,
            signals=["exception:GhCliError"],
        )
    return FetchOutcome(status=200, headers={"content-type": "application/json"}, body=stdout, final_url=url)


async def _fetch_linkedin(url: str) -> Optional[FetchOutcome]:
    if not available("linkedin"):
        return None
    if "/in/" not in urlsplit(url).path:
        # Only person-profile URLs are wired (mcp-server-linkedin's
        # get_person_profile tool). Company/job pages use different tools
        # this dispatcher doesn't call yet.
        return None

    # get_person_profile's own docs: "A full profile URL is accepted too and
    # is reduced to the username" -- passing the URL directly avoids
    # reimplementing that parsing here.
    return await _run_mcporter(
        ["call", "linkedin.get_person_profile", f"linkedin_username={url}", "--output", "json"], url
    )


async def _run_mcporter(args: list[str], url: str) -> FetchOutcome:
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = await asyncio.create_subprocess_exec(
        "mcporter",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_child_env(),
        **kwargs,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        return FetchOutcome(
            status=None,
            headers={},
            body=stderr,
            final_url=url,
            signals=["exception:McporterError"],
        )
    return FetchOutcome(status=200, headers={"content-type": "application/json"}, body=stdout, final_url=url)
