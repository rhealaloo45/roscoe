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


def _mode(key: str, label: str, fields: list[dict[str, Any]]) -> dict[str, Any]:
    """One selectable way to authenticate a connector that offers more than
    one — the picker shows a chooser and only that mode's fields, instead of
    every field for every mode mixed into one list with no way to tell which
    ones a given setup actually needs."""
    return {"key": key, "label": label, "fields": fields}


def _logo(path_d: str) -> str:
    """A service's own brand mark (Simple Icons, CC0), not a generic emoji.

    ``fill="currentColor"`` so it inherits whatever colour the picker/card
    is already drawn in — a fixed brand colour would clash with the accent
    colour used to highlight a selected connector.
    """
    return (
        '<svg class="i" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
        'fill="currentColor"><path d="' + path_d + '"/></svg>'
    )


def _icon(body: str) -> str:
    """A generic line icon, for a connector with no brand mark of its own.

    Stroked rather than filled (the counterpart to :func:`_logo`), so a
    category icon and a real brand logo sit at the same visual weight instead
    of an emoji's full-colour glyph shouting over everything next to it.
    """
    return (
        '<svg class="i" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" '
        'stroke-linejoin="round">' + body + "</svg>"
    )


_GOOGLE_SETUP = (
    "OAuth 2.0: create a Desktop app OAuth client in Google Cloud Console, put "
    "the id and secret in .env, then run `roscoe google-auth` to mint the "
    "refresh token. Service account: create one in Google Cloud Console, "
    "download its key, and have a workspace admin grant it domain-wide "
    "delegation."
)

#: One credential set covers every Google product, so each per-product entry
#: below offers exactly the same two ways to authenticate.
_GOOGLE_AUTH_MODES: list[dict[str, Any]] = [
    _mode("oauth", "OAuth 2.0 (single user)", [
        _f("client_id", "Client ID", env="GOOGLE_CLIENT_ID"),
        _f("client_secret", "Client secret", secret=True, env="GOOGLE_CLIENT_SECRET"),
        _f("refresh_token", "Refresh token", secret=True, env="GOOGLE_REFRESH_TOKEN",
           help="Created for you by `roscoe google-auth`."),
    ]),
    _mode("service_account", "Service account (org-wide)", [
        _f("credentials_file", "Service account key file",
           placeholder="./google-service-account.json",
           help="Path to the JSON key downloaded from Google Cloud Console."),
        _f("subject", "Acts as (email)", env="GOOGLE_SUBJECT",
           help="The user this agent impersonates — needs domain-wide "
                "delegation granted by a workspace admin."),
    ]),
]


def _google(label: str, icon: str, blurb: str, category: str) -> dict[str, Any]:
    """One Google product as its own connector.

    They all run on ``GoogleWorkspaceConnector`` and the same credentials; the
    registry narrows each one to its own tools. Listing them separately is what
    makes Calendar and Tasks findable — as a single "Google Workspace" entry
    the suite looked like one option, and its thirteen methods only appeared
    after it had already been added.
    """
    return {
        "label": label,
        "icon": icon,
        "blurb": blurb,
        "category": category,
        "auth_modes": [dict(m) for m in _GOOGLE_AUTH_MODES],
        "setup": _GOOGLE_SETUP,
    }


