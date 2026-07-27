"""ProviderFactory — resolves a config ``model:`` block to a LangChain chat model.

Five built-in providers (azure_openai, openai, gemini, anthropic, ollama) plus a
plugin registry: ``ProviderFactory.register(name, provider)`` adds any
``BaseProvider``. Registered providers are checked **before** built-ins, so a user
can override a built-in if they want. Adapter SDKs are imported lazily, so a missing
optional package only errors if that provider is actually used.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from roscoe.llm.base_provider import BaseProvider
from roscoe.llm.capability_map import get_capabilities


class _AzureOpenAIProvider(BaseProvider):
    def get_llm(self, config: dict[str, Any]) -> BaseChatModel:
        from langchain_openai import AzureChatOpenAI

        _require(config, "azure_openai", "deployment", "endpoint", "api_key")
        kwargs: dict[str, Any] = {
            "azure_deployment": config["deployment"],
            "azure_endpoint": config["endpoint"],
            "api_key": config["api_key"],
            "api_version": config.get("api_version", "2024-06-01"),
        }
        _apply_sampling(kwargs, config, tokens_key="max_tokens")
        return AzureChatOpenAI(**kwargs)

    def capabilities(self) -> dict[str, bool]:
        return get_capabilities("azure_openai")


class _OpenAIProvider(BaseProvider):
    def get_llm(self, config: dict[str, Any]) -> BaseChatModel:
        from langchain_openai import ChatOpenAI

        _require(config, "openai", "model", "api_key")
        kwargs: dict[str, Any] = {
            "model": config["model"],
            "api_key": config["api_key"],
            "base_url": config.get("base_url"),
        }
        _apply_sampling(kwargs, config, tokens_key="max_tokens")
        return ChatOpenAI(**kwargs)

    def capabilities(self) -> dict[str, bool]:
        return get_capabilities("openai")


class _GeminiProvider(BaseProvider):
    def get_llm(self, config: dict[str, Any]) -> BaseChatModel:
        from langchain_google_genai import ChatGoogleGenerativeAI

        _require(config, "gemini", "model", "api_key")
        return ChatGoogleGenerativeAI(
            model=config["model"],
            google_api_key=config["api_key"],
            temperature=config.get("temperature", 0.1),
            max_output_tokens=config.get("max_tokens"),
        )

    def capabilities(self) -> dict[str, bool]:
        return get_capabilities("gemini")


class _AnthropicProvider(BaseProvider):
    def get_llm(self, config: dict[str, Any]) -> BaseChatModel:
        from langchain_anthropic import ChatAnthropic

        _require(config, "anthropic", "model", "api_key")
        return ChatAnthropic(
            model=config["model"],
            api_key=config["api_key"],
            temperature=config.get("temperature", 0.1),
            max_tokens=config.get("max_tokens", 1024),
        )

    def capabilities(self) -> dict[str, bool]:
        return get_capabilities("anthropic")


class _OllamaProvider(BaseProvider):
    def get_llm(self, config: dict[str, Any]) -> BaseChatModel:
        from langchain_ollama import ChatOllama

        _require(config, "ollama", "model")
        return ChatOllama(
            model=config["model"],
            base_url=config.get("base_url", "http://localhost:11434"),
            temperature=config.get("temperature", 0.1),
        )

    def capabilities(self) -> dict[str, bool]:
        return get_capabilities("ollama")


class _NvidiaProvider(BaseProvider):
    def get_llm(self, config: dict[str, Any]) -> BaseChatModel:
        from langchain_nvidia_ai_endpoints import ChatNVIDIA

        _require(config, "nvidia", "model", "api_key")
        kwargs: dict[str, Any] = {
            "model": config["model"],
            "api_key": config["api_key"],
            "temperature": config.get("temperature", 0.1),
        }
        if config.get("top_p") is not None:
            kwargs["top_p"] = config["top_p"]
        if config.get("max_tokens") is not None:
            kwargs["max_tokens"] = config["max_tokens"]
        if config.get("reasoning_budget") is not None:
            kwargs["reasoning_budget"] = config["reasoning_budget"]
        if config.get("chat_template_kwargs") is not None:
            kwargs["chat_template_kwargs"] = config["chat_template_kwargs"]
        if config.get("base_url") is not None:
            kwargs["base_url"] = config["base_url"]
        return ChatNVIDIA(**kwargs)

    def capabilities(self) -> dict[str, bool]:
        return get_capabilities("nvidia")


_BUILTINS: dict[str, BaseProvider] = {
    "azure_openai": _AzureOpenAIProvider(),
    "openai": _OpenAIProvider(),
    "gemini": _GeminiProvider(),
    "anthropic": _AnthropicProvider(),
    "ollama": _OllamaProvider(),
    "nvidia": _NvidiaProvider(),
}


class ProviderFactory:
    """Resolves providers; supports custom registration."""

    _registry: dict[str, BaseProvider] = {}

    @classmethod
    def register(cls, name: str, provider: BaseProvider) -> None:
        """Register a custom ``BaseProvider`` under ``name`` (process lifetime).

        Custom names take priority over built-ins with the same name.
        """
        if not isinstance(provider, BaseProvider):
            raise TypeError(
                f"provider must be a BaseProvider instance, got {type(provider).__name__}."
            )
        cls._registry[name] = provider

    @classmethod
    def get_provider(cls, name: str) -> BaseProvider:
        """Return the provider for ``name`` (registry first, then built-ins)."""
        if name in cls._registry:
            return cls._registry[name]
        if name in _BUILTINS:
            return _BUILTINS[name]
        raise ValueError(
            f"Unknown provider '{name}'. Built-in providers: "
            f"{', '.join(sorted(_BUILTINS))}. To add your own, implement BaseProvider "
            f"and call ProviderFactory.register('{name}', YourProvider())."
        )

    @classmethod
    def get_llm(cls, config: dict[str, Any]) -> BaseChatModel:
        """Build a chat model from the config's ``model`` block."""
        provider = config.get("provider")
        if not provider:
            raise ValueError("model config is missing required key 'provider'.")
        return cls.get_provider(provider).get_llm(config)

    @classmethod
    def capabilities(cls, name: str) -> dict[str, bool]:
        """Capability flags for a provider (registry-aware)."""
        return cls.get_provider(name).capabilities()


def _apply_sampling(kwargs: dict[str, Any], config: dict[str, Any], *, tokens_key: str) -> None:
    """Add temperature/max_tokens only when the user actually set them.

    A reasoning model (o1, o3, gpt-5-*) rejects any temperature but its default
    (1) outright — "Unsupported value: 'temperature' does not support 0.1 with
    this model." Forcing a default here means roscoe can never point at one of
    those deployments. Omitting the key lets the model's own default apply,
    which is what those deployments actually require.
    """
    if config.get("temperature") is not None:
        kwargs["temperature"] = config["temperature"]
    if config.get("max_tokens") is not None:
        kwargs[tokens_key] = config["max_tokens"]


def _require(config: dict[str, Any], provider: str, *keys: str) -> None:
    missing = [k for k in keys if not config.get(k)]
    if missing:
        raise ValueError(
            f"{provider} config missing required keys: {', '.join(missing)}."
        )
