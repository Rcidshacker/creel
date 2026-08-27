"""Thin CLI adapter — zero logic below the call into core.orchestrator.

Every vendor import is lazy (engines/*.py import Scrapling/httpx inside
fetch(), not at module load), so this module itself never pulls in
scrapegraphai's 16-second LangChain import (Phase 0 spike) — `creel fetch
--help` must stay instant regardless of which extractors get wired in later
phases.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Optional

from creel.core.env import load_dotenv
from creel.core.events import AttemptFinished, AttemptStarted, EventBus
from creel.core.orchestrator import CooldownActive, Orchestrator


def _print_event(event) -> None:
    if isinstance(event, AttemptStarted):
        print(f"  -> {event.engine} ...", file=sys.stderr)
    elif isinstance(event, AttemptFinished):
        outcome = "ok" if event.status == "ok" else f"failed ({event.failure_class})"
        print(f"  <- {event.engine} {outcome} in {event.duration_ms}ms", file=sys.stderr)


async def _run_fetch(
    url: str, cost_mode: str, show_trace: bool, orchestrator: Optional[Orchestrator] = None
) -> int:
    orch = orchestrator
    if orch is None:
        events = EventBus()
        if show_trace:
            events.subscribe(_print_event)
        orch = Orchestrator(events=events)
    try:
        result = await orch.fetch(url, cost_mode=cost_mode)
    except CooldownActive as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"status: {result.status}")
    print(f"engine_path: {' -> '.join(result.engine_path) if result.engine_path else '(none)'}")
    print(f"from_cache: {result.from_cache}")
    if result.status == "ok" and result.html:
        print(f"body preview:\n{result.html[:500]}")
    return 0 if result.status == "ok" else 2


def main(argv: Optional[list[str]] = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="creel")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_p = sub.add_parser("fetch", help="Fetch a URL through the acquisition ladder")
    fetch_p.add_argument("url")
    fetch_p.add_argument("--cost-mode", choices=["frugal", "reliable"], default="frugal")
    fetch_p.add_argument("--show-trace", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "fetch":
        return asyncio.run(_run_fetch(args.url, args.cost_mode, args.show_trace))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
