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


def _logo(path_d: str) -> str:
    """A service's own brand mark (Simple Icons, CC0), not a generic emoji.

    ``fill="currentColor"`` so it inherits whatever colour the picker/card
    is already drawn in — a fixed brand colour would clash with the accent
    colour used to highlight a selected connector.
    """
    return (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
        'fill="currentColor"><path d="' + path_d + '"/></svg>'
    )


#: type name -> how to present and configure it.
CATALOG: dict[str, dict[str, Any]] = {
    "web_search": {
        "label": "Web search",
        "icon": "\U0001F50D",
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
        "icon": "✉️",
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
        "icon": "\U0001F4F1",
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
    "telegram": {
        "label": "Telegram",
        "icon": _logo("M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"),
        "blurb": "Send messages and photos through a bot.",
        "category": "Communication",
        "fields": [
            _f("bot_token", "Bot token", secret=True, env="TELEGRAM_BOT_TOKEN"),
            _f("default_chat_id", "Default chat ID", required=False, env="TELEGRAM_CHAT_ID",
               help="Used when a tool call doesn't name one."),
        ],
        "setup": "Message @BotFather on Telegram to create a bot and get its token.",
    },
    "slack": {
        "label": "Slack",
        "icon": "\U0001F4AC",
        "blurb": "Post and read messages in a channel.",
        "category": "Communication",
        "fields": [
            _f("bot_token", "Bot token", secret=True, env="SLACK_BOT_TOKEN",
               placeholder="xoxb-..."),
        ],
        "setup": "Create a Slack app, add chat:write (and channels:history to read) "
                 "bot scopes, install it to your workspace, then invite the bot to "
                 "the channels it should use.",
    },
    "google_workspace": {
        "label": "Google Workspace",
        "icon": _logo("M24 5.457v13.909c0 .904-.732 1.636-1.636 1.636h-3.819V11.73L12 16.64l-6.545-4.91v9.273H1.636A1.636 1.636 0 0 1 0 19.366V5.457c0-2.023 2.309-3.178 3.927-1.964L5.455 4.64 12 9.548l6.545-4.91 1.528-1.145C21.69 2.28 24 3.434 24 5.457z"),
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
        "icon": "\U0001F4E8",
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
        "icon": _logo("M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"),
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
        "icon": _logo("M11.571 11.513H0a5.218 5.218 0 0 0 5.232 5.215h2.13v2.057A5.215 5.215 0 0 0 12.575 24V12.518a1.005 1.005 0 0 0-1.005-1.005zm5.723-5.756H5.736a5.215 5.215 0 0 0 5.215 5.214h2.129v2.058a5.218 5.218 0 0 0 5.215 5.214V6.758a1.001 1.001 0 0 0-1.001-1.001zM23.013 0H11.455a5.215 5.215 0 0 0 5.215 5.215h2.129v2.057A5.215 5.215 0 0 0 24 12.483V1.005A1.001 1.001 0 0 0 23.013 0Z"),
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
        "icon": "\U0001F6E0️",
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
        "icon": "\U0001F4C1",
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
        "icon": _logo("M4.459 4.208c.746.606 1.026.56 2.428.466l13.215-.793c.28 0 .047-.28-.046-.326L17.86 1.968c-.42-.326-.981-.7-2.055-.607L3.01 2.295c-.466.046-.56.28-.374.466zm.793 3.08v13.904c0 .747.373 1.027 1.214.98l14.523-.84c.841-.046.935-.56.935-1.167V6.354c0-.606-.233-.933-.748-.887l-15.177.887c-.56.047-.747.327-.747.933zm14.337.745c.093.42 0 .84-.42.888l-.7.14v10.264c-.608.327-1.168.514-1.635.514-.748 0-.935-.234-1.495-.933l-4.577-7.186v6.952L12.21 19s0 .84-1.168.84l-3.222.186c-.093-.186 0-.653.327-.746l.84-.233V9.854L7.822 9.76c-.094-.42.14-1.026.793-1.073l3.456-.233 4.764 7.279v-6.44l-1.215-.139c-.093-.514.28-.887.747-.933zM1.936 1.035l13.31-.98c1.634-.14 2.055-.047 3.082.7l4.249 2.986c.7.513.934.653.934 1.213v16.378c0 1.026-.373 1.634-1.68 1.726l-15.458.934c-.98.047-1.448-.093-1.962-.747l-3.129-4.06c-.56-.747-.793-1.306-.793-1.96V2.667c0-.839.374-1.54 1.447-1.632z"),
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
        "icon": _logo("M12 0C5.383 0 0 5.383 0 12s5.383 12 12 12 12-5.383 12-12h-2.7c0 5.128-4.172 9.3-9.3 9.3-5.128 0-9.3-4.172-9.3-9.3 0-5.128 4.172-9.3 9.3-9.3V0Zm7.4 2.583-7.505 9.371L8.388 9.08l-2.002 2.436 4.741 3.888a1.573 1.573 0 0 0 2.231-.233l8.504-10.617L19.4 2.583Z"),
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
        "icon": "\U0001F5C4️",
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
        "icon": _logo("M24 3.459c0 .646-.418 1.18-1.141 1.18-.723 0-1.142-.534-1.142-1.18 0-.647.419-1.18 1.142-1.18.723 0 1.141.533 1.141 1.18zm-.228 0c0-.533-.38-.951-.913-.951s-.913.38-.913.95c0 .533.38.952.913.952.57 0 .913-.419.913-.951zm-1.37-.533h.495c.266 0 .456.152.456.38 0 .153-.076.229-.19.305l.19.266v.038h-.266l-.19-.266h-.229v.266h-.266zm.495.228h-.229v.267h.229c.114 0 .152-.038.152-.114.038-.077-.038-.153-.152-.153zM7.602 12.4c.038-.151.076-.304.076-.456 0-.114-.038-.228-.038-.342-.114-.343-.304-.647-.646-.838l-4.87-2.777c-.685-.38-1.56-.152-1.94.533-.381.685-.153 1.56.532 1.94l2.701 1.56-2.701 1.56c-.685.38-.913 1.256-.533 1.94.38.685 1.256.914 1.94.533l4.832-2.777c.343-.267.571-.533.647-.876zm1.332 2.626c-.266-.038-.57.038-.837.19l-4.832 2.777c-.685.38-.913 1.256-.532 1.94.38.686 1.255.914 1.94.533l2.701-1.56v3.12c0 .8.647 1.408 1.446 1.408.799 0 1.407-.647 1.407-1.408v-5.592c0-.761-.57-1.37-1.293-1.408zm4.946-6.088c.266.038.57-.038.837-.19l4.832-2.777c.685-.38.913-1.256.532-1.94-.38-.686-1.255-.914-1.94-.533l-2.701 1.56V1.975c0-.799-.647-1.408-1.446-1.408-.799 0-1.446.609-1.446 1.408V7.53c0 .76.609 1.37 1.332 1.407zM3.265 5.97l4.832 2.777c.266.152.533.19.837.19.723-.038 1.331-.684 1.331-1.407V1.975c0-.799-.646-1.408-1.407-1.408-.799 0-1.446.647-1.446 1.408v3.12l-2.701-1.56c-.685-.38-1.56-.152-1.94.533-.419.646-.19 1.521.494 1.902zm9.093 6.011a.412.412 0 00-.114-.266l-.57-.571a.346.346 0 00-.267-.114.412.412 0 00-.266.114l-.571.57a.411.411 0 00-.114.267c0 .076.038.19.114.267l.57.57a.345.345 0 00.267.114c.076 0 .19-.038.266-.114l.571-.57a.412.412 0 00.114-.267zm1.598.533L11.94 14.53c-.039.038-.153.114-.229.114h-.608a.411.411 0 01-.267-.114L8.82 12.514a.408.408 0 01-.076-.229v-.608c0-.076.038-.19.114-.267l2.016-2.016a.41.41 0 01.267-.114h.608a.41.41 0 01.267.114l2.016 2.016a.347.347 0 01.114.267v.608c-.076.077-.114.19-.19.229zm5.593 5.44l-4.832-2.777c-.266-.152-.57-.19-.837-.152-.723.038-1.332.684-1.332 1.408v5.554c0 .8.647 1.408 1.408 1.408.799 0 1.446-.647 1.446-1.408v-3.12l2.7 1.56c.686.38 1.561.152 1.941-.533.419-.646.19-1.521-.494-1.94zm2.549-7.533l-2.701 1.56 2.7 1.56c.686.38.914 1.256.533 1.94-.38.685-1.255.913-1.94.533l-4.832-2.778a1.644 1.644 0 01-.647-.798c-.037-.153-.076-.305-.076-.457 0-.114.039-.228.039-.342.114-.343.342-.647.646-.837l4.832-2.778c.685-.38 1.56-.152 1.94.533.457.609.19 1.484-.494 1.864"),
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
    "vector_store": {
        "label": "Vector store (local memory)",
        "icon": "\U0001F9E0",
        "blurb": "Remember text and recall the closest matches later. No embedding API.",
        "category": "Data",
        "fields": [
            _f("path", "SQLite file", required=False, default="./vectorstore.db",
               placeholder="./vectorstore.db", help="Created if it doesn't exist."),
        ],
        "setup": "Nothing to install — similarity is computed locally, no server "
                 "or embeddings API needed.",
    },
    "agent": {
        "label": "Another agent",
        "icon": "\U0001F916",
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
        "icon": "\U0001F310",
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
