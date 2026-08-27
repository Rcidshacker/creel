"""platform_cli tests. The doctor probe and the v2ex channel object are
mocked for determinism (agent_reach's own status can drift on a real
machine over time). The github path is tested both via a mocked subprocess
(deterministic) and ONE real `gh` CLI invocation against a stable public
repo (skipped gracefully if `gh` isn't authenticated) — analogous to the
real-browser tests in test_engines.py: gh is a local tool already verified
working, not a remote service we're paying to call.
"""
import json
import shutil
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from creel.engines import platform_cli
from creel.engines.platform_cli import _DoctorCache, _child_env, fetch


class TestDoctorCache(unittest.TestCase):
    def test_probes_once_and_caches(self):
        cache = _DoctorCache()
        with patch.object(_DoctorCache, "_probe", return_value={"v2ex": {"status": "ok"}}) as mock_probe:
            self.assertTrue(cache.available("v2ex"))
            self.assertTrue(cache.available("v2ex"))
        mock_probe.assert_called_once()

    def test_status_ok_or_warn_is_available(self):
        cache = _DoctorCache()
        with patch.object(_DoctorCache, "_probe", return_value={"a": {"status": "ok"}, "b": {"status": "warn"}}):
            self.assertTrue(cache.available("a"))
            self.assertTrue(cache.available("b"))

    def test_status_off_or_error_is_unavailable(self):
        cache = _DoctorCache()
        with patch.object(_DoctorCache, "_probe", return_value={"a": {"status": "off"}, "b": {"status": "error"}}):
            self.assertFalse(cache.available("a"))
            self.assertFalse(cache.available("b"))

    def test_missing_channel_is_unavailable(self):
        cache = _DoctorCache()
        with patch.object(_DoctorCache, "_probe", return_value={}):
            self.assertFalse(cache.available("nonexistent"))

    def test_probe_exception_degrades_to_unavailable_not_a_crash(self):
        cache = _DoctorCache()
        with patch.object(_DoctorCache, "_probe", side_effect=RuntimeError("agent_reach broke")):
            # _probe raising would propagate through snapshot() -- but the
            # REAL _probe (not this test's mock) wraps everything in
            # try/except and returns {} on any failure. This test targets
            # that real behavior directly, not the mock.
            pass
        real_cache = _DoctorCache()
        with patch("agent_reach.doctor.check_all", side_effect=RuntimeError("boom")):
            self.assertFalse(real_cache.available("v2ex"))

    def test_reset_forces_reprobe(self):
        cache = _DoctorCache()
        with patch.object(_DoctorCache, "_probe", return_value={"v2ex": {"status": "ok"}}) as mock_probe:
            cache.available("v2ex")
            cache.reset()
            cache.available("v2ex")
        self.assertEqual(mock_probe.call_count, 2)


class TestChildEnv(unittest.TestCase):
    def test_includes_systemroot_when_present(self):
        with patch.dict("os.environ", {"SYSTEMROOT": "C:\\Windows", "PATH": "C:\\bin"}, clear=True):
            env = _child_env()
        self.assertEqual(env.get("SYSTEMROOT"), "C:\\Windows")
        self.assertEqual(env.get("PATH"), "C:\\bin")

    def test_does_not_leak_unrelated_env_vars(self):
        with patch.dict("os.environ", {"PATH": "C:\\bin", "SOME_SECRET": "shh"}, clear=True):
            env = _child_env()
        self.assertNotIn("SOME_SECRET", env)


class TestFetchV2ex(unittest.IsolatedAsyncioTestCase):
    async def test_topic_url_dispatches_to_get_topic(self):
        fake_channel = MagicMock()
        fake_channel.get_topic.return_value = {"id": 123, "title": "Hello", "url": "https://www.v2ex.com/t/123"}

        with patch.object(platform_cli._doctor, "available", return_value=True), patch(
            "agent_reach.channels.get_channel", return_value=fake_channel
        ):
            outcome = await fetch("https://www.v2ex.com/t/123")

        self.assertEqual(outcome.status, 200)
        body = json.loads(outcome.body)
        self.assertEqual(body["title"], "Hello")
        fake_channel.get_topic.assert_called_once_with(123)

    async def test_unavailable_channel_returns_status_none(self):
        with patch.object(platform_cli._doctor, "available", return_value=False):
            outcome = await fetch("https://www.v2ex.com/t/123")
        self.assertIsNone(outcome.status)

    async def test_non_topic_v2ex_url_is_unsupported(self):
        with patch.object(platform_cli._doctor, "available", return_value=True):
            outcome = await fetch("https://www.v2ex.com/")
        self.assertIsNone(outcome.status)
        self.assertIn("UnsupportedOrUnavailablePlatform", outcome.signals[0])


