"""Tiny .env loader -- no python-dotenv dependency for ten lines of parsing.
Only fills variables not already set in the real environment, so an actual
`export` always wins over the file. Called by adapter entrypoints (cli.py,
api.py main()); library code keeps reading os.environ directly, same as
FIRECRAWL_API_KEY already does in orchestrator.py.
"""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.is_file():
        return
    for key, value in read_env(path).items():
        os.environ.setdefault(key, value)


def read_env(path: str = ".env") -> dict[str, str]:
    """Ordered key/value pairs from a .env file. Empty dict if it doesn't
    exist yet -- that's a normal first-run state, not an error."""
    p = Path(path)
    if not p.is_file():
        return {}
    values: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_env(updates: dict[str, str], path: str = ".env") -> dict[str, str]:
    """Merge `updates` into the .env file, dropping keys whose new value is
    the empty string (that's how a settings-page field says "clear this"),
    keeping every other existing key untouched. Returns the final merged
    dict. Comments in an existing file are not preserved -- Creel's own
    .env is never hand-annotated, and this is the only writer of it."""
    values = read_env(path)
    for key, value in updates.items():
        if value == "":
            values.pop(key, None)
        else:
            values[key] = value
    Path(path).write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return values
