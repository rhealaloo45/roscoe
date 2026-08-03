"""What each connector looks like once roscoe isn't there to run it.

An exported file can't import roscoe's connectors — they're built on
``BaseConnector`` and ``StructuredTool``. But most of what those classes do is
build one HTTP request: a base URL, some auth headers, a path, a payload. That
part translates directly, so each connector here contributes two things.

``settings`` runs at export time and turns the connector's YAML into the plain
dict the generated file carries — resolving anything that was a decision rather
than a value (which base URL a search provider uses, which header its key goes
in) so the generated code doesn't have to re-derive it.

``code`` is the source of the tool functions themselves, emitted verbatim into
the file, and only for the connector types the workflow actually uses — an agent
that searches the web shouldn't ship a Jira client it never calls. Each function
takes ``(cfg, args)`` and is registered as ``_{type}_{tool}``, which is the
convention the generated dispatch table is built from.

The OAuth connectors work too. Google's refresh-token flow and Microsoft's
client-credentials flow are both a form-encoded POST returning a token and a
lifetime, so the generated file mints and caches its own — see ``_oauth_token``
in the template. Google's *service-account* mode is the exception: it signs a
JWT with RSA, which needs a crypto library an exported file can't assume is
there, so that mode alone is refused with a pointer at ``roscoe google-auth``.

What's still missing is missing on purpose. Snowflake and the non-SQLite
database drivers need a package installed on the far side, which is exactly the
assumption an export exists to avoid.
"""

from __future__ import annotations

from typing import Any, Callable

# --------------------------------------------------------------------------
# Web search
# --------------------------------------------------------------------------

#: Provider -> (base url, the header its key travels in).
_SEARCH_PROVIDERS = {
    "tavily": ("https://api.tavily.com", "Authorization"),
    "brave": ("https://api.search.brave.com", "X-Subscription-Token"),
    "serper": ("https://google.serper.dev", "X-API-KEY"),
}


def _web_search_settings(spec: dict[str, Any]) -> dict[str, Any]:
    provider = str(spec.get("provider") or "tavily").lower()
    if provider not in _SEARCH_PROVIDERS:
        raise ValueError(
            f"web_search provider '{provider}' isn't one roscoe knows. "
            f"Use one of: {', '.join(sorted(_SEARCH_PROVIDERS))}."
        )
    base_url, header = _SEARCH_PROVIDERS[provider]
    settings: dict[str, Any] = {"provider": provider, "base_url": base_url}
    key = spec.get("api_key", "")
    if header == "Authorization":
        # Tavily takes a bearer token; the others take the key bare.
        settings.update(auth="bearer", token=key,
                        headers={"Content-Type": "application/json"})
    else:
        settings.update(auth="api_key", header=header, api_key=key)
    return settings


_WEB_SEARCH_CODE = '''
def _web_search_search(cfg, args):
    """Search the web. Returns {"query", "results": [{title, url, snippet}]}."""
    query = args.get("query", "")
    limit = int(args.get("max_results", 5) or 5)
    provider = cfg.get("provider", "tavily")

    if provider == "tavily":
        raw = _http(cfg, "POST", "/search",
                    json_body={"query": query, "max_results": limit})
        items = raw.get("results", [])
        results = [{"title": i.get("title", ""), "url": i.get("url", ""),
                    "snippet": i.get("content", "")} for i in items]
    elif provider == "brave":
        raw = _http(cfg, "GET", "/res/v1/web/search",
                    params={"q": query, "count": limit})
        items = (raw.get("web") or {}).get("results", [])
        results = [{"title": i.get("title", ""), "url": i.get("url", ""),
                    "snippet": i.get("description", "")} for i in items]
    else:  # serper
        raw = _http(cfg, "POST", "/search", json_body={"q": query, "num": limit})
        items = raw.get("organic", [])
        results = [{"title": i.get("title", ""), "url": i.get("link", ""),
                    "snippet": i.get("snippet", "")} for i in items]

    # Trimmed here as well as asked for: not every provider honours the limit.
    return {"query": query, "results": results[:limit]}
'''


# --------------------------------------------------------------------------
# Email (SMTP)
# --------------------------------------------------------------------------

def _smtp_settings(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "host": spec.get("host", ""),
        "port": spec.get("port", 587),
        "username": spec.get("username", ""),
        "password": spec.get("password", ""),
        "from": spec.get("from", ""),
    }


