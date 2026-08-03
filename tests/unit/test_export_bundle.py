"""The download itself — a folder someone can unzip into a project and use.

The generated script is covered elsewhere. What matters here is everything
around it: that the .env.example names the variables the script will actually
look for, that the README describes this workflow rather than a generic one, and
that the whole thing arrives as one archive from the button in the editor.
"""

import base64
import io
import textwrap
import zipfile

import pytest

from roscoe.cli.build_command import _EditorState
from roscoe.export import ExportError, build_bundle, env_vars
from roscoe.export.bundle import describe_flow, env_example
from roscoe.workflow.schema import Workflow

CONFIG = {
    "agent_name": "briefing",
    "model": {"provider": "nvidia", "model": "gpt-oss", "api_key": "${NVIDIA_API_KEY}"},
    "connectors": {
        "research": {"type": "web_search", "provider": "tavily",
                     "api_key": "${SEARCH_API_KEY}"},
    },
}

FLOW = {
    "entry": "search",
    "output": "{{ answer }}",
    "nodes": [
        {"id": "daily", "type": "trigger", "every": "1d", "at": "08:00",
         "next": "search"},
        {"id": "search", "type": "connector_action", "connector": "research",
         "method": "search", "inputs": {"query": "ai agents"}, "output": "results",
         "next": "brief"},
        {"id": "brief", "type": "llm_step", "prompt": "Summarise {{ results }}",
         "output": "answer", "next": "END"},
    ],
}


def _workflow(flow=FLOW):
    return Workflow.from_dict(flow, {})


