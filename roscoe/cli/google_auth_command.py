"""``roscoe google-auth`` — one-time OAuth2 consent flow for GoogleWorkspaceConnector.

Opens a browser for Google login, exchanges the auth code for a refresh
token, and prints (or appends to ``.env``) the ``GOOGLE_REFRESH_TOKEN``
value the connector's OAuth mode needs.
"""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import click

_SCOPES = " ".join([
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive.readonly",
])


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        query = parse_qs(urlparse(self.path).query)
        _CallbackHandler.code = query.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Done! You can close this tab.</h2>")

    def log_message(self, *args: object) -> None:
        pass


@click.command("google-auth")
@click.option("--client-id", envvar="GOOGLE_CLIENT_ID", required=True)
@click.option("--client-secret", envvar="GOOGLE_CLIENT_SECRET", required=True)
@click.option("--port", default=8090, show_default=True, help="Local callback port.")
@click.option(
    "--env-file",
    default=".env",
    show_default=True,
    help="Append GOOGLE_REFRESH_TOKEN to this file instead of just printing it.",
)
def google_auth_command(client_id: str, client_secret: str, port: int, env_file: str) -> None:
    """Run Google's OAuth2 consent flow and mint a GOOGLE_REFRESH_TOKEN.

    Use this for GoogleWorkspaceConnector's OAuth mode (single-user consent,
    no domain-wide delegation admin rights needed). Requires an OAuth 2.0
    Client ID of type "Desktop app" from https://console.cloud.google.com.
    """
    import httpx

    redirect_uri = f"http://localhost:{port}/callback"
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    })

    click.echo("Opening browser for Google login...")
    click.launch(auth_url)

    server = HTTPServer(("localhost", port), _CallbackHandler)
    server.handle_request()

    if not _CallbackHandler.code:
        raise click.ClickException("No authorization code received.")

    click.echo("Got auth code. Exchanging for tokens...")
    resp = httpx.post("https://oauth2.googleapis.com/token", data={
        "code": _CallbackHandler.code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    resp.raise_for_status()
    refresh_token = resp.json().get("refresh_token")
    if not refresh_token:
        raise click.ClickException(
            "No refresh token received. Revoke prior access at "
            "https://myaccount.google.com/permissions and try again."
        )

    if env_file and os.path.exists(env_file):
        with open(env_file, "a") as f:
            f.write(f"\nGOOGLE_REFRESH_TOKEN={refresh_token}\n")
        click.echo(f"Saved GOOGLE_REFRESH_TOKEN to {env_file}")
    else:
        click.echo(f"\nGOOGLE_REFRESH_TOKEN={refresh_token}")