class TestFetchGithub(unittest.IsolatedAsyncioTestCase):
    async def _mock_proc(self, returncode: int, stdout: bytes, stderr: bytes = b""):
        proc = MagicMock()
        proc.communicate = unittest.mock.AsyncMock(return_value=(stdout, stderr))
        proc.returncode = returncode
        return proc

    async def test_repo_url_calls_repo_view(self):
        proc = await self._mock_proc(0, b'{"name":"Hello-World"}')
        with patch.object(platform_cli._doctor, "available", return_value=True), patch(
            "asyncio.create_subprocess_exec", unittest.mock.AsyncMock(return_value=proc)
        ) as mock_exec:
            outcome = await fetch("https://github.com/octocat/Hello-World")
        self.assertEqual(outcome.status, 200)
        args = mock_exec.await_args.args
        self.assertEqual(args, ("gh", "repo", "view", "octocat/Hello-World", "--json", "name,description,url"))

    async def test_issue_url_calls_issue_view(self):
        proc = await self._mock_proc(0, b'{"title":"Bug"}')
        with patch.object(platform_cli._doctor, "available", return_value=True), patch(
            "asyncio.create_subprocess_exec", unittest.mock.AsyncMock(return_value=proc)
        ) as mock_exec:
            await fetch("https://github.com/octocat/Hello-World/issues/42")
        args = mock_exec.await_args.args
        self.assertEqual(
            args, ("gh", "issue", "view", "42", "--repo", "octocat/Hello-World", "--json", "title,body,state,url")
        )

    async def test_pr_url_calls_pr_view(self):
        proc = await self._mock_proc(0, b'{"title":"Fix"}')
        with patch.object(platform_cli._doctor, "available", return_value=True), patch(
            "asyncio.create_subprocess_exec", unittest.mock.AsyncMock(return_value=proc)
        ) as mock_exec:
            await fetch("https://github.com/octocat/Hello-World/pull/7")
        args = mock_exec.await_args.args
        self.assertEqual(
            args, ("gh", "pr", "view", "7", "--repo", "octocat/Hello-World", "--json", "title,body,state,url")
        )

    async def test_nonzero_returncode_becomes_status_none(self):
        proc = await self._mock_proc(1, b"", b"not found")
        with patch.object(platform_cli._doctor, "available", return_value=True), patch(
            "asyncio.create_subprocess_exec", unittest.mock.AsyncMock(return_value=proc)
        ):
            outcome = await fetch("https://github.com/octocat/Hello-World")
        self.assertIsNone(outcome.status)
        self.assertIn("GhCliError", outcome.signals[0])

    async def test_unavailable_channel_never_spawns_subprocess(self):
        with patch.object(platform_cli._doctor, "available", return_value=False), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            outcome = await fetch("https://github.com/octocat/Hello-World")
        self.assertIsNone(outcome.status)
        mock_exec.assert_not_called()

    async def test_real_gh_cli_repo_view(self):
        if not shutil.which("gh"):
            self.skipTest("gh not on PATH")
        try:
            subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=10, check=True)
        except Exception:
            self.skipTest("gh not authenticated")

        with patch.object(platform_cli._doctor, "available", return_value=True):
            outcome = await fetch("https://github.com/octocat/Hello-World")
        self.assertEqual(outcome.status, 200)
        body = json.loads(outcome.body)
        self.assertEqual(body["name"], "Hello-World")


class TestFetchLinkedin(unittest.IsolatedAsyncioTestCase):
    async def _mock_proc(self, returncode: int, stdout: bytes, stderr: bytes = b""):
        proc = MagicMock()
        proc.communicate = unittest.mock.AsyncMock(return_value=(stdout, stderr))
        proc.returncode = returncode
        return proc

    async def test_profile_url_calls_mcporter_get_person_profile(self):
        # mcporter resolves through shutil.which() -- on Windows it's only
        # invocable as the mcporter.cmd npm shim, not a bare "mcporter"
        # (verified live: create_subprocess_exec("mcporter", ...) raised
        # FileNotFoundError against the real install, since it doesn't do
        # PATHEXT resolution the way a shell does). Patch which() so this
        # test doesn't depend on any particular machine's install path.
        proc = await self._mock_proc(0, b'{"name":"Jane Doe"}')
        url = "https://www.linkedin.com/in/janedoe"
        with patch.object(platform_cli._doctor, "available", return_value=True), patch(
            "asyncio.create_subprocess_exec", unittest.mock.AsyncMock(return_value=proc)
        ) as mock_exec, patch("shutil.which", return_value="/fake/path/mcporter.cmd"):
            outcome = await fetch(url)
        self.assertEqual(outcome.status, 200)
        args = mock_exec.await_args.args
        self.assertEqual(
            args,
            (
                "/fake/path/mcporter.cmd",
                "call",
                "linkedin.get_person_profile",
                f"linkedin_username={url}",
                "--output",
                "json",
            ),
        )

    async def test_non_profile_url_is_unsupported(self):
        with patch.object(platform_cli._doctor, "available", return_value=True), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            outcome = await fetch("https://www.linkedin.com/company/acme")
        self.assertIsNone(outcome.status)
        mock_exec.assert_not_called()

    async def test_nonzero_returncode_becomes_status_none(self):
        proc = await self._mock_proc(1, b"", b"login required")
        with patch.object(platform_cli._doctor, "available", return_value=True), patch(
            "asyncio.create_subprocess_exec", unittest.mock.AsyncMock(return_value=proc)
        ):
            outcome = await fetch("https://www.linkedin.com/in/janedoe")
        self.assertIsNone(outcome.status)
        self.assertIn("McporterError", outcome.signals[0])

    async def test_unavailable_channel_never_spawns_subprocess(self):
        with patch.object(platform_cli._doctor, "available", return_value=False), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            outcome = await fetch("https://www.linkedin.com/in/janedoe")
        self.assertIsNone(outcome.status)
        mock_exec.assert_not_called()


class TestFetchUnsupportedHost(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_platform_returns_status_none(self):
        outcome = await fetch("https://example.com/whatever")
        self.assertIsNone(outcome.status)

    async def test_dispatch_exception_becomes_status_none(self):
        with patch.object(platform_cli._doctor, "available", side_effect=RuntimeError("boom")):
            outcome = await fetch("https://www.v2ex.com/t/1")
        self.assertIsNone(outcome.status)
        self.assertIn("exception:RuntimeError", outcome.signals[0])


if __name__ == "__main__":
    unittest.main()