def _files(archive: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return {name: zf.read(name).decode() for name in zf.namelist()}


# --- what's in the box ---


def test_the_download_is_four_files_under_one_folder():
    """Unzipping in a project root should drop one tidy directory, not scatter
    four loose files into whatever was already there."""
    files = _files(build_bundle(_workflow(), CONFIG, name="briefing"))

    assert set(files) == {
        "briefing/briefing.py",
        "briefing/.env.example",
        "briefing/requirements.txt",
        "briefing/README.md",
    }


def test_requirements_asks_for_httpx_and_nothing_else():
    files = _files(build_bundle(_workflow(), CONFIG, name="briefing"))
    stated = [line for line in files["briefing/requirements.txt"].splitlines()
              if line.strip() and not line.startswith("#")]

    assert stated == ["httpx>=0.27,<1.0"]


def test_the_script_in_the_zip_is_the_generated_one():
    files = _files(build_bundle(_workflow(), CONFIG, name="briefing"))

    compile(files["briefing/briefing.py"], "briefing.py", "exec")
    assert "import roscoe" not in files["briefing/briefing.py"]


# --- .env.example ---


def test_every_placeholder_is_listed_with_what_wants_it():
    """Generated from the config, not written by hand, so a connector added in
    the editor can't be missing from the file someone fills in."""
    found = env_vars(CONFIG)

    assert found["NVIDIA_API_KEY"] == "nvidia API key"
    assert found["SEARCH_API_KEY"] == "connector 'research' (web_search)"


def test_the_example_file_names_variables_but_carries_no_values():
    text = env_example(CONFIG)

    assert "NVIDIA_API_KEY=" in text
    assert "SEARCH_API_KEY=" in text
    # Every assignment is left empty — an example file with a real key in it is
    # the leak this whole placeholder convention exists to avoid.
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            assert line.endswith("="), line


def test_a_placeholder_nested_in_a_connector_setting_is_still_found():
    config = {"model": {"provider": "openai"},
              "connectors": {"api": {"type": "rest_api", "auth": "bearer",
                                     "token": "${DEEP_TOKEN}"}}}

    assert "DEEP_TOKEN" in env_vars(config)


def test_an_agent_needing_no_secrets_says_so_rather_than_shipping_a_blank_file():
    config = {"model": {"provider": "ollama", "model": "llama3.1"}, "connectors": {}}

    assert "needs no secrets" in env_example(config)


# --- README ---


def test_the_readme_describes_this_workflow_not_a_generic_one():
    steps = describe_flow(_workflow())

    assert "`search`" in steps
    assert "calls `search` on the `research` connector" in steps
    assert "saving the result as `results`" in steps
    assert "every 1d at 08:00" in steps        # the trigger documents its cadence


def test_the_readme_shows_how_to_call_it_and_what_comes_back():
    files = _files(build_bundle(_workflow(), CONFIG, name="briefing"))
    readme = files["briefing/README.md"]

    assert "from briefing import run" in readme
    assert "pip install -r requirements.txt" in readme
    assert "cp .env.example .env" in readme
    # The two things a caller most needs to know before wiring it in.
    assert "never raises" in readme
    assert "roscoe is not needed" in readme.replace("**", "")


def test_the_readme_says_what_did_not_come_with_it():
    """Someone integrating this shouldn't assume retries and approval gates came
    along — the header says so, and so should the README."""
    files = _files(build_bundle(_workflow(), CONFIG, name="briefing"))

    assert "What stayed behind" in files["briefing/README.md"]


# --- refusals still refuse ---


def test_a_workflow_that_cannot_export_refuses_before_building_an_archive():
    config = {**CONFIG, "connectors": {"gmail": {"type": "google_workspace"}}}
    flow = {"entry": "a", "nodes": [
        {"id": "a", "type": "connector_action", "connector": "gmail",
         "method": "read_emails"}]}

    with pytest.raises(ExportError):
        build_bundle(Workflow.from_dict(flow, {}), config, name="x")


# --- the button in the editor ---


def _project(tmp_path):
    (tmp_path / "agent_config.yaml").write_text(textwrap.dedent("""
        agent_name: briefing
        model:
          provider: nvidia
          model: gpt-oss
          api_key: ${NVIDIA_API_KEY}
        connectors:
          research:
            type: web_search
            provider: tavily
            api_key: ${SEARCH_API_KEY}
    """))
    (tmp_path / "workflow.yaml").write_text(textwrap.dedent("""
        workflow:
          entry: search
          output: "{{ answer }}"
          nodes:
            - id: search
              type: connector_action
              connector: research
              method: search
              inputs:
                query: ai agents
              output: results
              next: brief
            - id: brief
              type: llm_step
              prompt: Summarise {{ results }}
              output: answer
              next: END
    """))
    return _EditorState(tmp_path / "agent_config.yaml", tmp_path / "workflow.yaml")


def test_the_editor_hands_back_a_named_archive(tmp_path):
    out = _project(tmp_path).export_bundle()

    assert out["ok"] is True
    assert out["filename"] == "briefing.zip"
    assert set(_files(base64.b64decode(out["data"]))) == {
        "briefing/briefing.py",
        "briefing/.env.example",
        "briefing/requirements.txt",
        "briefing/README.md",
    }


def test_an_unset_secret_does_not_block_the_download(tmp_path):
    """The placeholder is what gets written out anyway, so refusing here would
    only stop people exporting before they had finished configuring."""
    out = _project(tmp_path).export_bundle()

    source = _files(base64.b64decode(out["data"]))["briefing/briefing.py"]
    assert "os.environ.get('SEARCH_API_KEY', '')" in source


def test_a_refusal_comes_back_as_a_message_not_an_exception(tmp_path):
    """The person reading it is looking at a web page, not a terminal."""
    (tmp_path / "agent_config.yaml").write_text(textwrap.dedent("""
        agent_name: briefing
        model: {provider: nvidia, model: m}
        connectors:
          gmail:
            type: google_workspace
    """))
    (tmp_path / "workflow.yaml").write_text(textwrap.dedent("""
        workflow:
          entry: a
          nodes:
            - id: a
              type: connector_action
              connector: gmail
              method: read_emails
              output: x
              next: END
    """))
    state = _EditorState(tmp_path / "agent_config.yaml", tmp_path / "workflow.yaml")

    out = state.export_bundle()

    assert out["ok"] is False
    assert "gmail" in out["error"]


def test_the_page_downloads_the_archive_rather_than_a_bare_script():
    from roscoe.cli.build_ui import PAGE

    assert "/api/export-bundle" in PAGE
    assert "application/zip" in PAGE
