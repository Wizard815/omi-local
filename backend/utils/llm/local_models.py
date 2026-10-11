"""Model discovery for self-hosted (local-LLM) deployments.

OPENAI_BASE_URL points at omi-host's merge proxy, which lists every model it can
route to as "<backend-tag>/<raw id>" (e.g. "llama/gemma-4-12b").
"""

from __future__ import annotations

import os

import httpx


async def list_proxy_models(timeout: float = 5.0) -> list[dict[str, str]]:
    """``[{"id", "owned_by"}]`` from OPENAI_BASE_URL's /models, or [] when unreachable."""
    base = os.getenv('OPENAI_BASE_URL', '').strip()
    if not base:
        return []
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base.rstrip('/')}/models")
    except httpx.HTTPError:
        return []
    if resp.status_code != 200:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    models: list[dict[str, str]] = []
    for item in data.get('data', []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get('id'):
            models.append({'id': str(item['id']), 'owned_by': str(item.get('owned_by') or '')})
    return models
