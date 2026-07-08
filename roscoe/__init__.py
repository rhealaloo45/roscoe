"""roscoe — provider-agnostic LangChain agent framework with middleware and evals."""

from roscoe.core.agent_result import AgentResult
from roscoe.core.agent_runner import AgentRunner

__version__ = "2.1.0"

__all__ = ["AgentRunner", "AgentResult", "__version__"]
