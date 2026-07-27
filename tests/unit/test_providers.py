"""Unit tests for ProviderFactory: built-ins, registration, capabilities."""

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from roscoe.llm import BaseProvider, ProviderFactory

_FAKE_CONFIGS = {
    "azure_openai": {
        "provider": "azure_openai",
        "deployment": "gpt-4o",
        "endpoint": "https://x.openai.azure.com",
        "api_key": "fake",
    },
    "openai": {"provider": "openai", "model": "gpt-4o", "api_key": "fake"},
    "gemini": {"provider": "gemini", "model": "gemini-1.5-pro", "api_key": "fake"},
    "anthropic": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "api_key": "fake",
    },
    "ollama": {"provider": "ollama", "model": "qwen2.5"},
}


@pytest.mark.parametrize("provider", list(_FAKE_CONFIGS))
def test_builtin_providers_instantiate(provider):
    llm = ProviderFactory.get_llm(_FAKE_CONFIGS[provider])
    assert isinstance(llm, BaseChatModel)


def test_unknown_provider_lists_builtins_and_hints_register():
    with pytest.raises(ValueError) as exc:
        ProviderFactory.get_llm({"provider": "nope"})
    msg = str(exc.value)
    assert "nope" in msg
    assert "azure_openai" in msg
    assert "register(" in msg


def test_custom_provider_registered_and_resolved():
    class DummyProvider(BaseProvider):
        def get_llm(self, config):
            return FakeMessagesListChatModel(responses=[AIMessage(content="hi")])

        def capabilities(self):
            return {"tool_calling": True, "streaming": False, "cost_tracking": False}

    ProviderFactory.register("company_llm", DummyProvider())
    llm = ProviderFactory.get_llm({"provider": "company_llm"})
    assert isinstance(llm, BaseChatModel)
    assert ProviderFactory.capabilities("company_llm")["tool_calling"] is True


def test_register_rejects_non_provider():
    with pytest.raises(TypeError):
        ProviderFactory.register("bad", object())


def test_builtin_capabilities():
    assert ProviderFactory.capabilities("ollama")["cost_tracking"] is False
    assert ProviderFactory.capabilities("azure_openai")["tool_calling"] is True


# --- temperature is opt-in, so reasoning models aren't broken by default ---
#
# o1/o3/gpt-5-* reject any temperature but their default (1) outright:
# "Unsupported value: 'temperature' does not support 0.1 with this model."
# Forcing a default meant roscoe could never point at one of those deployments.


@pytest.mark.parametrize("provider", ["azure_openai", "openai"])
def test_temperature_is_omitted_when_the_config_does_not_set_one(provider):
    llm = ProviderFactory.get_llm(_FAKE_CONFIGS[provider])

    # langchain_openai chat models default temperature to None internally when
    # not passed, so "we never forced one" shows up as None here.
    assert llm.temperature is None


@pytest.mark.parametrize("provider", ["azure_openai", "openai"])
def test_an_explicit_temperature_is_still_honoured(provider):
    llm = ProviderFactory.get_llm({**_FAKE_CONFIGS[provider], "temperature": 0.2})

    assert llm.temperature == 0.2
