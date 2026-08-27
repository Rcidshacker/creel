import os
import tempfile
import unittest
from pathlib import Path

from creel.core.env import load_dotenv, read_env, write_env


class TestReadEnv(unittest.TestCase):
    def test_missing_file_returns_empty_dict(self):
        self.assertEqual(read_env("/nonexistent/path/.env"), {})

    def test_parses_key_value_pairs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
            self.assertEqual(read_env(str(p)), {"FOO": "bar", "BAZ": "qux"})

    def test_skips_blank_lines_and_comments(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("# a comment\n\nFOO=bar\n", encoding="utf-8")
            self.assertEqual(read_env(str(p)), {"FOO": "bar"})


class TestWriteEnv(unittest.TestCase):
    def test_creates_new_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            result = write_env({"FOO": "bar"}, str(p))
            self.assertEqual(result, {"FOO": "bar"})
            self.assertEqual(read_env(str(p)), {"FOO": "bar"})

    def test_merges_without_touching_other_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            write_env({"FOO": "bar", "BAZ": "qux"}, str(p))
            write_env({"FOO": "updated"}, str(p))
            self.assertEqual(read_env(str(p)), {"FOO": "updated", "BAZ": "qux"})

    def test_empty_string_value_clears_the_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            write_env({"FOO": "bar", "BAZ": "qux"}, str(p))
            write_env({"FOO": ""}, str(p))
            self.assertEqual(read_env(str(p)), {"BAZ": "qux"})


class TestLoadDotenv(unittest.TestCase):
    def test_does_not_override_real_env(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("CREEL_TEST_VAR=from_file\n", encoding="utf-8")
            os.environ["CREEL_TEST_VAR"] = "from_real_env"
            try:
                load_dotenv(str(p))
                self.assertEqual(os.environ["CREEL_TEST_VAR"], "from_real_env")
            finally:
                del os.environ["CREEL_TEST_VAR"]

    def test_missing_file_is_a_noop(self):
        load_dotenv("/nonexistent/path/.env")  # must not raise


if __name__ == "__main__":
    unittest.main()
