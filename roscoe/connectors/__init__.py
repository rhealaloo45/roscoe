"""Connectors subpackage — pre-built tool collections for enterprise systems.

REST (generic), Jira, ServiceNow, Outlook, SharePoint, GitHub, Notion,
Google Workspace are HTTP-based.
Snowflake is SQL-based (optional driver: `pip install "roscoe[snowflake]"`).
"""

from roscoe.connectors.base_connector import BaseConnector
from roscoe.connectors.database import DatabaseConnector, DatabaseError
from roscoe.connectors.github import GitHubConnector
from roscoe.connectors.google_workspace import GoogleWorkspaceConnector
from roscoe.connectors.jira import JiraConnector
from roscoe.connectors.notion import NotionConnector
from roscoe.connectors.outlook import OutlookConnector
from roscoe.connectors.rest_api import RESTConnector
from roscoe.connectors.servicenow import ServiceNowConnector
from roscoe.connectors.sharepoint import SharePointConnector
from roscoe.connectors.snowflake import SnowflakeConnector

__all__ = [
    "BaseConnector",
    "DatabaseConnector",
    "DatabaseError",
    "RESTConnector",
    "JiraConnector",
    "ServiceNowConnector",
    "OutlookConnector",
    "SharePointConnector",
    "GitHubConnector",
    "NotionConnector",
    "GoogleWorkspaceConnector",
    "SnowflakeConnector",
]
