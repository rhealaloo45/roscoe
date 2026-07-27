"""Unit tests for the YAML config loader."""

import pytest

from roscoe.config.loader import ConfigError, load_config


def _write(tmp_path, text):
    p = tmp_path / "agent_config.yaml"
    p.write_text(text)
    return p


def test_env_var_substitution(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_KEY", "secret-123")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    cfg = _write(
        tmp_path,
        """
        model:
          provider: azure_openai
          deployment: gpt-4o
          endpoint: ${AZURE_OPENAI_ENDPOINT}
          api_key: ${AZURE_OPENAI_KEY}
        """,
    )
    out = load_config(cfg)
    assert out["model"]["api_key"] == "secret-123"
    assert out["model"]["endpoint"] == "https://x.openai.azure.com"
    assert out["model"]["deployment"] == "gpt-4o"


def test_missing_env_var_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("DOES_NOT_EXIST", raising=False)
    cfg = _write(
        tmp_path,
        """
        model:
          api_key: ${DOES_NOT_EXIST}
        """,
    )
    with pytest.raises(ConfigError) as exc:
        load_config(cfg)
    msg = str(exc.value)
    assert "DOES_NOT_EXIST" in msg
    assert "model.api_key" in msg  # names the offending key path


def test_non_strict_leaves_a_missing_var_as_the_literal_placeholder(tmp_path, monkeypatch):
    # roscoe validate/graph inspect a project's structure and are meant to work
    # before any secrets are configured — strict=False is how they opt out of
    # the fail-fast behaviour that `roscoe run` needs.
    monkeypatch.delenv("DOES_NOT_EXIST", raising=False)
    cfg = _write(
        tmp_path,
        """
        model:
          api_key: ${DOES_NOT_EXIST}
        """,
    )
    resolved = load_config(cfg, strict=False)
    assert resolved["model"]["api_key"] == "${DOES_NOT_EXIST}"


def test_substitution_inside_nested_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_URL", "https://api.internal")
    cfg = _write(
        tmp_path,
        """
        connectors:
          - name: rest
            base_url: ${TOOL_URL}
        """,
    )
    out = load_config(cfg)
    assert out["connectors"][0]["base_url"] == "https://api.internal"


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_non_mapping_root_raises(tmp_path):
    cfg = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ConfigError):
        load_config(cfg)