_SMTP_CODE = '''
def _smtp_send_email(cfg, args):
    """Send a plain-text email. `to` and `cc` may be comma-separated."""
    import smtplib
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = cfg.get("from") or cfg.get("username", "")
    message["To"] = args.get("to", "")
    if args.get("cc"):
        message["Cc"] = args["cc"]
    message["Subject"] = args.get("subject", "")
    message.set_content(args.get("body", ""))

    host = cfg.get("host", "")
    port = int(cfg.get("port", 587) or 587)
    user, password = cfg.get("username", ""), cfg.get("password", "")

    if port == 465:
        # Already encrypted — calling starttls on this is an error.
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if port != 25:
                server.starttls()
                server.ehlo()
            server.login(user, password)
            server.send_message(message)

    # SMTP hands back no message id, so report what was sent instead.
    return {"sent": True, "to": args.get("to", ""), "subject": args.get("subject", "")}
'''


# --------------------------------------------------------------------------
# SMS (Twilio)
# --------------------------------------------------------------------------

def _twilio_settings(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_url": "https://api.twilio.com",
        "auth": "basic",
        "username": spec.get("account_sid", ""),
        "password": spec.get("auth_token", ""),
        "account_sid": spec.get("account_sid", ""),
        "from": spec.get("from", ""),
    }


_TWILIO_CODE = '''
def _twilio_send_sms(cfg, args):
    """Send an SMS. `to` is E.164, e.g. +447700900123."""
    sender = cfg.get("from")
    if not sender:
        raise RuntimeError(
            "twilio: no 'from' number configured, so there is nothing to send "
            "this message from.")
    sid = cfg.get("account_sid", "")
    # Twilio's REST API takes form-encoded bodies, not JSON.
    return _http(cfg, "POST", "/2010-04-01/Accounts/" + sid + "/Messages.json",
                 data={"To": args.get("to", ""), "From": sender,
                       "Body": args.get("body", "")})
'''


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------

def _github_settings(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_url": spec.get("base_url") or "https://api.github.com",
        "auth": "bearer",
        "token": spec.get("token", ""),
        "headers": {"Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28"},
    }


_GITHUB_CODE = '''
def _github_get_issue(cfg, args):
    """Fetch an issue by number from a repo (repo = 'owner/name')."""
    return _http(cfg, "GET",
                 "/repos/" + args["repo"] + "/issues/" + str(args["number"]))


def _github_create_issue(cfg, args):
    """Open a new issue in a repo."""
    return _http(cfg, "POST", "/repos/" + args["repo"] + "/issues",
                 json_body={"title": args.get("title", ""),
                            "body": args.get("body", "")})


def _github_search_issues(cfg, args):
    """Search issues and PRs across GitHub with a search query."""
    return _http(cfg, "GET", "/search/issues", params={"q": args.get("query", "")})


def _github_add_comment(cfg, args):
    """Add a comment to an issue or pull request."""
    return _http(cfg, "POST",
                 "/repos/" + args["repo"] + "/issues/" + str(args["number"]) + "/comments",
                 json_body={"body": args.get("body", "")})


def _github_get_file(cfg, args):
    """Get the contents metadata of a file at a path on a branch/ref."""
    return _http(cfg, "GET", "/repos/" + args["repo"] + "/contents/" + args["path"],
                 params={"ref": args.get("ref", "main")})


def _github_list_repos(cfg, args):
    """List repos for an org, or the authenticated user if org is empty."""
    org = args.get("org", "")
    return _http(cfg, "GET", ("/orgs/" + org + "/repos") if org else "/user/repos")
'''


# --------------------------------------------------------------------------
# Jira
# --------------------------------------------------------------------------

def _jira_settings(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_url": str(spec.get("base_url", "")).rstrip("/"),
        "auth": "basic",
        "username": spec.get("email", ""),
        "password": spec.get("api_token", ""),
        "headers": {"Accept": "application/json", "Content-Type": "application/json"},
    }


