"""Executive assistant agent tools.

Email and calendar over Microsoft 365 via roscoe's ``OutlookConnector`` (Graph
OAuth2). That connector now also carries Teams, To Do and OneDrive tools — this
template stays scoped to what an exec assistant actually needs, so it filters
down to the four rather than handing the agent messaging and file access it
was never asked to have. Put outgoing actions (send_email,
create_calendar_event) behind the approval gate in the config.
"""

from __future__ import annotations

from typing import Any

_TOOL_NAMES = {"send_email", "read_emails", "create_calendar_event", "get_availability"}


def build_tools(outlook: Any) -> list:
    """Return the Outlook email/calendar tools for the assistant."""
    return [tool for tool in outlook.tools if tool.name in _TOOL_NAMES]
