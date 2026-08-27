"""CLI adapter tests. Verifies: (1) the module import stays cheap — no
scrapegraphai import hiding anywhere in the chain, (2) argument parsing,
(3) end-to-end output shape against the fixture server, via _run_fetch's
injectable-orchestrator seam (guard_config needs allow_private_hosts=True
for the fixture host, which the real CLI wouldn't set by default).
"""
import contextlib
import io
import subprocess
import sys
import time
import unittest

from creel.adapters.cli import _run_fetch, main
from creel.core.guard import GuardConfig
from creel.core.orchestrator import Orchestrator
from tests.fixtures.server import FixtureServer

_LOCAL = GuardConfig(allow_private_hosts=True)


class TestCliImportCost(unittest.TestCase):
    def test_importing_cli_module_is_fast(self):
        # A subprocess timing check, not just "already imported in this
        # process" — proves the import graph itself is cheap, matching the
        # Phase 0 spike's finding that scrapegraphai alone costs ~16s.
        start = time.monotonic()
        subprocess.run(
            [sys.executable, "-c", "import creel.adapters.cli"],
            check=True,
            cwd=".",
            timeout=5,
        )
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 3.0, "importing the CLI must not pull in scrapegraphai's LangChain tree")


class TestArgParsing(unittest.TestCase):
    def test_missing_command_exits_nonzero(self):
        with self.assertRaises(SystemExit):
            main([])

    def test_invalid_cost_mode_exits_nonzero(self):
        with self.assertRaises(SystemExit):
            main(["fetch", "https://x.com", "--cost-mode", "nonsense"])


class TestRunFetch(unittest.IsolatedAsyncioTestCase):
    async def test_ok_page_prints_status_and_engine_path(self):
        with FixtureServer() as server:
            orch = Orchestrator(guard_config=_LOCAL)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = await _run_fetch(server.url("/ok"), "frugal", False, orchestrator=orch)
        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("status: ok", output)
        self.assertIn("scrapling_http", output)

    async def test_404_returns_nonzero_exit_code(self):
        with FixtureServer() as server:
            orch = Orchestrator(guard_config=_LOCAL)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = await _run_fetch(server.url("/does-not-exist"), "frugal", False, orchestrator=orch)
        self.assertEqual(code, 2)
        self.assertIn("status: failed", buf.getvalue())

    async def test_cooldown_active_prints_error_and_returns_one(self):
        from creel.core.breaker import CircuitBreaker
        from creel.core.cooldown import CooldownRegistry

        with FixtureServer() as server:
            orch = Orchestrator(guard_config=_LOCAL, breaker=CircuitBreaker(), cooldowns=CooldownRegistry())
            await _run_fetch(server.url("/rate-limited"), "frugal", False, orchestrator=orch)  # triggers cooldown

            buf_out, buf_err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                code = await _run_fetch(server.url("/rate-limited"), "frugal", False, orchestrator=orch)
        self.assertEqual(code, 1)
        self.assertIn("cooling down", buf_err.getvalue())


if __name__ == "__main__":
    unittest.main()
