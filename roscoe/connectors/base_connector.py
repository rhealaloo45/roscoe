"""BaseConnector — shared interface + auth for pre-built tool collections.

A connector wraps an enterprise system's API behind ``@tool``-ready functions.
``connector.tools`` returns LangChain ``StructuredTool`` objects to hand straight to
``AgentRunner``. Auth is configured from the YAML ``connectors:`` block. An httpx
``transport`` can be injected for tests (so tools can be exercised without a live API).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx
from langchain_core.tools import StructuredTool


class BaseConnector(ABC):
    """Base class all connectors implement."""

    #: Default per-request timeout (seconds).
    timeout: float = 30.0

    def __init__(self, config: dict[str, Any], *, transport: Any | None = None) -> None:
        self.config = config
        self._client = self._build_client(transport)

    def _build_client(self, transport: Any | None) -> httpx.Client:
        # A connector's client is built once, then can sit idle between agent_step
        # nodes (a slow upstream node runs first, sometimes a minute-plus). A NAT
        # or proxy in between can silently drop that idle keep-alive connection;
        # the next request reuses the dead socket and dies with a connection
        # reset. A short keepalive_expiry recycles idle connections before that
        # happens, and retries=1 absorbs a reset that slips through anyway.
        return httpx.Client(
            base_url=self._base_url(),
            headers=self._auth_headers(),
            timeout=self.timeout,
            transport=transport or httpx.HTTPTransport(retries=1),
            limits=httpx.Limits(keepalive_expiry=5.0),
        )

    @abstractmethod
    def _base_url(self) -> str:
        """Return the API base URL."""

    @abstractmethod
    def _auth_headers(self) -> dict[str, str]:
        """Return auth headers applied to every request."""

    @property
    @abstractmethod
    def tools(self) -> list[StructuredTool]:
        """Return the connector's tools."""

    # --- helpers for subclasses ---

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._client.request(method, path, **kwargs)
        raise_for_status(resp)
        if resp.content and "application/json" in resp.headers.get("content-type", ""):
            return resp.json()
        return {"status_code": resp.status_code, "text": resp.text}

    def close(self) -> None:
        self._client.close()


def raise_for_status(resp: httpx.Response) -> None:
    """``resp.raise_for_status()``, but the error says what the server said.

    httpx's own message is just the status line — for an API error the actual
    reason ("Drive API has not been used in project ... before or it is
    disabled", ``invalid_grant``, a field-level validation message) is in the
    response body, and that's the one thing you need to fix it without guessing.
    """
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = resp.text.strip()
        if len(detail) > 500:
            detail = detail[:500] + "…"
        raise httpx.HTTPStatusError(
            f"{exc}\n{detail}" if detail else str(exc),
            request=exc.request,
            response=exc.response,
        ) from None
