import json
import os
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from creel.adapters.api import create_app
from creel.core.env import read_env
from creel.core.guard import GuardConfig
from creel.core.orchestrator import Orchestrator
from creel.extract.base import ExtractOutcome
from tests.fixtures.server import FixtureServer

_LOCAL = GuardConfig(allow_private_hosts=True)


class TestScrapeEndpoint(unittest.TestCase):
    def test_missing_url_is_400(self):
        app = create_app(Orchestrator(guard_config=_LOCAL))
        client = TestClient(app)
        resp = client.get("/scrape")
        self.assertEqual(resp.status_code, 400)

    def test_default_include_is_data_and_markdown_not_html(self):
        with FixtureServer() as server:
            app = create_app(Orchestrator(guard_config=_LOCAL))
            client = TestClient(app)
            resp = client.get("/scrape", params={"url": server.url("/ok")})
        body = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("markdown", body)
        self.assertIn("data", body)
        self.assertNotIn("html", body)
        self.assertEqual(body["engine_path"], ["scrapling_http"])

    def test_html_is_opt_in_via_include(self):
        with FixtureServer() as server:
            app = create_app(Orchestrator(guard_config=_LOCAL))
            client = TestClient(app)
            resp = client.get("/scrape", params={"url": server.url("/ok"), "include": "html"})
        body = resp.json()
        self.assertIn("html", body)
        self.assertNotIn("markdown", body)
        self.assertIn("Fixture OK", body["html"])

    def test_cooldown_active_returns_429(self):
        with FixtureServer() as server:
            orch = Orchestrator(guard_config=_LOCAL)
            app = create_app(orch)
            client = TestClient(app)
            client.get("/scrape", params={"url": server.url("/rate-limited")})
            resp = client.get("/scrape", params={"url": server.url("/rate-limited")})
        self.assertEqual(resp.status_code, 429)
        self.assertIn("cooling down", resp.json()["error"])

    def test_prompt_with_extractor_populates_data(self):
        async def fake_extract(html, prompt, schema):
            return ExtractOutcome(data={"title": "Fixture OK"}, exact=True)

        with FixtureServer() as server:
            app = create_app(Orchestrator(guard_config=_LOCAL), llm_extract=fake_extract, model="test-model")
            client = TestClient(app)
            resp = client.get("/scrape", params={"url": server.url("/ok"), "prompt": "get the title"})
        self.assertEqual(resp.json()["data"], {"title": "Fixture OK"})


class TestScrapeStreamEndpoint(unittest.TestCase):
    def _read_sse_events(self, client, params):
        events = []
        with client.stream("GET", "/scrape/stream", params=params) as resp:
            event_name = None
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    events.append((event_name, json.loads(line.split(":", 1)[1].strip())))
        return events

    def test_stream_emits_attempt_and_done_events(self):
        with FixtureServer() as server:
            app = create_app(Orchestrator(guard_config=_LOCAL))
            client = TestClient(app)
            events = self._read_sse_events(client, {"url": server.url("/ok")})

        names = [e[0] for e in events]
        self.assertIn("attempt_started", names)
        self.assertIn("attempt_finished", names)
        self.assertEqual(names[-1], "done")
        done_payload = events[-1][1]
        self.assertEqual(done_payload["status"], "ok")
        self.assertEqual(done_payload["engine_path"], ["scrapling_http"])

    def test_stream_on_blocked_page_emits_multiple_attempts_then_done(self):
        with FixtureServer() as server:
            app = create_app(Orchestrator(guard_config=_LOCAL))
            client = TestClient(app)
            events = self._read_sse_events(client, {"url": server.url("/does-not-exist")})

        started_count = sum(1 for name, _ in events if name == "attempt_started")
        self.assertEqual(started_count, 1, "404 must not escalate to further attempts")
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["status"], "failed")


class TestStreamAndSyncAgree(unittest.TestCase):
    def test_identical_data_across_sync_and_streaming_surfaces(self):
        async def fake_extract(html, prompt, schema):
            return ExtractOutcome(data={"title": "Fixture OK"}, exact=True)

        with FixtureServer() as server:
            orch = Orchestrator(guard_config=_LOCAL)
            app = create_app(orch, llm_extract=fake_extract, model="test-model")
            client = TestClient(app)

            sync_resp = client.get(
                "/scrape", params={"url": server.url("/ok"), "prompt": "get the title", "include": "data,markdown"}
            )
            sync_body = sync_resp.json()

        with FixtureServer() as server2:
            orch2 = Orchestrator(guard_config=_LOCAL)
            app2 = create_app(orch2, llm_extract=fake_extract, model="test-model")
            client2 = TestClient(app2)
            events = TestScrapeStreamEndpoint()._read_sse_events(
                client2, {"url": server2.url("/ok"), "prompt": "get the title", "include": "data,markdown"}
            )
        stream_body = events[-1][1]

        self.assertEqual(sync_body["data"], stream_body["data"])
        self.assertEqual(sync_body["markdown"], stream_body["markdown"])
        self.assertEqual(sync_body["status"], stream_body["status"])