_JIRA_CODE = '''
def _jira_adf(text):
    """Wrap plain text in Atlassian Document Format, which Jira v3 requires."""
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": text or " "}]}]}


def _jira_create_issue(cfg, args):
    """Create a Jira issue and return its key and id."""
    return _http(cfg, "POST", "/rest/api/3/issue", json_body={"fields": {
        "project": {"key": args.get("project_key", "")},
        "summary": args.get("summary", ""),
        "description": _jira_adf(args.get("description", "")),
        "issuetype": {"name": args.get("issue_type", "Task")}}})


def _jira_get_issue(cfg, args):
    """Fetch a Jira issue by key (e.g. PROJ-123)."""
    return _http(cfg, "GET", "/rest/api/3/issue/" + args["issue_key"])


def _jira_update_issue(cfg, args):
    """Update fields on a Jira issue."""
    return _http(cfg, "PUT", "/rest/api/3/issue/" + args["issue_key"],
                 json_body={"fields": args.get("fields") or {}})


def _jira_search_issues(cfg, args):
    """Search Jira issues with a JQL query."""
    return _http(cfg, "POST", "/rest/api/3/search",
                 json_body={"jql": args.get("jql", ""),
                            "maxResults": int(args.get("max_results", 20) or 20)})


def _jira_add_comment(cfg, args):
    """Add a comment to a Jira issue."""
    return _http(cfg, "POST", "/rest/api/3/issue/" + args["issue_key"] + "/comment",
                 json_body={"body": _jira_adf(args.get("body", ""))})
'''


# --------------------------------------------------------------------------
# ServiceNow
# --------------------------------------------------------------------------

def _servicenow_settings(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_url": str(spec.get("instance_url", "")).rstrip("/"),
        "auth": "basic",
        "username": spec.get("username", ""),
        "password": spec.get("password", ""),
        "headers": {"Accept": "application/json", "Content-Type": "application/json"},
    }


_SERVICENOW_CODE = '''
def _servicenow_create_ticket(cfg, args):
    """Create a ServiceNow incident and return its number and sys_id."""
    return _http(cfg, "POST", "/api/now/table/incident", json_body={
        "short_description": args.get("short_description", ""),
        "description": args.get("description", ""),
        "urgency": str(args.get("urgency", "3"))})


def _servicenow_update_ticket(cfg, args):
    """Update fields on a ServiceNow incident by sys_id."""
    return _http(cfg, "PATCH", "/api/now/table/incident/" + args["sys_id"],
                 json_body=args.get("fields") or {})


def _servicenow_get_ticket_status(cfg, args):
    """Look up an incident by its number (e.g. INC0010001)."""
    return _http(cfg, "GET", "/api/now/table/incident", params={
        "sysparm_query": "number=" + args.get("number", ""), "sysparm_limit": 1})


def _servicenow_search_kb(cfg, args):
    """Search the ServiceNow knowledge base."""
    return _http(cfg, "GET", "/api/now/table/kb_knowledge", params={
        "sysparm_query": "short_descriptionLIKE" + args.get("query", ""),
        "sysparm_limit": int(args.get("limit", 5) or 5)})
'''


# --------------------------------------------------------------------------
# Notion
# --------------------------------------------------------------------------

def _notion_settings(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_url": "https://api.notion.com/v1",
        "auth": "bearer",
        "token": spec.get("token", ""),
        "headers": {"Notion-Version": spec.get("version") or "2022-06-28",
                    "Content-Type": "application/json"},
    }


_NOTION_CODE = '''
def _notion_search(cfg, args):
    """Search Notion pages and databases the integration can access."""
    return _http(cfg, "POST", "/search", json_body={"query": args.get("query", "")})


def _notion_get_page(cfg, args):
    """Retrieve a Notion page's properties by id."""
    return _http(cfg, "GET", "/pages/" + args["page_id"])


def _notion_create_page(cfg, args):
    """Create a page in a database with the given title."""
    return _http(cfg, "POST", "/pages", json_body={
        "parent": {"database_id": args.get("parent_database_id", "")},
        "properties": {"title": {"title": [
            {"text": {"content": args.get("title", "")}}]}}})


def _notion_query_database(cfg, args):
    """Query rows of a Notion database."""
    return _http(cfg, "POST", "/databases/" + args["database_id"] + "/query",
                 json_body={"page_size": int(args.get("page_size", 20) or 20)})


def _notion_append_block(cfg, args):
    """Append a paragraph block of text to a page or block."""
    return _http(cfg, "PATCH", "/blocks/" + args["block_id"] + "/children", json_body={
        "children": [{"object": "block", "type": "paragraph", "paragraph": {
            "rich_text": [{"type": "text",
                           "text": {"content": args.get("text", "")}}]}}]})
'''


