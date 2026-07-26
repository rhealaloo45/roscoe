"""``roscoe init`` — scaffold a new agent project.

Blank scaffold (default) copies ``cli/scaffold/`` and substitutes the project name.
``--template`` copies a pre-built template from ``roscoe.templates`` and adds the
project-level files a template doesn't carry (``main.py``, ``.env.example``,
``evals/test_cases.json``). No Jinja2 — a single ``__PROJECT_NAME__`` placeholder is
enough, keeping the dependency surface small.

Interactive wizard (blank projects only, skip with ``--quick``):
    Prompts for provider, model, middleware toggles, and memory. Answers are written
    into ``agent_config.yaml`` with all comments preserved — the user can always edit
    the YAML later.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import click

from roscoe.templates import available_templates, template_path

SCAFFOLD_DIR = Path(__file__).parent / "scaffold"
#: Scaffold for `roscoe init-nc` — YAML workflow instead of tools/my_tools.py.
SCAFFOLD_NC_DIR = Path(__file__).parent / "scaffold_nc"
PLACEHOLDER = "__PROJECT_NAME__"
_RENDER_SUFFIXES = {".yaml", ".yml", ".txt", ".py", ".md", ".json"}

_PROVIDERS = {
    "openai": {"env_key": "OPENAI_API_KEY", "default_model": "gpt-4o-mini", "base_url": None},
    "openrouter": {"env_key": "OPENROUTER_API_KEY", "default_model": "meta-llama/llama-3.1-8b-instruct", "base_url": "https://openrouter.ai/api/v1"},
    "azure_openai": {"env_key": "AZURE_OPENAI_KEY", "default_model": "gpt-4o", "base_url": None},
    "anthropic": {"env_key": "ANTHROPIC_API_KEY", "default_model": "claude-sonnet-4-5", "base_url": None},
    "gemini": {"env_key": "GOOGLE_API_KEY", "default_model": "gemini-1.5-pro", "base_url": None},
    "nvidia": {"env_key": "NVIDIA_API_KEY", "default_model": "openai/gpt-oss-120b", "base_url": None},
    "ollama": {"env_key": None, "default_model": "llama3.1", "base_url": None},
}

# Per-template entry point: how to build that template's tools.
_TEMPLATE_MAIN = {
    "hr_agent": '''"""Entry point for the HR agent. Run: python main.py"""

from roscoe import AgentRunner
from roscoe.connectors import RESTConnector

from tools.hr_tools import build_tools

rest = RESTConnector(
    {"base_url": __import__("os").environ["HR_API_BASE_URL"], "auth": "bearer",
     "token": __import__("os").environ["HR_API_TOKEN"]}
)
agent = AgentRunner.from_config("agent_config.yaml", tools=build_tools(rest))

if __name__ == "__main__":
    print(agent.run("How many leave days does employee E1 have left?").output)
''',
    "it_support_agent": '''"""Entry point for the IT support agent. Run: python main.py"""

import os

from roscoe import AgentRunner
from roscoe.connectors import ServiceNowConnector

from tools.it_tools import build_tools

snow = ServiceNowConnector(
    {"instance_url": os.environ["SERVICENOW_INSTANCE_URL"],
     "username": os.environ["SERVICENOW_USERNAME"],
     "password": os.environ["SERVICENOW_PASSWORD"]}
)
agent = AgentRunner.from_config("agent_config.yaml", tools=build_tools(snow))

if __name__ == "__main__":
    print(agent.run("My laptop won't connect to wifi, please open a ticket.").output)
''',
    "legal_agent": '''"""Entry point for the legal agent. Run: python main.py"""

from roscoe import AgentRunner
from roscoe.memory.knowledge import KnowledgeMemory

from tools.legal_tools import build_tools

# Replace with your real contract texts (and embeddings for semantic search).
contracts = ["Limitation of liability: ... ", "Term and termination: ..."]
knowledge = KnowledgeMemory.from_texts(
    contracts, metadatas=[{"source": "contract.pdf"}, {"source": "contract.pdf"}]
)
agent = AgentRunner.from_config("agent_config.yaml", tools=build_tools(knowledge))

if __name__ == "__main__":
    print(agent.run("What does the liability clause say?").output)
''',
    "knowledge_base_agent": '''"""Entry point for the knowledge-base agent. Run: python main.py"""

import os

from roscoe import AgentRunner
from roscoe.connectors import NotionConnector

from tools.kb_tools import build_tools

# Wire whichever sources you have. Add SharePointConnector / KnowledgeMemory as needed.
notion = NotionConnector({"token": os.environ["NOTION_TOKEN"]})
agent = AgentRunner.from_config("agent_config.yaml", tools=build_tools(notion=notion))

if __name__ == "__main__":
    print(agent.run("What is our remote-work policy?").output)
''',
    "exec_assistant_agent": '''"""Entry point for the executive assistant. Run: python main.py"""

import os

from roscoe import AgentRunner
from roscoe.connectors import OutlookConnector

from tools.exec_tools import build_tools

outlook = OutlookConnector(
    {"client_id": os.environ["MS_CLIENT_ID"],
     "client_secret": os.environ["MS_CLIENT_SECRET"],
     "tenant_id": os.environ["MS_TENANT_ID"],
     "mailbox": os.environ["MS_MAILBOX"]}
)
agent = AgentRunner.from_config("agent_config.yaml", tools=build_tools(outlook))

if __name__ == "__main__":
    print(agent.run("Summarize my unread emails.").output)
''',
    "google_workspace_agent": '''"""Entry point for the Google Workspace agent. Run: python main.py"""

import os

from roscoe import AgentRunner
from roscoe.connectors import GoogleWorkspaceConnector

from tools.gws_tools import build_tools

google = GoogleWorkspaceConnector(
    {"credentials_file": os.environ["GOOGLE_SA_KEY_FILE"],
     "subject": os.environ["GOOGLE_SUBJECT"]}
)
agent = AgentRunner.from_config("agent_config.yaml", tools=build_tools(google))

if __name__ == "__main__":
    print(agent.run("Summarize my unread emails and list today's meetings.").output)
''',
}

_TEMPLATE_ENV = {
    "hr_agent": "OPENAI_API_KEY=\nHR_API_BASE_URL=\nHR_API_TOKEN=\n",
    "it_support_agent": (
        "OPENAI_API_KEY=\nSERVICENOW_INSTANCE_URL=\n"
        "SERVICENOW_USERNAME=\nSERVICENOW_PASSWORD=\n"
    ),
    "legal_agent": "OPENAI_API_KEY=\n",
    "knowledge_base_agent": (
        "OPENAI_API_KEY=\nNOTION_TOKEN=\n"
        "# SharePoint (optional):\n# MS_CLIENT_ID=\n# MS_CLIENT_SECRET=\n"
        "# MS_TENANT_ID=\n# SP_SITE_ID=\n"
    ),
    "exec_assistant_agent": (
        "OPENAI_API_KEY=\nMS_CLIENT_ID=\nMS_CLIENT_SECRET=\n"
        "MS_TENANT_ID=\nMS_MAILBOX=\n"
    ),
    "google_workspace_agent": (
        "OPENAI_API_KEY=\n"
        "GOOGLE_SA_KEY_FILE=path/to/service-account.json\n"
        "GOOGLE_SUBJECT=user@yourdomain.com\n"
    ),
}

_EXAMPLE_CASES = {
    "cases": [
        {"id": "example-1", "input": "Replace with a real test input.", "expected_output": "..."}
    ]
}


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------

def _run_wizard(name: str) -> dict:
    """Prompt the user for project settings. Returns a dict of choices."""
    click.echo()
    click.secho("  roscoe — new agent setup", bold=True)
    click.secho("  Configure your agent. All choices go into agent_config.yaml.", dim=True)
    click.secho("  You can change everything later by editing the YAML.\n", dim=True)

    # --- Provider ---
    click.echo("LLM Provider:")
    providers = list(_PROVIDERS.keys())
    _LABELS = {
        "openai": "openai",
        "openrouter": "openrouter  (100+ models, one API key)",
        "azure_openai": "azure_openai",
        "anthropic": "anthropic",
        "gemini": "gemini",
        "nvidia": "nvidia  (NVIDIA NIM, free-tier models available)",
        "ollama": "ollama  (free, local, no API key)",
    }
    for i, p in enumerate(providers, 1):
        click.echo(f"  [{i}] {_LABELS.get(p, p)}")
    choice = click.prompt(
        "Choose provider",
        type=click.IntRange(1, len(providers)),
        default=1,
    )
    provider = providers[choice - 1]
    pinfo = _PROVIDERS[provider]

    # --- Model ---
    model = click.prompt("Model name", default=pinfo["default_model"])

    # --- Temperature ---
    temperature = click.prompt("Temperature (0.0 = precise, 1.0 = creative)", default=0.1, type=float)

    # --- Base URL ---
    base_url = pinfo["base_url"]
    if provider == "openai":
        if click.confirm("Using a custom endpoint (Together, etc.)?", default=False):
            base_url = click.prompt("Base URL")

    # --- Azure extras ---
    azure_deployment = None
    if provider == "azure_openai":
        azure_deployment = click.prompt("Azure deployment name", default=model)

    # --- Middleware ---
    click.echo("\nMiddleware (all recommended for production):")
    cost_tracking = click.confirm("  Enable cost tracking?", default=True)
    rate_limiting = click.confirm("  Enable rate limiting?", default=True)
    rpm = 60
    if rate_limiting:
        rpm = click.prompt("  Requests per minute", default=60, type=int)
    retry = click.confirm("  Enable auto-retry on failures?", default=True)
    retry_attempts = 3
    if retry:
        retry_attempts = click.prompt("  Max retry attempts", default=3, type=int)
    audit = click.confirm("  Enable audit logging?", default=True)

    # --- Human approval ---
    click.echo("\nHuman-in-the-loop:")
    human_approval = click.confirm("  Require approval before running certain tools?", default=False)
    approval_tools: list[str] = []
    if human_approval:
        click.secho("  Enter tool function names that need sign-off (comma-separated).", dim=True)
        click.secho("  Example: send_email, delete_record, submit_payment", dim=True)
        raw = click.prompt("  Tools requiring approval")
        approval_tools = [t.strip() for t in raw.split(",") if t.strip()]

    # --- Memory ---
    click.echo("\nMemory:")
    conversation_memory = click.confirm("  Enable conversation memory (remembers within a session)?", default=True)
    window_size = 10
    if conversation_memory:
        window_size = click.prompt("  Conversation window size (messages to keep)", default=10, type=int)
    persistent_memory = click.confirm("  Enable persistent memory (remembers across sessions, sqlite)?", default=False)

    click.echo()

    return {
        "provider": provider,
        "model": model,
        "temperature": temperature,
        "base_url": base_url,
        "azure_deployment": azure_deployment,
        "env_key": pinfo["env_key"],
        "cost_tracking": cost_tracking,
        "rate_limiting": rate_limiting,
        "rpm": rpm,
        "retry": retry,
        "retry_attempts": retry_attempts,
        "audit": audit,
        "conversation_memory": conversation_memory,
        "window_size": window_size,
        "persistent_memory": persistent_memory,
        "human_approval": human_approval,
        "approval_tools": approval_tools,
    }


def _apply_wizard(dest: Path, answers: dict) -> None:
    """Rewrite agent_config.yaml with the wizard answers, keeping all comments."""
    cfg_path = dest / "agent_config.yaml"
    text = cfg_path.read_text()

    # OpenRouter uses the openai provider with a base_url
    config_provider = "openai" if answers["provider"] == "openrouter" else answers["provider"]

    # Provider + model block
    model_block = f"  provider: {config_provider}\n"
    if answers["azure_deployment"]:
        model_block += f"  deployment: {answers['azure_deployment']}\n"
        model_block += f"  endpoint: ${{{answers['env_key']}_ENDPOINT}}\n"  # noqa: E501
    else:
        model_block += f"  model: {answers['model']}\n"
    if answers["env_key"]:
        model_block += f"  api_key: ${{{answers['env_key']}}}\n"
    model_block += f"  temperature: {answers['temperature']}\n"
    if answers["base_url"]:
        model_block += f"  base_url: {answers['base_url']}\n"

    # Replace the active model block (non-comment lines between "model:" and next section)
    import re
    model_section = re.search(
        r"(^model:\n)((?:[ \t]+(?!#).*\n)+)",
        text,
        re.MULTILINE,
    )
    if model_section:
        text = text[: model_section.start(2)] + model_block + text[model_section.end(2) :]

    # Middleware toggles
    _swap = {
        "enabled: true": "enabled: true",
        "enabled: false": "enabled: false",
    }

    # Cost tracking
    if not answers["cost_tracking"]:
        text = text.replace(
            "  cost_tracking:\n    enabled: true",
            "  cost_tracking:\n    enabled: false",
        )

    # Rate limiting
    if not answers["rate_limiting"]:
        text = text.replace(
            "  rate_limiter:\n    enabled: true\n    requests_per_minute: 60",
            "  rate_limiter:\n    enabled: false\n    requests_per_minute: 60",
        )
    else:
        text = text.replace(
            "    requests_per_minute: 60",
            f"    requests_per_minute: {answers['rpm']}",
        )

    # Retry
    if not answers["retry"]:
        text = text.replace(
            "  retry:\n    max_attempts: 3",
            "  # retry:\n  #   max_attempts: 3",
        )
    else:
        text = text.replace("    max_attempts: 3", f"    max_attempts: {answers['retry_attempts']}")

    # Audit
    if not answers["audit"]:
        text = text.replace(
            "  audit:\n    enabled: true",
            "  audit:\n    enabled: false",
        )

    # Human approval
    if answers["human_approval"] and answers["approval_tools"]:
        tools_yaml = ", ".join(f'"{t}"' for t in answers["approval_tools"])
        text = text.replace(
            "  # human_approval:\n"
            "  #   require_approval_for: [send_email, delete_record]   # gate sensitive tools",
            f"  human_approval:\n"
            f"    require_approval_for: [{tools_yaml}]",
        )

    # Memory
    if not answers["conversation_memory"]:
        text = text.replace(
            "  conversation:\n    enabled: true\n    window_size: 10",
            "  conversation:\n    enabled: false\n    window_size: 10",
        )
    else:
        text = text.replace("    window_size: 10", f"    window_size: {answers['window_size']}")

    if answers["persistent_memory"]:
        text = text.replace(
            "    enabled: false                # long-term facts per user_id",
            "    enabled: true",
        )

    cfg_path.write_text(text)


# ---------------------------------------------------------------------------
# Scaffold logic
# ---------------------------------------------------------------------------

def scaffold_project(
    name: str,
    *,
    template: str | None = None,
    dest_dir: str | Path = ".",
    wizard_answers: dict | None = None,
) -> Path:
    """Create a new project directory. Returns the path created."""
    dest = Path(dest_dir) / name
    if dest.exists():
        raise FileExistsError(f"Destination already exists: {dest}")

    if template:
        shutil.copytree(
            template_path(template), dest, ignore=shutil.ignore_patterns("__pycache__")
        )
        _add_template_extras(dest, template)
    else:
        shutil.copytree(SCAFFOLD_DIR, dest)

    _render_placeholders(dest, name)

    if wizard_answers:
        _apply_wizard(dest, wizard_answers)

    return dest


def scaffold_workflow_project(name: str, dest_dir: str | Path = ".") -> Path:
    """Create a no-code project: ``agent_config.yaml`` + ``workflow.yaml``, no Python.

    Separate from :func:`scaffold_project` on purpose — the two layouts differ (there
    is no ``tools/my_tools.py`` here), and keeping them apart means the existing
    ``roscoe init`` flow is untouched for projects that write their tools in Python.
    """
    dest = Path(dest_dir) / name
    if dest.exists():
        raise FileExistsError(f"Destination already exists: {dest}")

    shutil.copytree(SCAFFOLD_NC_DIR, dest)
    shutil.copy(SCAFFOLD_DIR / "docs.md", dest / "docs.md")
    shutil.copy(SCAFFOLD_DIR / ".env.example", dest / ".env.example")
    evals_dir = dest / "evals"
    evals_dir.mkdir(exist_ok=True)
    (evals_dir / "test_cases.json").write_text(json.dumps(_EXAMPLE_CASES, indent=2))

    _render_placeholders(dest, name)
    return dest


def _add_template_extras(dest: Path, template: str) -> None:
    (dest / "main.py").write_text(_TEMPLATE_MAIN[template])
    (dest / ".env.example").write_text(_TEMPLATE_ENV[template])
    shutil.copy(SCAFFOLD_DIR / "docs.md", dest / "docs.md")
    evals_dir = dest / "evals"
    evals_dir.mkdir(exist_ok=True)
    (evals_dir / "test_cases.json").write_text(json.dumps(_EXAMPLE_CASES, indent=2))


def _render_placeholders(dest: Path, name: str) -> None:
    for p in dest.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in _RENDER_SUFFIXES and p.name != ".env.example":
            continue
        text = p.read_text()
        if PLACEHOLDER in text:
            p.write_text(text.replace(PLACEHOLDER, name))


@click.command("init")
@click.argument("project_name")
@click.option(
    "--template",
    type=click.Choice(available_templates()),
    default=None,
    help="Scaffold from a pre-built template instead of a blank project.",
)
@click.option(
    "--quick",
    is_flag=True,
    default=False,
    help="Skip the interactive wizard and use defaults.",
)
@click.option(
    "--cli",
    is_flag=True,
    default=False,
    help="Force CLI wizard instead of GUI window.",
)
def init_command(project_name: str, template: str | None, quick: bool, cli: bool) -> None:
    """Create a new roscoe agent project."""
    wizard_answers = None
    if not template and not quick:
        if cli:
            wizard_answers = _run_wizard(project_name)
        else:
            try:
                from roscoe.cli.wizard_gui import _TkUnavailable, run_wizard_gui

                wizard_answers = run_wizard_gui(project_name)
                if wizard_answers is None:
                    raise click.Abort()
            except _TkUnavailable:
                click.echo("No display available — falling back to CLI wizard.\n")
                wizard_answers = _run_wizard(project_name)

    try:
        dest = scaffold_project(project_name, template=template, wizard_answers=wizard_answers)
    except FileExistsError as exc:
        raise click.ClickException(str(exc)) from exc

    kind = f"template '{template}'" if template else "blank project"
    click.echo(click.style(f"  Created {kind} at {dest}/", fg="green", bold=True))
    click.echo()
    click.echo("  Next steps:")
    click.echo(f"    cd {dest}")
    if not template and wizard_answers and wizard_answers.get("env_key"):
        click.echo("    cp .env.example .env   # fill in your API key")
    elif template:
        click.echo("    cp .env.example .env   # fill in your keys")
    click.echo("    python main.py")


@click.command("init-nc")
@click.argument("project_name")
def init_nc_command(project_name: str) -> None:
    """Create a no-code agent project — behaviour defined in workflow.yaml.

    The same runtime as ``roscoe init``: connectors, middleware, approval gates,
    cost tracking and audit all apply. The difference is where behaviour lives —
    a workflow graph in YAML rather than ``@tool`` functions in Python.
    """
    try:
        dest = scaffold_workflow_project(project_name)
    except FileExistsError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(click.style(f"  Created no-code project at {dest}/", fg="green", bold=True))
    click.echo()
    click.echo("  Next steps:")
    click.echo(f"    cd {dest}")
    click.echo("    cp .env.example .env       # fill in your API key")
    click.echo("    roscoe validate            # check the workflow before running it")
    click.echo('    roscoe run --set topic="cloud security"')
