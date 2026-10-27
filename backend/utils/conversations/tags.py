"""Conversation tags: user-managed labels, normalized once at the boundary.

A tag is lowercase, trimmed, whitespace and slashes collapsed to one dash,
at most TAG_MAX_LENGTH characters; a conversation carries at most MAX_TAGS.
Normalization is the only rule shared by the API, the day bundle's auto-tags
and the tag catalog, so "Work", " work " and "WORK" are one tag everywhere.
"""

from __future__ import annotations

import re
from typing import Iterable, List

TAG_MAX_LENGTH = 40
MAX_TAGS = 20
_SEPARATORS = re.compile(r'[\s/\\,;#]+')


def normalize_tag(raw: object) -> str:
    text = str(raw or '').strip().lower()
    text = _SEPARATORS.sub('-', text).strip('-')
    text = re.sub(r'-{2,}', '-', text)
    return text[:TAG_MAX_LENGTH].rstrip('-')


def normalize_tags(raw: Iterable[object] | None) -> List[str]:
    """Normalized, de-duplicated, order-preserving tags, capped at MAX_TAGS."""
    out: List[str] = []
    for item in raw or ():
        tag = normalize_tag(item)
        if tag and tag not in out:
            out.append(tag)
        if len(out) >= MAX_TAGS:
            break
    return out