# --------------------------------------------------------------------------
# TickTick
# --------------------------------------------------------------------------

def _ticktick_settings(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_url": spec.get("base_url") or "https://api.ticktick.com/open/v1",
        "auth": "bearer",
        "token": spec.get("token", ""),
        "headers": {"Content-Type": "application/json"},
        "default_project_id": spec.get("default_project_id", ""),
    }


_TICKTICK_CODE = '''
#: TickTick's priorities are a sparse scale, not 0-4.
_TICKTICK_PRIORITIES = {"none": 0, "low": 1, "medium": 3, "high": 5}


def _ticktick_project(cfg, given):
    resolved = given or cfg.get("default_project_id")
    if not resolved:
        raise RuntimeError(
            "ticktick: no project_id given and no 'default_project_id' configured. "
            "Call list_projects to find one.")
    return str(resolved)


def _ticktick_payload(cfg, item):
    priority = item.get("priority", "none")
    if priority not in _TICKTICK_PRIORITIES:
        raise RuntimeError(
            "Unknown priority " + repr(priority) + ". Use one of: "
            + ", ".join(_TICKTICK_PRIORITIES) + ".")
    payload = {"title": item.get("title", ""),
               "projectId": _ticktick_project(cfg, item.get("project_id")),
               "priority": _TICKTICK_PRIORITIES[priority]}
    if item.get("content"):
        payload["content"] = item["content"]
    if item.get("due_date"):
        payload["dueDate"] = item["due_date"]
        payload["isAllDay"] = item.get("is_all_day", True)
    if item.get("checklist"):
        payload["kind"] = "CHECKLIST"
        payload["items"] = [{"title": t, "status": 0} for t in item["checklist"]]
    return payload


def _ticktick_list_projects(cfg, args):
    """List TickTick projects (lists), with their ids."""
    return _http(cfg, "GET", "/project")


def _ticktick_list_project_tasks(cfg, args):
    """List the undone tasks in a TickTick project."""
    return _http(cfg, "GET", "/project/" + args["project_id"] + "/data")


def _ticktick_get_task(cfg, args):
    """Retrieve one TickTick task by id."""
    return _http(cfg, "GET",
                 "/project/" + args["project_id"] + "/task/" + args["task_id"])


def _ticktick_create_task(cfg, args):
    """Create a TickTick task. priority is none/low/medium/high."""
    return _http(cfg, "POST", "/task", json_body=_ticktick_payload(cfg, args))


def _ticktick_create_tasks_batch(cfg, args):
    """Create several TickTick tasks in one call."""
    created = [_http(cfg, "POST", "/task", json_body=_ticktick_payload(cfg, t))
               for t in (args.get("tasks") or [])]
    return {"created": len(created), "tasks": created}


def _ticktick_complete_task(cfg, args):
    """Mark a TickTick task as complete."""
    return _http(cfg, "POST",
                 "/project/" + args["project_id"] + "/task/" + args["task_id"]
                 + "/complete")
'''


# --------------------------------------------------------------------------
# Database (SQLite)
# --------------------------------------------------------------------------

def _database_settings(spec: dict[str, Any]) -> dict[str, Any]:
    driver = str(spec.get("driver") or "sqlite").lower()
    if driver not in ("sqlite", "sqlite3"):
        raise ValueError(
            f"database driver '{driver}' needs a package the exported file can't "
            f"assume is installed. Only the built-in sqlite driver exports."
        )
    return {
        "path": spec.get("path") or spec.get("database") or "",
        "schema": spec.get("schema") or "",
        # Defaults to read-only here exactly as it does in roscoe: an export
        # shouldn't quietly become more permissive than what you tested.
        "read_only": bool(spec.get("read_only", True)),
    }


