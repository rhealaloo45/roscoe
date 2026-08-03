"""What each connector is, in plain language, and what it needs to be set up.

The builder used to offer one generic form — a name, a type from a list of
twenty, and free-form key/value pairs — which only helps if you already know
what keys that connector wants. This is the description it needs instead: a
readable label, what the thing is for, and the exact fields with help text.

Kept beside the connectors rather than in the CLI because it describes the
connectors: adding one here alongside the class is how it shows up in the
picker, and it's obvious it was missed if it doesn't.

``env`` on a field is the environment variable it conventionally reads, used to
prefill ``${VAR}`` so nobody types a secret into a form that gets written to
disk.
"""

from __future__ import annotations

from typing import Any


def _f(name: str, label: str, *, secret: bool = False, env: str | None = None,
       help: str = "", required: bool = True, placeholder: str = "",
       choices: list[str] | None = None, default: Any = None) -> dict[str, Any]:
    return {
        "name": name, "label": label, "secret": secret, "env": env, "help": help,
        "required": required, "placeholder": placeholder, "choices": choices,
        "default": default,
    }


#: type name -> how to present and configure it.
CATALOG: dict[str, dict[str, Any]] = {
    "web_search": {
        "label": "Web search",
        "blurb": "Search the web and read back titles, links and snippets.",
        "category": "Information",
        "fields": [
            _f("provider", "Search provider", choices=["tavily", "brave", "serper"],
               default="tavily", help="Tavily has a free tier and needs no card."),
            _f("api_key", "API key", secret=True, env="SEARCH_API_KEY"),
        ],
        "setup": "Sign up with your chosen provider and paste the API key.",
    },
    "smtp": {
        "label": "Email (SMTP)",
        "blurb": "Send email from any mailbox. No Google/Microsoft app to register.",
        "category": "Communication",
        "fields": [
            _f("host", "SMTP server", placeholder="smtp.gmail.com"),
            _f("port", "Port", default=587, required=False,
               help="587 for STARTTLS (usual), 465 for SSL."),
            _f("username", "Username", env="SMTP_USER", placeholder="you@example.com"),
            _f("password", "Password", secret=True, env="SMTP_PASSWORD",
               help="With 2FA on, this is an app password, not your account password."),
            _f("from", "From address", required=False, env="SMTP_USER",
               help="Defaults to the username."),
        ],
        "setup": "Gmail: enable 2-step verification, then create an app password.",
    },
    "twilio": {
        "label": "SMS (Twilio)",
        "blurb": "Send a text message.",
        "category": "Communication",
        "fields": [
            _f("account_sid", "Account SID", env="TWILIO_ACCOUNT_SID"),
            _f("auth_token", "Auth token", secret=True, env="TWILIO_AUTH_TOKEN"),
            _f("from", "From number", env="TWILIO_FROM_NUMBER", placeholder="+15550000000",
               help="A number you own in Twilio, in +country format."),
        ],
        "setup": "From the Twilio console dashboard.",
    },
    "google_workspace": {
        "label": "Google Workspace",
        "blurb": "Gmail, Calendar, Tasks and Drive.",
        "category": "Communication",
        "fields": [
            _f("client_id", "Client ID", env="GOOGLE_CLIENT_ID"),
            _f("client_secret", "Client secret", secret=True, env="GOOGLE_CLIENT_SECRET"),
            _f("refresh_token", "Refresh token", secret=True, env="GOOGLE_REFRESH_TOKEN",
               help="Created for you by `roscoe google-auth`."),
        ],
        "setup": "Create a Desktop app OAuth client in Google Cloud Console, put the "
                 "id and secret in .env, then run `roscoe google-auth` to mint the "
                 "refresh token.",
    },
    "outlook": {
        "label": "Outlook",
        "blurb": "Send mail and manage calendar via Microsoft Graph.",
        "category": "Communication",
        "fields": [
            _f("client_id", "Client ID", env="OUTLOOK_CLIENT_ID"),
            _f("client_secret", "Client secret", secret=True, env="OUTLOOK_CLIENT_SECRET"),
            _f("tenant_id", "Tenant ID", env="OUTLOOK_TENANT_ID"),
            _f("mailbox", "Mailbox", placeholder="someone@your-org.com"),
        ],
        "setup": "Register an app in Entra ID with Mail.Send / Calendars.ReadWrite "
                 "application permissions and grant admin consent.",
    },
    "github": {
        "label": "GitHub",
        "blurb": "Issues, pull requests and repository contents.",
        "category": "Engineering",
        "fields": [
            _f("token", "Personal access token", secret=True, env="GITHUB_TOKEN"),
            _f("base_url", "API base URL", required=False,
               placeholder="https://api.github.com",
               help="Only change this for GitHub Enterprise."),
        ],
        "setup": "GitHub → Settings → Developer settings → Personal access tokens.",
    },
    "jira": {
        "label": "Jira",
        "blurb": "Search, read and create issues.",
        "category": "Engineering",
        "fields": [
            _f("base_url", "Site URL", placeholder="https://your-org.atlassian.net"),
            _f("email", "Account email", env="JIRA_EMAIL"),
            _f("api_token", "API token", secret=True, env="JIRA_TOKEN"),
        ],
        "setup": "Create an API token at id.atlassian.com → Security.",
    },
    "servicenow": {
        "label": "ServiceNow",
        "blurb": "Incidents and catalogue requests.",
        "category": "Engineering",
        "fields": [
            _f("instance_url", "Instance URL",
               placeholder="https://your-instance.service-now.com"),
            _f("username", "Username", env="SERVICENOW_USER"),
            _f("password", "Password", secret=True, env="SERVICENOW_PASSWORD"),
        ],
        "setup": "Use a service account with the roles the tools need.",
    },
    "sharepoint": {
        "label": "SharePoint",
        "blurb": "Read documents and lists from a site.",
        "category": "Documents",
        "fields": [
            _f("client_id", "Client ID", env="SP_CLIENT_ID"),
            _f("client_secret", "Client secret", secret=True, env="SP_CLIENT_SECRET"),
            _f("tenant_id", "Tenant ID", env="SP_TENANT_ID"),
            _f("site_id", "Site ID", env="SP_SITE_ID",
               help="The Graph site id: host,siteCollectionId,siteId."),
        ],
        "setup": "Needs Sites.Read.All (or ReadWrite) with admin consent.",
    },
    "notion": {
        "label": "Notion",
        "blurb": "Search pages, read and create in databases.",
        "category": "Documents",
        "fields": [
            _f("token", "Integration token", secret=True, env="NOTION_TOKEN"),
            _f("version", "API version", required=False, default="2022-06-28"),
        ],
        "setup": "Create an internal integration, then share the pages it should "
                 "see with it — it can't reach anything until you do.",
    },
    "ticktick": {
        "label": "TickTick",
        "blurb": "Read and create tasks, with checklists.",
        "category": "Productivity",
        "fields": [
            _f("token", "Access token", secret=True, env="TICKTICK_TOKEN"),
            _f("default_project_id", "Default list", required=False,
               env="TICKTICK_INBOX_ID",
               help="Used when a task doesn't name a list of its own."),
        ],
        "setup": "From TickTick's developer settings.",
    },
    "database": {
        "label": "Database",
        "blurb": "Query a SQL database. SQLite needs no driver or server.",
        "category": "Data",
        "fields": [
            _f("path", "SQLite file", placeholder="./app.db",
               help="Created if it doesn't exist."),
            _f("schema", "Schema file", required=False, placeholder="./schema.sql",
               help="Plain SQL, applied once when the database is first created."),
            _f("read_only", "Read only", required=False, default=True,
               choices=["true", "false"],
               help="Writes are refused unless you turn this off."),
        ],
        "setup": "Nothing to install for SQLite.",
    },
    "snowflake": {
        "label": "Snowflake",
        "blurb": "Query a Snowflake warehouse.",
        "category": "Data",
        "fields": [
            _f("account", "Account", env="SNOWFLAKE_ACCOUNT"),
            _f("user", "User", env="SNOWFLAKE_USER"),
            _f("password", "Password", secret=True, env="SNOWFLAKE_PASSWORD"),
            _f("warehouse", "Warehouse", env="SNOWFLAKE_WAREHOUSE"),
            _f("database", "Database", env="SNOWFLAKE_DATABASE"),
            _f("schema", "Schema", env="SNOWFLAKE_SCHEMA"),
        ],
        "setup": 'Needs the driver: pip install "roscoe[snowflake]".',
    },
    "agent": {
        "label": "Another agent",
        "blurb": "Ask a different roscoe agent and use its answer here.",
        "category": "Custom",
        "fields": [
            _f("base_url", "Where it is running",
               placeholder="http://localhost:8091"),
            _f("api_key", "Its API key", secret=True, required=False,
               env="AGENT_API_KEY",
               help="Only if that agent was started with --api-key."),
        ],
        "setup": "Start the other agent with `roscoe run --no-browser --port 8091`.",
    },
    "rest_api": {
        "label": "Your own API",
        "blurb": "Call any REST API — yours, or a public one.",
        "category": "Custom",
        "fields": [
            _f("base_url", "Base URL", placeholder="https://api.example.com"),
            _f("auth", "Authentication", required=False, default="none",
               choices=["none", "bearer", "api_key", "basic"]),
            _f("token", "Token", secret=True, required=False,
               help="For bearer authentication."),
            _f("api_key", "API key", secret=True, required=False,
               help="For api_key authentication."),
            _f("header", "Key header", required=False, default="X-API-Key",
               help="Which header carries the API key."),
            _f("username", "Username", required=False, help="For basic authentication."),
            _f("password", "Password", secret=True, required=False,
               help="For basic authentication."),
        ],
        "setup": "Public APIs usually need nothing but the base URL.",
    },
}


def describe(type_name: str) -> dict[str, Any] | None:
    """How to present one connector type, or ``None`` if it isn't catalogued."""
    return CATALOG.get(type_name)


def catalog() -> list[dict[str, Any]]:
    """Every catalogued connector, grouped-ready and sorted for display."""
    return [
        {"type": name, **spec}
        for name, spec in sorted(
            CATALOG.items(), key=lambda kv: (kv[1]["category"], kv[1]["label"])
        )
    ]
