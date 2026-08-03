"""Web search connector — one tool over a choice of search APIs.

Search is the most-asked-for thing an agent can't do out of the box, and every
provider returns a different shape. This normalises them to one list of
``{title, url, snippet}`` so a prompt reading the results doesn't have to change
when the provider does.

```yaml
connectors:
  search:
    type: web_search
    provider: tavily          # tavily | brave | serper
    api_key: ${TAVILY_API_KEY}
```
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from roscoe.connectors.base_connector import BaseConnector

#: Provider -> (base url, the header carrying the key).
_PROVIDERS: dict[str, tuple[str, str]] = {
    "tavily": ("https://api.tavily.com", "Authorization"),
    "brave": ("https://api.search.brave.com", "X-Subscription-Token"),
    "serper": ("https://google.serper.dev", "X-API-KEY"),
}


class WebSearchConnector(BaseConnector):
    """Tools: search."""

    def _provider(self) -> str:
        name = str(self.config.get("provider", "tavily")).lower()
        if name not in _PROVIDERS:
            raise ValueError(
                f"web_search connector: unknown provider '{name}'. "
                f"Available: {', '.join(sorted(_PROVIDERS))}."
            )
        return name

    def _base_url(self) -> str:
        return _PROVIDERS[self._provider()][0]

    def _auth_headers(self) -> dict[str, str]:
        key = self.config.get("api_key")
        if not key:
            raise ValueError("web_search connector config missing required key 'api_key'.")
        provider = self._provider()
        header = _PROVIDERS[provider][1]
        # Tavily takes a bearer token; the others take the key bare.
        value = f"Bearer {key}" if provider == "tavily" else str(key)
        return {header: value, "Content-Type": "application/json"}

    @property
    def tools(self) -> list[StructuredTool]:
        def search(query: str, max_results: int = 5) -> Any:
            """Search the web and return a list of {title, url, snippet} results."""
            provider = self._provider()
            if provider == "tavily":
                raw = self._request("POST", "/search", json={
                    "query": query, "max_results": max_results,
                })
                items = raw.get("results", [])
                results = [
                    {"title": i.get("title", ""), "url": i.get("url", ""),
                     "snippet": i.get("content", "")}
                    for i in items
                ]
            elif provider == "brave":
                raw = self._request("GET", "/res/v1/web/search", params={
                    "q": query, "count": max_results,
                })
                items = (raw.get("web") or {}).get("results", [])
                results = [
                    {"title": i.get("title", ""), "url": i.get("url", ""),
                     "snippet": i.get("description", "")}
                    for i in items
                ]
            else:  # serper
                raw = self._request("POST", "/search", json={
                    "q": query, "num": max_results,
                })
                items = raw.get("organic", [])
                results = [
                    {"title": i.get("title", ""), "url": i.get("link", ""),
                     "snippet": i.get("snippet", "")}
                    for i in items
                ]
            return {"query": query, "results": results[:max_results]}

        return [StructuredTool.from_function(search, description=search.__doc__)]