_DATABASE_CODE = '''
#: Max rows any tool returns, so a wide table can't flood a prompt.
_DB_MAX_ROWS = 100
_DB_READ_PREFIXES = ("select", "with", "pragma", "explain")
_DB_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Kept open between calls, so ':memory:' survives more than one step.
_DB_CONNECTIONS = {}


def _db_connect(cfg):
    import sqlite3

    path = str(cfg.get("path") or ":memory:")
    conn = _DB_CONNECTIONS.get(path)
    if conn is not None:
        return conn

    # Decide before connecting: sqlite3.connect() creates the file itself.
    is_new = (path == ":memory:" or not os.path.exists(path)
              or os.path.getsize(path) == 0)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if is_new and cfg.get("schema") and os.path.exists(cfg["schema"]):
        with open(cfg["schema"], encoding="utf-8") as handle:
            conn.executescript(handle.read())
        conn.commit()
    _DB_CONNECTIONS[path] = conn
    return conn


def _db_rows(cursor):
    if cursor.description is None:
        return []
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchmany(_DB_MAX_ROWS)]


def _db_one_statement(sql):
    """Reject anything with a second statement hiding behind a semicolon."""
    stripped = (sql or "").strip().rstrip(";").strip()
    if ";" in stripped:
        raise RuntimeError(
            "Only one statement may be run at a time; split the work into "
            "separate calls.")
    if not stripped:
        raise RuntimeError("Empty SQL statement.")
    return stripped


def _database_query(cfg, args):
    """Run a read-only SQL query and return up to 100 rows."""
    statement = _db_one_statement(args.get("sql", ""))
    if not statement.lower().startswith(_DB_READ_PREFIXES):
        raise RuntimeError("query only runs read statements. Use execute for writes.")
    cursor = _db_connect(cfg).cursor()
    cursor.execute(statement, tuple(args.get("params") or ()))
    return _db_rows(cursor)


def _database_execute(cfg, args):
    """Run a single INSERT, UPDATE or DELETE. Returns the rows affected."""
    if cfg.get("read_only", True):
        raise RuntimeError(
            "This database is read-only. Set 'read_only: false' on the connector "
            "to allow writes.")
    statement = _db_one_statement(args.get("sql", ""))
    conn = _db_connect(cfg)
    cursor = conn.cursor()
    cursor.execute(statement, tuple(args.get("params") or ()))
    conn.commit()
    return {"rows_affected": cursor.rowcount}


def _database_list_tables(cfg, args):
    """List the tables in this database."""
    cursor = _db_connect(cfg).cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' "
                   "AND name NOT LIKE 'sqlite_%'")
    return _db_rows(cursor)


def _database_describe_table(cfg, args):
    """Show the column names and types of a table."""
    name = args.get("table", "")
    # Identifiers can't be bound as parameters, so they're checked instead.
    if not _DB_SAFE_IDENTIFIER.match(name or ""):
        raise RuntimeError(
            repr(name) + " is not a valid table name (letters, digits and "
            "underscores only).")
    cursor = _db_connect(cfg).cursor()
    cursor.execute("PRAGMA table_info(" + name + ")")
    return _db_rows(cursor)
'''


# --------------------------------------------------------------------------
# Google Workspace
# --------------------------------------------------------------------------

_GOOGLE_OAUTH_KEYS = ("client_id", "client_secret", "refresh_token")


def _google_workspace_settings(spec: dict[str, Any]) -> dict[str, Any]:
    if not all(spec.get(key) for key in _GOOGLE_OAUTH_KEYS):
        raise ValueError(
            "Google Workspace exports in refresh-token mode only, which needs "
            f"{', '.join(_GOOGLE_OAUTH_KEYS)}. The service-account mode signs a JWT "
            "with RSA, and an exported file can't assume a crypto library is "
            "installed. Run `roscoe google-auth` to mint a refresh token."
        )
    return {
        # Gmail's host; the other Google APIs live elsewhere, so those tools pass
        # absolute URLs instead.
        "base_url": "https://gmail.googleapis.com",
        "auth": "oauth",
        "token_url": "https://oauth2.googleapis.com/token",
        "token_form": {
            "client_id": spec.get("client_id", ""),
            "client_secret": spec.get("client_secret", ""),
            "refresh_token": spec.get("refresh_token", ""),
            "grant_type": "refresh_token",
        },
        "subject": spec.get("subject", ""),
        "headers": {"Accept": "application/json"},
    }


