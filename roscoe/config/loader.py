"""YAML config loader with ``${ENV_VAR}`` resolution.

Loads an agent config file and substitutes ``${VAR}`` references from the
environment. A missing variable raises a clear error naming the variable and the
config key path, so failures surface at load time — not mid-run.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

# Matches ${VAR}. Variable names follow shell conventions: letters, digits, underscore,
# not starting with a digit.
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(ValueError):
    """Raised when a config file is malformed or an env var is missing."""


def load_config(path: str | Path, *, strict: bool = True) -> dict[str, Any]:
    """Load a YAML config file and resolve ``${ENV_VAR}`` references.

    Args:
        path: Path to the YAML config file.
        strict: If True (the default, used to actually run an agent), a missing
            env var raises. If False (for read-only inspection like `roscoe
            validate`/`roscoe graph`, meant to work before secrets exist), a
            missing var is left as the literal ``${VAR}`` string instead.

    Returns:
        The parsed config as a dict, with all ``${VAR}`` strings substituted.

    Raises:
        ConfigError: If the file is missing, not a mapping, or (when strict)
            references an environment variable that is not set.
    """
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"Config file not found: {p}")

    # Load .env from the config file's directory (then CWD as fallback) so
    # ${ENV_VAR} references resolve without manual exports or shell setup.
    try:
        from dotenv import load_dotenv
        load_dotenv(p.parent / ".env", override=False)
        load_dotenv(override=False)  # CWD fallback
    except ImportError:
        pass

    raw = yaml.safe_load(p.read_text()) or {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Config root must be a mapping (YAML dict), got {type(raw).__name__}: {p}"
        )
    return _resolve(raw, key_path="", strict=strict)


def _resolve(node: Any, key_path: str, *, strict: bool) -> Any:
    """Recursively walk the config, substituting env vars in every string."""
    if isinstance(node, dict):
        return {k: _resolve(v, _join(key_path, k), strict=strict) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, f"{key_path}[{i}]", strict=strict) for i, v in enumerate(node)]
    if isinstance(node, str):
        return _substitute(node, key_path, strict=strict)
    return node


def _substitute(value: str, key_path: str, *, strict: bool) -> str:
    def replace(match: re.Match[str]) -> str:
        var = match.group(1)
        if var not in os.environ:
            if strict:
                raise ConfigError(
                    f"Environment variable '${{{var}}}' referenced at config key "
                    f"'{key_path}' is not set."
                )
            return match.group(0)
        return os.environ[var]

    return _ENV_PATTERN.sub(replace, value)


def _join(prefix: str, key: str) -> str:
    return key if not prefix else f"{prefix}.{key}"
