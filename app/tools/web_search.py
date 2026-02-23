"""Web search tool using DuckDuckGo. No API key required."""

import json
import logging

from strands import tool

logger = logging.getLogger(__name__)


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for current information using DuckDuckGo.

    Use this tool when the user asks about:
    - Current news or events
    - General knowledge questions you're unsure about
    - Product information, recipes, health tips
    - Anything that requires up-to-date information

    Args:
        query: The search query string.
        max_results: Maximum number of results to return (default 5, max 10).

    Returns:
        JSON string with search results including title, URL, and snippet.
    """
    max_results = min(max(1, max_results), 10)

    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return json.dumps({"results": [], "message": f"No results found for: {query}"})

        formatted = []
        for r in results:
            formatted.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })

        logger.info(f"🔍 Web search: '{query}' → {len(formatted)} results")
        return json.dumps({"query": query, "results": formatted}, ensure_ascii=False)

    except ImportError:
        logger.error("duckduckgo-search package not installed")
        return json.dumps({"error": "Web search not available. Install: pip install duckduckgo-search"})
    except Exception as e:
        logger.error(f"Web search error: {e}")
        return json.dumps({"error": f"Search failed: {str(e)}"})