_GOOGLE_WORKSPACE_CODE = '''
_GMAIL = "https://gmail.googleapis.com"
_GCALENDAR = "https://www.googleapis.com/calendar/v3"
_GTASKS = "https://tasks.googleapis.com/tasks/v1"
_GDRIVE = "https://www.googleapis.com/drive/v3"


def _google_user(cfg):
    """The mailbox to act on — 'me' is whoever the refresh token belongs to."""
    return cfg.get("subject") or "me"


def _google_workspace_send_email(cfg, args):
    """Send an email via Gmail from the configured account."""
    raw = ("To: " + args.get("to", "") + "\\r\\n"
           "Subject: " + args.get("subject", "") + "\\r\\n"
           "Content-Type: text/plain; charset=utf-8\\r\\n\\r\\n"
           + args.get("body", ""))
    return _http(cfg, "POST",
                 _GMAIL + "/gmail/v1/users/" + _google_user(cfg) + "/messages/send",
                 json_body={"raw": base64.urlsafe_b64encode(raw.encode()).decode()})


def _google_workspace_read_emails(cfg, args):
    """Recent inbox emails — sender, subject and snippet for each."""
    user = _google_user(cfg)
    listing = _http(cfg, "GET", _GMAIL + "/gmail/v1/users/" + user + "/messages",
                    params={"maxResults": int(args.get("max_results", 10) or 10),
                            "labelIds": "INBOX"})
    emails = []
    # messages.list returns bare ids, so each one is fetched for its metadata —
    # a digest has nothing to summarise otherwise.
    for stub in listing.get("messages", []):
        message = _http(
            cfg, "GET",
            _GMAIL + "/gmail/v1/users/" + user + "/messages/" + stub["id"],
            params={"format": "metadata", "metadataHeaders": ["Subject", "From"]})
        headers = {h["name"]: h["value"]
                   for h in (message.get("payload") or {}).get("headers", [])}
        emails.append({"id": message.get("id"),
                       "from": headers.get("From", ""),
                       "subject": headers.get("Subject", ""),
                       "snippet": message.get("snippet", "")})
    return {"emails": emails}


def _google_workspace_list_events(cfg, args):
    """List upcoming events from Google Calendar."""
    return _http(cfg, "GET", _GCALENDAR + "/calendars/primary/events", params={
        "maxResults": int(args.get("max_results", 10) or 10),
        "singleEvents": True,
        "orderBy": "startTime",
        "timeMin": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def _google_workspace_create_event(cfg, args):
    """Create a Google Calendar event. start/end are ISO 8601 datetimes (UTC)."""
    payload = {"summary": args.get("summary", ""),
               "start": {"dateTime": args.get("start"), "timeZone": "UTC"},
               "end": {"dateTime": args.get("end"), "timeZone": "UTC"}}
    if args.get("attendees"):
        payload["attendees"] = [{"email": a} for a in args["attendees"]]
    return _http(cfg, "POST", _GCALENDAR + "/calendars/primary/events",
                 json_body=payload)


def _google_workspace_list_tasks(cfg, args):
    """List tasks from Google Tasks."""
    return _http(cfg, "GET",
                 _GTASKS + "/lists/" + args.get("tasklist", "@default") + "/tasks",
                 params={"maxResults": int(args.get("max_results", 20) or 20)})


def _google_workspace_create_task(cfg, args):
    """Create a new task in Google Tasks."""
    return _http(cfg, "POST",
                 _GTASKS + "/lists/" + args.get("tasklist", "@default") + "/tasks",
                 json_body={"title": args.get("title", ""),
                            "notes": args.get("notes", "")})


def _google_workspace_search_drive(cfg, args):
    """Search Drive. Query uses Drive syntax, e.g. name contains 'report'."""
    return _http(cfg, "GET", _GDRIVE + "/files", params={
        "q": args.get("query", ""),
        "pageSize": int(args.get("max_results", 10) or 10),
        "fields": "files(id,name,mimeType,webViewLink,modifiedTime)"})


def _google_workspace_read_drive_file(cfg, args):
    """Read a Drive file's text. Google Docs are exported as plain text."""
    file_id = args["file_id"]
    limit = int(args.get("max_chars", 100000) or 100000)

    meta = _http(cfg, "GET", _GDRIVE + "/files/" + file_id,
                 params={"fields": "id,name,mimeType"})
    mime = meta.get("mimeType", "")
    if mime.startswith("application/vnd.google-apps."):
        # Google-native files have no bytes to download — they have to be
        # exported to a format that does.
        body = _http(cfg, "GET", _GDRIVE + "/files/" + file_id + "/export",
                     params={"mimeType": "text/plain"})
    else:
        body = _http(cfg, "GET", _GDRIVE + "/files/" + file_id,
                     params={"alt": "media"})

    text = body.get("text", "") if isinstance(body, dict) else str(body)
    return {"id": meta.get("id", file_id), "name": meta.get("name"),
            "mime_type": mime,
            # A long document can run past any model's context. Truncate here
            # rather than failing three nodes later on a token limit.
            "truncated": len(text) > limit,
            "text": text[:limit]}
'''


