"""Safety floors for local acquisition rungs: SSRF preflight, response-size
cap, content-type sniffing, and detail redaction before anything is stored.

Jina's SSRF guards protect *their* egress, not ours. Our own curl and headless
Chromium will happily fetch http://169.254.169.254/ or http://localhost:8080/
the moment a URL comes from anywhere programmatic — including the web UI
itself. preflight() is applied to LOCAL rungs only (engines/scrapling_*); the
remote-egress rungs (Jina, Firecrawl) fetch from their own network and are
exempt by construction.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # mirror Jina Reader's 5 MiB cap

_KEY_HINTS = ("api_key", "apikey", "token", "secret", "password", "authorization", "cookie", "ct0")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_URL_RE = re.compile(r"https?://\S+")


class SSRFError(ValueError):
    pass


class MaxBytesExceeded(ValueError):
    pass


@dataclass
class GuardConfig:
    allow_private_hosts: bool = False  # explicit opt-out for legitimate intranet scraping
    max_bytes: int = DEFAULT_MAX_BYTES


def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise SSRFError(f"cannot resolve host: {host}") from e
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def preflight(url: str, config: GuardConfig | None = None) -> None:
    """Raise SSRFError if a LOCAL engine must not fetch this url."""
    config = config or GuardConfig()
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise SSRFError(f"scheme not allowed: {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise SSRFError("no host in url")
    if config.allow_private_hosts:
        return
    for ip in _resolve(host):
        if not ip.is_global:
            raise SSRFError(f"host resolves to non-global address: {ip}")


def enforce_max_bytes(total_read: int, config: GuardConfig | None = None) -> None:
    """Call this incrementally while streaming a response body. A hostile
    800 MB response pulled into RAM through a headless browser is a
    self-inflicted DoS — abort before it accumulates, not after."""
    config = config or GuardConfig()
    if total_read > config.max_bytes:
        raise MaxBytesExceeded(f"body exceeded max_bytes={config.max_bytes}")


def sniff_content_type(headers: Mapping[str, str], body_prefix: bytes = b"") -> str:
    ctype = ""
    for k, v in headers.items():
        if k.lower() == "content-type":
            ctype = v.split(";")[0].strip().lower()
            break
    if ctype:
        return ctype
    stripped = body_prefix.lstrip()
    if stripped.startswith(b"%PDF-"):
        return "application/pdf"
    if stripped.startswith((b"<?xml", b"<rss", b"<feed")):
        return "application/xml"
    if stripped.startswith((b"{", b"[")):
        return "application/json"
    return "text/html"


def redact(text: str) -> str:
    """Scrub key-shaped substrings and truncate embedded URLs. Apply at the
    EMIT site (where an Attempt is constructed), not the render site — this
    text persists in the `attempts` table for weeks."""
    out = text
    for hint in _KEY_HINTS:
        out = re.sub(
            rf"(?i)({re.escape(hint)}[\"']?\s*[:=]\s*[\"']?)([^\s\"'&,}}]{{4,}})",
            r"\1<redacted>",
            out,
        )
    out = _URL_RE.sub(lambda m: (m.group(0)[:60] + "...<truncated>") if len(m.group(0)) > 60 else m.group(0), out)
    out = _LONG_TOKEN_RE.sub(lambda m: m.group(0)[:6] + "...<redacted>", out)
    return out
