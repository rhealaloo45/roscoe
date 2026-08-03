"""roscoe — provider-agnostic LangChain agent framework with middleware and evals."""

import os

# langchain_core imports transformers (for token counting) as soon as a chat
# model class is touched, and transformers imports torch unconditionally on
# import. roscoe never uses torch itself, but a broken local torch install
# (e.g. a missing DLL dependency) would otherwise crash every roscoe import.
# USE_TORCH=0 is transformers' own documented flag for skipping that import;
# setdefault so a project that genuinely wants torch can still opt back in.
os.environ.setdefault("USE_TORCH", "0")

from roscoe.core.agent_result import AgentResult
from roscoe.core.agent_runner import AgentRunner

__version__ = "2.6.0"

__all__ = ["AgentRunner", "AgentResult", "__version__"]