# --------------------------------------------------------------------------
# Microsoft Graph — Outlook and SharePoint
# --------------------------------------------------------------------------

_GRAPH_KEYS = ("client_id", "client_secret", "tenant_id")


def _graph_settings(spec: dict[str, Any], label: str, extra: dict[str, Any]) -> dict[str, Any]:
    for key in _GRAPH_KEYS + tuple(extra):
        if not spec.get(key):
            raise ValueError(f"{label} needs '{key}' to authenticate.")
    return {
        "base_url": "https://graph.microsoft.com/v1.0",
        "auth": "oauth",
        # {tenant} is filled in at run time from tenant_id, which is usually an
        # environment placeholder and so isn't known when this is written.
        "token_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "tenant_id": spec.get("tenant_id", ""),
        "token_form": {
            "grant_type": "client_credentials",
            "client_id": spec.get("client_id", ""),
            "client_secret": spec.get("client_secret", ""),
            "scope": "https://graph.microsoft.com/.default",
        },
        "headers": {"Accept": "application/json"},
        **{key: spec.get(key, "") for key in extra},
    }


def _outlook_settings(spec: dict[str, Any]) -> dict[str, Any]:
    return _graph_settings(spec, "Outlook", {"mailbox": ""})


def _sharepoint_settings(spec: dict[str, Any]) -> dict[str, Any]:
    return _graph_settings(spec, "SharePoint", {"site_id": ""})


_OUTLOOK_CODE = '''
def _outlook_send_email(cfg, args):
    """Send an email from the configured mailbox."""
    return _http(cfg, "POST", "/users/" + cfg.get("mailbox", "") + "/sendMail",
                 json_body={"message": {
                     "subject": args.get("subject", ""),
                     "body": {"contentType": "Text", "content": args.get("body", "")},
                     "toRecipients": [
                         {"emailAddress": {"address": args.get("to", "")}}]}})


def _outlook_read_emails(cfg, args):
    """Read the most recent emails in the mailbox inbox."""
    return _http(cfg, "GET", "/users/" + cfg.get("mailbox", "") + "/messages",
                 params={"$top": int(args.get("top", 10) or 10)})


def _outlook_create_calendar_event(cfg, args):
    """Create a calendar event. start/end are ISO 8601 datetimes (UTC)."""
    payload = {"subject": args.get("subject", ""),
               "start": {"dateTime": args.get("start"), "timeZone": "UTC"},
               "end": {"dateTime": args.get("end"), "timeZone": "UTC"}}
    if args.get("attendees"):
        payload["attendees"] = [
            {"emailAddress": {"address": a}, "type": "required"}
            for a in args["attendees"]]
    return _http(cfg, "POST", "/users/" + cfg.get("mailbox", "") + "/events",
                 json_body=payload)


def _outlook_get_availability(cfg, args):
    """Free/busy availability for the given mailboxes over a time window."""
    return _http(cfg, "POST",
                 "/users/" + cfg.get("mailbox", "") + "/calendar/getSchedule",
                 json_body={"schedules": args.get("emails") or [],
                            "startTime": {"dateTime": args.get("start"),
                                          "timeZone": "UTC"},
                            "endTime": {"dateTime": args.get("end"),
                                        "timeZone": "UTC"},
                            "availabilityViewInterval": 60})
'''


