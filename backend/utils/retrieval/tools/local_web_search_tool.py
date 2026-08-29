"""
Local web search tool using SearXNG (self-hosted metasearch engine).
Replaces Perplexity when SEARXNG_URL is configured.
Falls back to Perplexity gateway only if SEARXNG_URL is not set.
"""
import logging
import os
from urllib.parse import urlencode

import httpx
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_SEARXNG_URL = os.environ.get("SEARXNG_URL", "").strip().rstrip("/")


def _is_searxng_available() -> bool:
    return bool(_SEARXNG_URL)


@tool
async def local_web_search_tool(query: str) -> str:
    """
    Search the web using a local SearXNG instance.

    Use this tool when:
    - User asks about current events, news, or recent information
    - User asks questions that require up-to-date web information

    DO NOT use for user's personal data (memories, conversations, action items).

    Args:
        query: The search query string.

    Returns:
        Formatted search results with URLs and snippets.
    """
    if not _SEARXNG_URL:
        return (
            "Web search is not configured. "
            "Set SEARXNG_URL=http://your-searxng:8080 in your docker-compose.yml "
            "to enable local web search."
        )

    try:
        params = {
            "q": query,
            "format": "json",
            "engines": "google,duckduckgo,brave,wikipedia",
            "limit": 5,
            "safesearch": 0,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_SEARXNG_URL}/search?{urlencode(params)}",
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                logger.error(f"SearXNG search failed: {resp.status_code}")
                return f"Error: SearXNG returned status {resp.status_code}. Try again later."

            data = resp.json()
            results = data.get("results", [])
            if not results:
                return "No web search results found."

            lines = [f"Web search results for: {query}"]
            for i, r in enumerate(results[:5], 1):
                title = r.get("title", "Untitled")
                url = r.get("url", "")
                snippet = (r.get("content", "") or r.get("snippet", ""))[:200]
                engine = ", ".join(r.get("engines", [])) if r.get("engines") else "web"
                lines.append(f"\n{i}. {title}")
                lines.append(f"   {url}")
                if snippet:
                    lines.append(f"   {snippet}")
                if engine:
                    lines.append(f"   Source: {engine}")

            return "\n".join(lines)

    except httpx.TimeoutException:
        logger.warning("SearXNG search timeout")
        return "Error: Web search timed out. The SearXNG server may be overloaded."
    except Exception as e:
        logger.error(f"SearXNG search error: {e}")
        return f"Error: Web search failed — {e}"


def use_local_search() -> bool:
    """Check if local SearXNG search should be used."""
    return _is_searxng_available()


# Returns the appropriate web search tool based on configuration
def get_web_search_tool():
    """Return the appropriate web search tool instance."""
    if _is_searxng_available():
        return local_web_search_tool
    # Fall back to Perplexity (imported elsewhere to avoid circular imports)
    from utils.retrieval.tools.perplexity_tools import perplexity_web_search_tool
    return perplexity_web_search_tool