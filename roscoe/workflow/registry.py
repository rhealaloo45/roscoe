"""Building connectors from a config block, by name.

Workflow nodes address connectors by name (``connector: hr_rest``), so the
``connectors:`` block has to resolve names to live connector instances. Two spellings
are accepted:

```yaml
connectors:
  jira:                     # key is the connector type
    base_url: ...

  hr_rest:                  # key is a chosen name, type stated explicitly
    type: rest_api          # (needed when one type is used more than once)
    base_url: ...
```

Classes are looked up lazily so a project only pays the import cost — and the optional
dependency — for connectors it actually uses.
"""

from __future__ import annotations

from typing import Any

#: Connector type name -> attribute in ``roscoe.connectors``.
_TYPES: dict[str, str] = {
    "rest": "RESTConnector",
    "rest_api": "RESTConnector",
    "database": "DatabaseConnector",
    "sqlite": "DatabaseConnector",
    "sql": "DatabaseConnector",
    "jira": "JiraConnector",
    "servicenow": "ServiceNowConnector",
    "outlook": "OutlookConnector",
    "sharepoint": "SharePointConnector",
    "github": "GitHubConnector",
    "notion": "NotionConnector",
    "google_workspace": "GoogleWorkspaceConnector",
    "snowflake": "SnowflakeConnector",
    "ticktick": "TickTickConnector",
    "web_search": "WebSearchConnector",
    "search": "WebSearchConnector",
    "smtp": "SMTPConnector",
    "email": "SMTPConnector",
    "twilio": "TwilioConnector",
    "sms": "TwilioConnector",
}


class ConnectorError(ValueError):
    """Raised when a connector cannot be identified or constructed."""


def available_types() -> list[str]:
    """Connector type names usable in a ``connectors:`` block."""
    return sorted(_TYPES)


def get_connector_class(type_name: str) -> Any:
    """Resolve a connector type name to its class."""
    attribute = _TYPES.get(type_name)
    if attribute is None:
        raise ConnectorError(
            f"Unknown connector type '{type_name}'. Available: {', '.join(available_types())}"
        )
    import roscoe.connectors as connectors

    return getattr(connectors, attribute)


def build_connectors(block: dict[str, Any]) -> dict[str, Any]:
    """Instantiate every connector in a ``connectors:`` config block.

    Returns a mapping of the name used in the config to a live connector instance,
    ready to hand to :class:`~roscoe.workflow.executor.WorkflowExecutor`.
    """
    if not isinstance(block, dict):
        raise ConnectorError(
            f"'connectors' must be a mapping, got {type(block).__name__}."
        )

    built: dict[str, Any] = {}
    for name, settings in block.items():
        if not isinstance(settings, dict):
            raise ConnectorError(
                f"Connector '{name}' must be a mapping, got {type(settings).__name__}."
            )
        settings = dict(settings)
        type_name = settings.pop("type", name)
        cls = get_connector_class(str(type_name))
        try:
            built[name] = cls(settings)
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001 — name the connector that failed
            raise ConnectorError(
                f"Could not build connector '{name}' ({type_name}): "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    return built