_SHAREPOINT_CODE = '''
def _sharepoint_drive(cfg):
    return "/sites/" + cfg.get("site_id", "") + "/drive"


def _sharepoint_search_documents(cfg, args):
    """Search documents in the site's document library."""
    return _http(cfg, "GET",
                 _sharepoint_drive(cfg) + "/root/search(q='"
                 + args.get("query", "") + "')")


def _sharepoint_get_document(cfg, args):
    """Get a document's metadata by drive item id."""
    return _http(cfg, "GET", _sharepoint_drive(cfg) + "/items/" + args["item_id"])


def _sharepoint_list_files(cfg, args):
    """List files in a folder (root if no path given)."""
    folder = args.get("folder_path", "")
    if folder:
        return _http(cfg, "GET",
                     _sharepoint_drive(cfg) + "/root:/" + folder + ":/children")
    return _http(cfg, "GET", _sharepoint_drive(cfg) + "/root/children")


def _sharepoint_upload_file(cfg, args):
    """Upload a small text file to the library root."""
    return _http(cfg, "PUT",
                 _sharepoint_drive(cfg) + "/root:/" + args.get("name", "") + ":/content",
                 content=args.get("content", "").encode("utf-8"),
                 extra_headers={"Content-Type": "text/plain"})
'''


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------

#: Connector type -> what export needs to know about it.
#:
#: ``tools`` is the tool names the type answers to, checked at export time so a
#: typo is caught in the editor rather than at 3am in someone else's service.
SNIPPETS: dict[str, dict[str, Any]] = {
    "web_search": {
        "label": "web search",
        "settings": _web_search_settings,
        "code": _WEB_SEARCH_CODE,
        "tools": ("search",),
    },
    "smtp": {
        "label": "email (SMTP)",
        "settings": _smtp_settings,
        "code": _SMTP_CODE,
        "tools": ("send_email",),
    },
    "twilio": {
        "label": "SMS (Twilio)",
        "settings": _twilio_settings,
        "code": _TWILIO_CODE,
        "tools": ("send_sms",),
    },
    "github": {
        "label": "GitHub",
        "settings": _github_settings,
        "code": _GITHUB_CODE,
        "tools": ("get_issue", "create_issue", "search_issues", "add_comment",
                  "get_file", "list_repos"),
    },
    "jira": {
        "label": "Jira",
        "settings": _jira_settings,
        "code": _JIRA_CODE,
        "tools": ("create_issue", "get_issue", "update_issue", "search_issues",
                  "add_comment"),
    },
    "servicenow": {
        "label": "ServiceNow",
        "settings": _servicenow_settings,
        "code": _SERVICENOW_CODE,
        "tools": ("create_ticket", "update_ticket", "get_ticket_status", "search_kb"),
    },
    "notion": {
        "label": "Notion",
        "settings": _notion_settings,
        "code": _NOTION_CODE,
        "tools": ("search", "get_page", "create_page", "query_database",
                  "append_block"),
    },
    "ticktick": {
        "label": "TickTick",
        "settings": _ticktick_settings,
        "code": _TICKTICK_CODE,
        "tools": ("list_projects", "list_project_tasks", "get_task", "create_task",
                  "create_tasks_batch", "complete_task"),
    },
    "google_workspace": {
        "label": "Google Workspace",
        "settings": _google_workspace_settings,
        "code": _GOOGLE_WORKSPACE_CODE,
        "tools": ("send_email", "read_emails", "list_events", "create_event",
                  "list_tasks", "create_task", "search_drive", "read_drive_file"),
    },
    "outlook": {
        "label": "Outlook",
        "settings": _outlook_settings,
        "code": _OUTLOOK_CODE,
        "tools": ("send_email", "read_emails", "create_calendar_event",
                  "get_availability"),
    },
    "sharepoint": {
        "label": "SharePoint",
        "settings": _sharepoint_settings,
        "code": _SHAREPOINT_CODE,
        "tools": ("search_documents", "get_document", "list_files", "upload_file"),
    },
    "database": {
        "label": "SQLite database",
        "settings": _database_settings,
        "code": _DATABASE_CODE,
        "tools": ("query", "execute", "list_tables", "describe_table"),
        # sqlite3 and re are stdlib, but `re` has to be imported at the top of
        # the generated file rather than inside a function that uses it in a
        # module-level constant.
        "imports": ("re",),
    },
}


def settings_for(type_name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """The plain settings dict a generated file carries for one connector."""
    builder: Callable[[dict[str, Any]], dict[str, Any]] = SNIPPETS[type_name]["settings"]
    return builder(spec or {})


def tools_for(type_name: str) -> tuple[str, ...]:
    """Tool names this connector type answers to."""
    return tuple(SNIPPETS[type_name]["tools"])


def labels() -> str:
    """The supported types in plain language, for a refusal message."""
    return ", ".join(spec["label"] for spec in SNIPPETS.values())
