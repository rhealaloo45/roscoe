"""Google Workspace agent tools.

Gmail, Calendar (with Meet links), Tasks, and Drive via roscoe's
``GoogleWorkspaceConnector`` — every tool it offers, passed straight through.
Outgoing actions (send_email, create_event, create_task, delete_event,
delete_task, upload_drive_file) should be behind the approval gate.
"""

from __future__ import annotations

from typing import Any


def build_tools(google: Any) -> list:
    """Return the Google Workspace tools for the agent."""
    return list(google.tools)