class TestSettingsEndpoint(unittest.TestCase):
    # settings_save intentionally mutates the real os.environ (so an NVIDIA
    # key change applies without a server restart) -- snapshot/restore
    # around every test here so that real behavior doesn't leak into later
    # tests in the same unittest process, the same class of bug the
    # cli.py load_dotenv() ordering fix addressed earlier.
    def setUp(self):
        self._env_snapshot = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_snapshot)

    def test_page_loads(self):
        app = create_app(Orchestrator(guard_config=_LOCAL))
        resp = TestClient(app).get("/settings")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])

    def test_unset_fields_report_not_set(self):
        with tempfile.TemporaryDirectory() as d:
            env_path = str(Path(d) / ".env")
            app = create_app(Orchestrator(guard_config=_LOCAL), env_path=env_path)
            resp = TestClient(app).get("/settings/data")
        fields = {f["key"]: f for f in resp.json()["fields"]}
        self.assertFalse(fields["FIRECRAWL_API_KEY"]["set"])
        self.assertEqual(fields["FIRECRAWL_API_KEY"]["value"], "")

    def test_secret_field_is_masked_not_echoed_in_plaintext(self):
        with tempfile.TemporaryDirectory() as d:
            env_path = str(Path(d) / ".env")
            Path(env_path).write_text("FIRECRAWL_API_KEY=fc-supersecrettoken1234\n", encoding="utf-8")
            app = create_app(Orchestrator(guard_config=_LOCAL), env_path=env_path)
            resp = TestClient(app).get("/settings/data")
        fields = {f["key"]: f for f in resp.json()["fields"]}
        masked = fields["FIRECRAWL_API_KEY"]["value"]
        self.assertTrue(fields["FIRECRAWL_API_KEY"]["set"])
        self.assertNotIn("supersecrettoken", masked)
        self.assertTrue(masked.endswith("1234"))

    def test_non_secret_field_shown_in_full(self):
        with tempfile.TemporaryDirectory() as d:
            env_path = str(Path(d) / ".env")
            Path(env_path).write_text("NVIDIA_MODEL=nvidia/nemotron-3-super-120b-a12b\n", encoding="utf-8")
            app = create_app(Orchestrator(guard_config=_LOCAL), env_path=env_path)
            resp = TestClient(app).get("/settings/data")
        fields = {f["key"]: f for f in resp.json()["fields"]}
        self.assertEqual(fields["NVIDIA_MODEL"]["value"], "nvidia/nemotron-3-super-120b-a12b")

    def test_post_writes_to_env_file(self):
        with tempfile.TemporaryDirectory() as d:
            env_path = str(Path(d) / ".env")
            app = create_app(Orchestrator(guard_config=_LOCAL), env_path=env_path)
            resp = TestClient(app).post("/settings", json={"FIRECRAWL_API_KEY": "fc-newkey"})
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["saved"])
            self.assertEqual(read_env(env_path)["FIRECRAWL_API_KEY"], "fc-newkey")

    def test_post_ignores_unknown_keys(self):
        with tempfile.TemporaryDirectory() as d:
            env_path = str(Path(d) / ".env")
            app = create_app(Orchestrator(guard_config=_LOCAL), env_path=env_path)
            TestClient(app).post("/settings", json={"NOT_A_REAL_SETTING": "whatever"})
        self.assertEqual(read_env(env_path), {})

    def test_blank_value_leaves_existing_key_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            env_path = str(Path(d) / ".env")
            Path(env_path).write_text("FIRECRAWL_API_KEY=fc-existing\n", encoding="utf-8")
            app = create_app(Orchestrator(guard_config=_LOCAL), env_path=env_path)
            TestClient(app).post("/settings", json={"NVIDIA_API_KEY": "nvapi-new"})
            result = read_env(env_path)
            self.assertEqual(result["FIRECRAWL_API_KEY"], "fc-existing")
            self.assertEqual(result["NVIDIA_API_KEY"], "nvapi-new")


if __name__ == "__main__":
    unittest.main()
