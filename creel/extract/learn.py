"""Promotes a validated LLM extraction's candidate selectors into the free
tier-1 selector cache, keyed (domain, template_hash). Validation happens
HERE, before anything is cached: run the candidate selectors against the
SAME html and compare to the values the LLM actually returned. Only an
exact match is trusted — this is what stops a hallucinated selector from
poisoning tier 1 forever.

Selector maps themselves are stored in core.store's extract_cache table
(prefixed "selectors::") rather than a third table — both are "small JSON
blob keyed by a string", and Scrapling's own adaptive storage is designed
for ITS internal element-relocation bookkeeping (identifier/url/percentage
similarity), not a general (domain, template) -> {field: css} map. Reusing
what already exists is simpler and avoids depending on an internal API
surface that isn't meant for this.

Never learns from a cached body — callers must only invoke this with
freshly-fetched HTML (see the orchestrator's stale-body rule).
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

from creel.core.store import Store
from creel.extract.selectors import establish_adaptive_baseline, extract_with_selectors

_NUMERIC_SEGMENT = re.compile(r"\d+")


def template_hash(url: str) -> str:
    """URL path shape, numeric/slug segments normalized — cheap, catches
    /product/123 vs /product/456.

    ponytail: a DOM-skeleton hash is the documented upgrade if this
    misfires on sites that vary layout within one path shape. Not adopted
    speculatively — this hash is computed AFTER the fetch, unlike a cache
    lookup key, so upgrading it later is a one-function change.
    """
    path = urlsplit(url).path
    segments = [seg for seg in path.split("/") if seg]
    normalized = ["#" if _NUMERIC_SEGMENT.fullmatch(seg) else seg for seg in segments]
    return "/".join(normalized) or "/"


def _selector_key(domain: str, template: str) -> str:
    return f"selectors::{domain}::{template}"


def try_learn(
    store: Store,
    domain: str,
    url: str,
    html: str,
    extracted_data: dict,
    candidate_selectors: dict[str, str],
    storage_file: Optional[str] = None,
) -> bool:
    """Validates candidate_selectors against html + extracted_data. Caches
    and returns True only on an exact match for every field that has both a
    value and a candidate selector."""
    if not candidate_selectors:
        return False

    checked = {f: sel for f, sel in candidate_selectors.items() if f in extracted_data}
    if not checked:
        return False

    replayed = extract_with_selectors(html, url, checked, allow_adaptive=False, storage_file=storage_file)
    if replayed.data is None:
        return False
    for field, _ in checked.items():
        if str(extracted_data.get(field, "")).strip() != str(replayed.data.get(field, "")).strip():
            return False

    store.put_extract(_selector_key(domain, template_hash(url)), checked)
    establish_adaptive_baseline(html, url, checked, storage_file=storage_file)
    return True


def load_learned_selectors(store: Store, domain: str, url: str) -> Optional[dict[str, str]]:
    return store.get_extract(_selector_key(domain, template_hash(url)))
