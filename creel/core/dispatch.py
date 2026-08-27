"""Content-class routing, applied BEFORE the acquisition router.

PDFs are the common case this exists for: Scrapegraph-ai won't parse them,
Scrapling's .markdown() won't either, and walking a PDF through three browser
rungs is pure waste — while Firecrawl converts PDF->markdown natively. Sniff
early so the router can send PDF straight to the Firecrawl rung (recorded
honestly in engine_path) or fail fast as UNSUPPORTED_CONTENT instead of
laundering it through NETWORK or PARSE_FAILED.
"""
from __future__ import annotations

from enum import Enum
from urllib.parse import urlsplit


class ContentClass(Enum):
    HTML = "html"
    PDF = "pdf"
    XML_FEED = "xml_feed"
    JSON = "json"
    UNSUPPORTED = "unsupported"


_EXT_MAP = {
    ".pdf": ContentClass.PDF,
    ".xml": ContentClass.XML_FEED,
    ".rss": ContentClass.XML_FEED,
    ".atom": ContentClass.XML_FEED,
    ".json": ContentClass.JSON,
}

_CTYPE_MAP = {
    "application/pdf": ContentClass.PDF,
    "application/xml": ContentClass.XML_FEED,
    "text/xml": ContentClass.XML_FEED,
    "application/rss+xml": ContentClass.XML_FEED,
    "application/atom+xml": ContentClass.XML_FEED,
    "application/json": ContentClass.JSON,
    "text/html": ContentClass.HTML,
    "application/xhtml+xml": ContentClass.HTML,
}


def classify_content(url: str, content_type: str | None = None) -> ContentClass:
    """Content-type is authoritative when present; URL extension is the
    fallback. An unknown/absent signal defaults to HTML — the common case —
    rather than UNSUPPORTED, since most real pages omit a clean extension."""
    if content_type:
        base = content_type.split(";")[0].strip().lower()
        if base in _CTYPE_MAP:
            return _CTYPE_MAP[base]
        if base.startswith("text/") or "html" in base:
            return ContentClass.HTML
    path = urlsplit(url).path.lower()
    for ext, cls in _EXT_MAP.items():
        if path.endswith(ext):
            return cls
    return ContentClass.HTML
