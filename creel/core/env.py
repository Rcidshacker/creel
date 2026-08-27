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
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