#: type name -> how to present and configure it.
CATALOG: dict[str, dict[str, Any]] = {
    "web_search": {
        "label": "Web search",
        "icon": _icon('<circle cx="11" cy="11" r="7"/><path d="m20.5 20.5-4.2-4.2"/>'),
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
        "icon": _icon('<rect x="2" y="4.5" width="20" height="15" rx="2.5"/>'
                      '<path d="m2.8 6.5 9.2 6 9.2-6"/>'),
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
        "icon": _logo(
            "M12 0C5.381-.008.008 5.352 0 11.971V12c0 6.64 5.359 12 12 12 6.64 0 12-5.36 "
            "12-12 0-6.641-5.36-12-12-12zm0 20.801c-4.846.015-8.786-3.904-8.801-8.75V12c"
            "-.014-4.846 3.904-8.786 8.75-8.801H12c4.847-.014 8.786 3.904 8.801 8.75V12c"
            ".015 4.847-3.904 8.786-8.75 8.801H12zm5.44-11.76c0 1.359-1.12 2.479-2.481 "
            "2.479-1.366-.007-2.472-1.113-2.479-2.479 0-1.361 1.12-2.481 2.479-2.481 "
            "1.361 0 2.481 1.12 2.481 2.481zm0 5.919c0 1.36-1.12 2.48-2.481 2.48-1.367"
            "-.008-2.473-1.114-2.479-2.48 0-1.359 1.12-2.479 2.479-2.479 1.361-.001 "
            "2.481 1.12 2.481 2.479zm-5.919 0c0 1.36-1.12 2.48-2.479 2.48-1.368-.007"
            "-2.475-1.113-2.481-2.48 0-1.359 1.12-2.479 2.481-2.479 1.358-.001 2.479 "
            "1.12 2.479 2.479zm0-5.919c0 1.359-1.12 2.479-2.479 2.479-1.367-.007-2.475"
            "-1.112-2.481-2.479 0-1.361 1.12-2.481 2.481-2.481 1.358 0 2.479 1.12 2.479 "
            "2.481z"
        ),
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
        "icon": _logo(
            "M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 "
            "2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 "
            "2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 "
            "0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 "
            "0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 "
            "2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 "
            "1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 "
            "1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 "
            "2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 "
            "0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 "
            "2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 "
            "15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 "
            "0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 "
            "15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z"
        ),
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
        "blurb": "Gmail, Calendar, Meet, Tasks and Drive.",
        "category": "Communication",
        "auth_modes": [
            _mode("oauth", "OAuth 2.0 (single user)", [
                _f("client_id", "Client ID", env="GOOGLE_CLIENT_ID"),
                _f("client_secret", "Client secret", secret=True, env="GOOGLE_CLIENT_SECRET"),
                _f("refresh_token", "Refresh token", secret=True, env="GOOGLE_REFRESH_TOKEN",
                   help="Created for you by `roscoe google-auth`."),
            ]),
            _mode("service_account", "Service account (org-wide)", [
                _f("credentials_file", "Service account key file",
                   placeholder="./google-service-account.json",
                   help="Path to the JSON key downloaded from Google Cloud Console."),
                _f("subject", "Acts as (email)", env="GOOGLE_SUBJECT",
                   help="The user this agent impersonates — needs domain-wide "
                        "delegation granted by a workspace admin."),
            ]),
        ],
        "setup": _GOOGLE_SETUP,
    },
    "gmail": _google(
        "Gmail",
        _logo("M24 5.457v13.909c0 .904-.732 1.636-1.636 1.636h-3.819V11.73L12 16.64l-6.545"
              "-4.91v9.273H1.636A1.636 1.636 0 0 1 0 19.366V5.457c0-2.023 2.309-3.178 3.927"
              "-1.964L5.455 4.64 12 9.548l6.545-4.91 1.528-1.145C21.69 2.28 24 3.434 24 "
              "5.457z"),
        "Send and read email from a Gmail mailbox.",
        "Communication",
    ),
    "google_calendar": _google(
        "Google Calendar",
        _logo("M18.316 5.684H24v12.632h-5.684V5.684zM5.684 24h12.632v-5.684H5.684V24zM18.316 "
              "5.684V0H1.895A1.894 1.894 0 0 0 0 1.895v16.421h5.684V5.684h12.632zm-7.207 "
              "6.25v-.065c.272-.144.5-.349.687-.617s.279-.595.279-.982c0-.379-.099-.72-.3"
              "-1.025a2.05 2.05 0 0 0-.832-.714 2.703 2.703 0 0 0-1.197-.257c-.6 0-1.094.156"
              "-1.481.467-.386.311-.65.671-.793 1.078l1.085.452c.086-.249.224-.461.413-.633."
              "189-.172.445-.257.767-.257.33 0 .602.088.816.264a.86.86 0 0 1 .322.703c0 .33"
              "-.12.589-.36.778-.24.19-.535.284-.886.284h-.567v1.085h.633c.407 0 .748.109 "
              "1.02.327.272.218.407.499.407.843 0 .336-.129.614-.387.832s-.565.327-.924.327c"
              "-.351 0-.651-.103-.897-.311-.248-.208-.422-.502-.521-.881l-1.096.452c.178.616"
              ".505 1.082.977 1.401.472.319.984.478 1.538.477a2.84 2.84 0 0 0 1.293-.291c."
              "382-.193.684-.458.902-.794.218-.336.327-.72.327-1.149 0-.429-.115-.797-.344"
              "-1.105a2.067 2.067 0 0 0-.881-.689zm2.093-1.931l.602.913L15 10.045v5.744h1.187"
              "V8.446h-.827l-2.158 1.557zM22.105 0h-3.289v5.184H24V1.895A1.894 1.894 0 0 0 "
              "22.105 0zm-3.289 23.5l4.684-4.684h-4.684V23.5zM0 22.105C0 23.152.848 24 1.895 "
              "24h3.289v-5.184H0v3.289z"),
        "Read, create and update calendar events, with Meet links.",
        "Productivity",
    ),
    "google_tasks": _google(
        "Google Tasks",
        _logo("M11.383.617C5.097.617 0 5.714 0 12c0 6.286 5.097 11.383 11.383 11.383 6.286 0 "
              "11.38-5.097 11.38-11.383a11.34 11.34 0 0 0-.878-4.389l-3.203 3.203c.062.387.1."
              "782.1 1.186a7.398 7.398 0 1 1-7.4-7.398c1.499 0 2.889.448 4.054 1.214l2.857"
              "-2.857a11.325 11.325 0 0 0-6.91-2.342zm9.674.756c-.292 0-.583.112-.805.334"
              "-2.97 2.965-5.934 5.934-8.9 8.902L9.596 8.854a1.139 1.139 0 0 0-1.61 0l-1.775 "
              "1.773a1.139 1.139 0 0 0 0 1.61l4.166 4.163a1.421 1.421 0 0 0 2.012 0L23.666 "
              "5.121a1.136 1.136 0 0 0 0-1.61l-1.805-1.804a1.136 1.136 0 0 0-.804-.334z"),
        "Read, create and complete tasks in a task list.",
        "Productivity",
    ),
    "google_drive": _google(
        "Google Drive",
        _logo("M12.01 1.485c-2.082 0-3.754.02-3.743.047.01.02 1.708 3.001 3.774 6.62l3.76 "
              "6.574h3.76c2.081 0 3.753-.02 3.742-.047-.005-.02-1.708-3.001-3.775-6.62l-3.76"
              "-6.574zm-4.76 1.73a789.828 789.861 0 0 0-3.63 6.319L0 15.868l1.89 3.298 1.885 "
              "3.297 3.62-6.335 3.618-6.33-1.88-3.287C8.1 4.704 7.255 3.22 7.25 3.214zm2.259 "
              "12.653-.203.348c-.114.198-.96 1.672-1.88 3.287a423.93 423.948 0 0 1-1.698 "
              "2.97c-.01.026 3.24.042 7.222.042h7.244l1.796-3.157c.992-1.734 1.85-3.23 1.906"
              "-3.323l.104-.167h-7.249z"),
        "Search Drive and read or create files.",
        "Documents",
    ),
    "outlook": {
        "label": "Outlook",
        "icon": _icon('<path d="M22 12.5h-5.5l-1.7 2.6H9.2l-1.7-2.6H2"/>'
                      '<path d="M5.6 4.9 2 12.5V18a2.5 2.5 0 0 0 2.5 2.5h15A2.5 2.5 0 0 0 '
                      '22 18v-5.5l-3.6-7.6a2 2 0 0 0-1.8-1.1H7.4a2 2 0 0 0-1.8 1.1z"/>'),
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
        "auth_modes": [
            _mode("token", "Personal access token", [
                _f("token", "Personal access token", secret=True, env="GITHUB_TOKEN"),
                _f("base_url", "API base URL", required=False,
                   placeholder="https://api.github.com",
                   help="Only change this for GitHub Enterprise."),
            ]),
            _mode("app", "GitHub App", [
                _f("app_id", "App ID", env="GITHUB_APP_ID"),
                _f("installation_id", "Installation ID", env="GITHUB_APP_INSTALLATION_ID",
                   help="From the URL when viewing the app's installation settings."),
                _f("private_key", "Private key (PEM)", secret=True, required=False,
                   env="GITHUB_APP_PRIVATE_KEY", help="Paste the .pem contents, or "
                   "use the file path field below instead."),
                _f("private_key_file", "...or private key file", required=False,
                   placeholder="./github-app.pem"),
                _f("base_url", "API base URL", required=False,
                   placeholder="https://api.github.com",
                   help="Only change this for GitHub Enterprise."),
            ]),
        ],
        "setup": "Personal access token: GitHub → Settings → Developer settings → "
                 "Personal access tokens. GitHub App (recommended for automation — "
                 "scoped to exactly the repos it's installed on, tokens expire in "
                 "an hour): github.com/settings/apps → New GitHub App, install it, "
                 "then download its private key.",
    },
    "jira": {
        "label": "Jira",
        "icon": _logo("M11.571 11.513H0a5.218 5.218 0 0 0 5.232 5.215h2.13v2.057A5.215 5.215 0 0 0 12.575 24V12.518a1.005 1.005 0 0 0-1.005-1.005zm5.723-5.756H5.736a5.215 5.215 0 0 0 5.215 5.214h2.129v2.058a5.218 5.218 0 0 0 5.215 5.214V6.758a1.001 1.001 0 0 0-1.001-1.001zM23.013 0H11.455a5.215 5.215 0 0 0 5.215 5.215h2.129v2.057A5.215 5.215 0 0 0 24 12.483V1.005A1.001 1.001 0 0 0 23.013 0Z"),
        "blurb": "Search, read and create issues.",
        "category": "Engineering",
        "auth_modes": [
            _mode("token", "API token", [
                _f("base_url", "Site URL", placeholder="https://your-org.atlassian.net"),
                _f("email", "Account email", env="JIRA_EMAIL"),
                _f("api_token", "API token", secret=True, env="JIRA_TOKEN"),
            ]),
            _mode("oauth", "OAuth 2.0", [
                _f("cloud_id", "Cloud ID", env="JIRA_CLOUD_ID",
                   help="From the OAuth app's accessible-resources response."),
                _f("client_id", "Client ID", env="JIRA_CLIENT_ID"),
                _f("client_secret", "Client secret", secret=True, env="JIRA_CLIENT_SECRET"),
                _f("refresh_token", "Refresh token", secret=True, env="JIRA_REFRESH_TOKEN"),
            ]),
        ],
        "setup": "API token: id.atlassian.com → Security. OAuth 2.0: register an "
                 "app at developer.atlassian.com, then run its standard "
                 "authorization-code flow once to get a refresh token.",
    },
    "servicenow": {
        "label": "ServiceNow",
        "icon": _icon('<path d="M14.6 6.4a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.7-3.7'
                      'a6 6 0 0 1-7.9 7.9l-6.8 6.8a2.1 2.1 0 0 1-3-3l6.8-6.8a6 6 0 0 1 '
                      '7.9-7.9z"/>'),
        "blurb": "Incidents and catalogue requests.",
        "category": "Engineering",
        "auth_modes": [
            _mode("basic", "Username & password", [
                _f("instance_url", "Instance URL",
                   placeholder="https://your-instance.service-now.com"),
                _f("username", "Username", env="SERVICENOW_USER"),
                _f("password", "Password", secret=True, env="SERVICENOW_PASSWORD"),
            ]),
            _mode("oauth", "OAuth 2.0", [
                _f("instance_url", "Instance URL",
                   placeholder="https://your-instance.service-now.com"),
                _f("client_id", "Client ID", env="SERVICENOW_CLIENT_ID"),
                _f("client_secret", "Client secret", secret=True,
                   env="SERVICENOW_CLIENT_SECRET"),
                _f("username", "Username", required=False, env="SERVICENOW_USER",
                   help="With password below, uses the password grant. Leave "
                        "both blank for client_credentials instead."),
                _f("password", "Password", secret=True, required=False,
                   env="SERVICENOW_PASSWORD"),
            ]),
        ],
        "setup": "Basic: a service account with the roles the tools need. OAuth "
                 "2.0: System OAuth → Application Registry in the instance.",
    },
    "sharepoint": {
        "label": "SharePoint",
        "icon": _icon('<path d="M20 20a2 2 0 0 0 2-2V8.5a2 2 0 0 0-2-2h-7.4a2 2 0 0 1-1.7-.9'
                      'l-.8-1.2a2 2 0 0 0-1.7-.9H4a2 2 0 0 0-2 2V18a2 2 0 0 0 2 2z"/>'),
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
        "icon": _icon('<ellipse cx="12" cy="5.5" rx="8.5" ry="3"/>'
                      '<path d="M3.5 5.5v13c0 1.7 3.8 3 8.5 3s8.5-1.3 8.5-3v-13"/>'
                      '<path d="M3.5 12c0 1.7 3.8 3 8.5 3s8.5-1.3 8.5-3"/>'),
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
        "icon": _icon('<path d="m12 2.5 8.5 4.4v9.2L12 20.5 3.5 16.1V6.9z"/>'
                      '<path d="M12 11.4v9.1"/><path d="m20.5 6.9-8.5 4.5-8.5-4.5"/>'),
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
        "icon": _icon('<rect x="3" y="8" width="18" height="12.5" rx="2.5"/>'
                      '<path d="M12 8V4.5"/><circle cx="12" cy="3.2" r="1.3"/>'
                      '<path d="M8.8 13.5v1.6"/><path d="M15.2 13.5v1.6"/>'),
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
        "icon": _icon('<circle cx="12" cy="12" r="9.5"/><path d="M2.5 12h19"/>'
                      '<path d="M12 2.5a14.5 14.5 0 0 1 3.8 9.5A14.5 14.5 0 0 1 12 21.5'
                      'a14.5 14.5 0 0 1-3.8-9.5A14.5 14.5 0 0 1 12 2.5z"/>'),
        "blurb": "Call any REST API — yours, or a public one.",
        "category": "Custom",
        # This connector's mode IS a real config value (`auth:`), not just
        # inferred from which fields are filled in — the picker writes it
        # explicitly so the mode chosen here is exactly what the connector
        # switches on at runtime.
        "mode_field": "auth",
        "auth_modes": [
            _mode("none", "No authentication", [
                _f("base_url", "Base URL", placeholder="https://api.example.com"),
            ]),
            _mode("bearer", "Bearer token", [
                _f("base_url", "Base URL", placeholder="https://api.example.com"),
                # No fixed env-var convention to suggest — this is *someone
                # else's* API, whose secret naming roscoe has no way to know.
                _f("token", "Token", secret=True, required=False),
            ]),
            _mode("api_key", "API key header", [
                _f("base_url", "Base URL", placeholder="https://api.example.com"),
                _f("api_key", "API key", secret=True, required=False),
                _f("header", "Key header", required=False, default="X-API-Key",
                   help="Which header carries the API key."),
            ]),
            _mode("basic", "Username & password", [
                _f("base_url", "Base URL", placeholder="https://api.example.com"),
                _f("username", "Username", required=False),
                _f("password", "Password", secret=True, required=False),
            ]),
            _mode("oauth", "OAuth 2.0", [
                _f("base_url", "Base URL", placeholder="https://api.example.com"),
                _f("token_url", "Token URL", placeholder="https://auth.example.com/oauth/token"),
            ]),
        ],
        "setup": "Public APIs usually need nothing but the base URL. OAuth also "
                 "needs 'token_form' set by hand in agent_config.yaml — the "
                 "token exchange shape varies too much per API to offer as "
                 "fields here. See the connector's own docs for the exact YAML.",
    },
}


