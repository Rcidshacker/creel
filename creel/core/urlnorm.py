"""URL canonicalization for cache/identity keying, and registrable-domain
extraction for domain-scoped memory/breaker/cooldown keys.

Without this, one URL with 50 tracking-param variants earns 50 cache rows,
and domain memory keyed on raw hostname fragments across every CDN subdomain
instead of the site that actually owns the policy.
"""
from __future__ import annotations

from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_EXACT = {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "ref", "ref_src", "igshid"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _strip_tracking(query: str) -> str:
    pairs = parse_qsl(query, keep_blank_values=True)
    kept = [
        (k, v)
        for k, v in pairs
        if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_EXACT
    ]
    kept.sort()  # order-independence -> identical query params always hash the same
    return urlencode(kept)


def canonicalize(url: str) -> str:
    """Normalize a URL for cache/identity keying. Not for display or for
    following redirects — use response.final_url for that."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    if parts.port and parts.port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{parts.port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query = _strip_tracking(parts.query)
    return urlunsplit((scheme, netloc, path, query, ""))


def registrable_domain(url: str) -> str:
    """eTLD+1 for domain-scoped memory/breaker/cooldown keys — never a raw
    subdomain, since CDNs spawn endless ones. Uses the `tld` package's public
    suffix list, already installed as a Scrapling dependency."""
    from tld import get_fld

    try:
        return get_fld(url, fix_protocol=True)
    except Exception:
        return (urlsplit(url).hostname or url).lower()
