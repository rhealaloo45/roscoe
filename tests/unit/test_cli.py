"""Phase 10 — CLI tests. Filesystem scaffolding and command wiring; no live LLM."""

import json

import pytest
from click.testing import CliRunner

from roscoe.cli.eval_command import _load_tools
from roscoe.cli.init_command import scaffold_project
from roscoe.cli.main import cli


# --- init: blank scaffold ---


def test_scaffold_blank_project(tmp_path):
    dest = scaffold_project("my_proj", dest_dir=tmp_path)
    assert (dest / "agent_config.yaml").exists()
    assert (dest / "main.py").exists()
    assert (dest / "tools" / "my_tools.py").exists()
    assert (dest / "prompts" / "system.txt").exists()
    assert (dest / "evals" / "test_cases.json").exists()
    assert (dest / ".env.example").exists()
    assert (dest / "custom_ui_example.py").exists()
    assert (dest / "quickstart.md").exists()
    assert (dest / "quickstart_images").is_dir()
    # placeholder substituted
    assert "my_proj" in (dest / "agent_config.yaml").read_text()
    assert "__PROJECT_NAME__" not in (dest / "prompts" / "system.txt").read_text()


def test_custom_ui_example_is_syntactically_valid():
    """Not imported (it eagerly builds a runner at module scope, which needs a
    real, working project to construct against) — but a typo here would sit
    invisible until someone actually tried the one thing this file exists to
    demonstrate.
    """
    import ast

    from roscoe.cli.init_command import SCAFFOLD_DIR

    source = (SCAFFOLD_DIR / "custom_ui_example.py").read_text()
    ast.parse(source)  # raises SyntaxError if it doesn't parse


def test_scaffold_existing_dir_raises(tmp_path):
    scaffold_project("dup", dest_dir=tmp_path)
    with pytest.raises(FileExistsError):
        scaffold_project("dup", dest_dir=tmp_path)


def test_init_command_blank():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["init", "blank_bot", "--quick"])
        assert result.exit_code == 0, result.output
        assert "Created blank project" in result.output
        from pathlib import Path

        assert Path("blank_bot/main.py").exists()


def test_init_command_wizard():
    runner = CliRunner()
    with runner.isolated_filesystem():
        # Simulate wizard: provider=1 (openai), model=default, temp=default,
        # no custom endpoint, all middleware yes (defaults), memory defaults
        inputs = "\n".join([
            "1",           # provider: openai
            "",            # model: default (gpt-4o-mini)
            "",            # temperature: default (0.1)
            "n",           # custom endpoint? no
            "y",           # cost tracking
            "y",           # rate limiting
            "60",          # rpm
            "y",           # retry
            "3",           # retry attempts
            "y",           # audit
            "n",           # human approval? no
            "y",           # conversation memory
            "10",          # window size
            "n",           # persistent memory
        ])
        result = runner.invoke(cli, ["init", "wizard_bot", "--cli"], input=inputs)
        assert result.exit_code == 0, result.output
        assert "Created blank project" in result.output
        from pathlib import Path

        cfg = Path("wizard_bot/agent_config.yaml").read_text()
        assert "provider: openai" in cfg
        assert "gpt-4o-mini" in cfg


def test_init_command_wizard_openrouter():
    runner = CliRunner()
    with runner.isolated_filesystem():
        inputs = "\n".join([
            "2",           # provider: openrouter
            "",            # model: default
            "",            # temperature: default
            "y",           # cost tracking
            "y",           # rate limiting
            "60",          # rpm
            "y",           # retry
            "3",           # retry attempts
            "y",           # audit
            "n",           # human approval? no
            "y",           # conversation memory
            "10",          # window size
            "n",           # persistent memory
        ])
        result = runner.invoke(cli, ["init", "or_bot", "--cli"], input=inputs)
        assert result.exit_code == 0, result.output
        from pathlib import Path

        cfg = Path("or_bot/agent_config.yaml").read_text()
        assert "provider: openai" in cfg
        assert "openrouter.ai" in cfg
        assert "OPENROUTER_API_KEY" in cfg


# --- init: from template ---


def test_scaffold_from_template_hr(tmp_path):
    dest = scaffold_project("my_hr", template="hr_agent", dest_dir=tmp_path)
    assert (dest / "agent_config.yaml").exists()
    assert (dest / "tools" / "hr_tools.py").exists()
    assert (dest / "prompts" / "system.txt").exists()
    # template extras added by init
    assert (dest / "main.py").exists()
    assert (dest / ".env.example").exists()
    assert (dest / "evals" / "test_cases.json").exists()
    assert (dest / "custom_ui_example.py").exists()
    assert (dest / "quickstart.md").exists()
    assert (dest / "quickstart_images").is_dir()
    assert "build_tools" in (dest / "main.py").read_text()
    assert "HR_API_TOKEN" in (dest / ".env.example").read_text()


def test_init_command_wizard_hitl():
    runner = CliRunner()
    with runner.isolated_filesystem():
        inputs = "\n".join([
            "1",           # provider: openai
            "",            # model: default
            "",            # temperature: default
            "n",           # custom endpoint? no
            "y",           # cost tracking
            "y",           # rate limiting
            "60",          # rpm
            "y",           # retry
            "3",           # retry attempts
            "y",           # audit
            "y",           # human approval? yes
            "send_email, delete_record",  # tools
            "y",           # conversation memory
            "10",          # window size
            "n",           # persistent memory
        ])
        result = runner.invoke(cli, ["init", "hitl_bot", "--cli"], input=inputs)
        assert result.exit_code == 0, result.output
        from pathlib import Path

        cfg = Path("hitl_bot/agent_config.yaml").read_text()
        assert "human_approval:" in cfg
        assert "send_email" in cfg
        assert "delete_record" in cfg


def test_init_command_unknown_template():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["init", "x", "--template", "nope"])
        assert result.exit_code != 0  # click rejects bad choice


# --- monitor ---


def test_monitor_command_reads_audit(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps(
            {
                "agent_name": "hr",
                "status": "success",
                "cost_usd": 0.02,
                "total_tokens": 100,
                "start_time": "2026-06-27T10:00:00+00:00",
                "end_time": "2026-06-27T10:00:01+00:00",
            }
        )
        + "\n"
    )
    # --terminal is required: without it `monitor` opens the tkinter dashboard, which
    # blocks until a human closes the window on any machine that actually has a display.
    result = CliRunner().invoke(cli, ["monitor", "--path", str(audit), "--terminal"])
    assert result.exit_code == 0
    assert "roscoe monitor" in result.output
    assert "runs: 1" in result.output


def test_monitor_command_empty(tmp_path):
    result = CliRunner().invoke(
        cli, ["monitor", "--path", str(tmp_path / "none.jsonl"), "--terminal"]
    )
    assert result.exit_code == 0
    assert "No audit records" in result.output


# --- eval helpers ---


def test_load_tools_none_and_bad_ref():
    assert _load_tools(None) == []
    with pytest.raises(ValueError):
        _load_tools("no_colon_here")


def test_load_tools_resolves_callable_factory():
    # available_templates() is a zero-arg callable returning a list
    tools = _load_tools("roscoe.templates:available_templates")
    assert "hr_agent" in tools


def test_eval_command_missing_dataset_errors():
    runner = CliRunner()
    with runner.isolated_filesystem():
        # --terminal for the same reason as `monitor` above: the default opens the
        # tkinter eval runner, which blocks on a machine with a display.
        result = runner.invoke(
            cli, ["eval", "--dataset", "nope.json", "--config", "nope.yaml", "--terminal"]
        )
        assert result.exit_code != 0