def describe(type_name: str) -> dict[str, Any] | None:
    """How to present one connector type, or ``None`` if it isn't catalogued."""
    return CATALOG.get(type_name)


#: Official brand colours for the marks drawn by :func:`_logo` (Simple Icons,
#: CC0). Keyed on connector type; a connector using a generic :func:`_icon`
#: has no entry and inherits the UI's own colour instead.
#:
#: The second value is what to use on a dark background. It is the same colour
#: for everything except the two brands whose mark is essentially black, which
#: would otherwise disappear entirely against a dark panel.
_BRAND: dict[str, tuple[str, str]] = {
    "slack": ("#4A154B", "#C9A0CB"),
    "twilio": ("#F22F46", "#F22F46"),
    "telegram": ("#26A5E4", "#26A5E4"),
    "google_workspace": ("#EA4335", "#EA4335"),
    "gmail": ("#EA4335", "#EA4335"),
    "google_calendar": ("#4285F4", "#8AB4F8"),
    "google_tasks": ("#2684FC", "#8AB4F8"),
    "google_drive": ("#4285F4", "#8AB4F8"),
    "github": ("#181717", "#E6EDF3"),
    "jira": ("#0052CC", "#4C9AFF"),
    "notion": ("#000000", "#E6E6E6"),
    "snowflake": ("#29B5E8", "#29B5E8"),
    "ticktick": ("#4772FA", "#7B9BFF"),
}


def catalog() -> list[dict[str, Any]]:
    """Every catalogued connector, grouped-ready and sorted for display."""
    out = []
    for name, spec in sorted(
        CATALOG.items(), key=lambda kv: (kv[1]["category"], kv[1]["label"])
    ):
        entry: dict[str, Any] = {"type": name, **spec}
        brand = _BRAND.get(name)
        if brand:
            entry["color"], entry["color_dark"] = brand
        out.append(entry)
    return out
