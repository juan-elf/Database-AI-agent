"""
Web search module using the Tavily API.

Tavily is designed for LLM agents — results are clean and summarized,
not raw HTML. Free tier: 1,000 searches/month.

Setup:
1. Sign up at https://tavily.com (free)
2. Copy your API key
3. Add to .env: TAVILY_API_KEY=tvly-xxxxx
"""
import os
from typing import Any

from tavily import TavilyClient
from dotenv import load_dotenv

load_dotenv()

_client: TavilyClient | None = None


def _get_client() -> TavilyClient | None:
    """Lazy-initialize Tavily client. Returns None if API key is missing."""
    global _client
    if _client is not None:
        return _client

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return None

    _client = TavilyClient(api_key=api_key)
    return _client


def search_web(query: str, max_results: int = 5) -> dict[str, Any]:
    """
    Search the internet using Tavily.

    Returns:
        {
            "success": bool,
            "query": str,
            "answer": str | None,    # AI-generated summary from Tavily
            "results": list[dict],   # list of sources
            "error": str | None
        }
    """
    base = {
        "success": False,
        "query": query,
        "answer": None,
        "results": [],
        "error": None,
    }

    client = _get_client()
    if client is None:
        return {
            **base,
            "error": (
                "Web search unavailable: TAVILY_API_KEY not found in .env. "
                "Sign up for free at https://tavily.com to get an API key."
            )
        }

    max_results = max(1, min(max_results, 10))

    try:
        response = client.search(
            query=query,
            max_results=max_results,
            include_answer=True,
            search_depth="basic",
        )

        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", "")[:500],
                "score": round(item.get("score", 0), 3),
            }
            for item in response.get("results", [])
        ]

        return {
            **base,
            "success": True,
            "answer": response.get("answer"),
            "results": results,
        }

    except Exception as e:
        return {
            **base,
            "error": f"Web search error: {type(e).__name__}: {e}"
        }


def is_available() -> bool:
    """Return True if web search is available (API key present)."""
    return _get_client() is not None


if __name__ == "__main__":
    print("Testing web search...")
    if not is_available():
        print("❌ TAVILY_API_KEY not found. Set it in .env first.")
    else:
        result = search_web("typical Li-ion battery degradation rate per cycle", max_results=3)
        if result["success"]:
            print("✅ Search successful!")
            print(f"\n📝 Answer: {result['answer']}")
            print(f"\n🔗 Sources ({len(result['results'])}):")
            for r in result["results"]:
                print(f"  • {r['title']} ({r['url']})")
        else:
            print(f"❌ Error: {result['error']}")
