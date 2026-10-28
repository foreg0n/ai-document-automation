"""Small, explicit configuration loader; never print credentials."""
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Missing or invalid LLM settings."""


@dataclass(frozen=True)
class LLMSettings:
    model: str
    api_key: str = field(repr=False)
    base_url: str = "https://openrouter.ai/api/v1"
    response_format: str = "json_schema"
    timeout_seconds: float = 60.0


def read_env_file(path: Path) -> dict[str, str]:
    """Read KEY=value lines, optional matching quotes, and full-line comments.

    This intentionally does not execute shell code or expand variables.
    """
    if not path.exists():
        return {}
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not key.isidentifier():
            raise ConfigurationError(f"Invalid .env assignment on line {number}.")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigurationError(f"Unclosed .env quote on line {number}.")
            value = value[1:-1]
        values[key] = value
    return values


def load_settings(
    env_file: Path, *, model: str | None = None,
    response_format: str | None = None, require_api_key: bool = True,
) -> LLMSettings:
    """CLI overrides environment variables, which override the .env file."""
    values = {**read_env_file(env_file), **os.environ}
    selected_model = (model if model is not None else values.get("LLM_MODEL", "")).strip()
    api_key = values.get("LLM_API_KEY", "").strip()
    base_url = values.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").strip().rstrip("/")
    selected_format = response_format or values.get("LLM_RESPONSE_FORMAT", "json_schema")
    if not selected_model:
        raise ConfigurationError("Set LLM_MODEL in .env, or pass --model MODEL_ID.")
    if require_api_key and not api_key:
        raise ConfigurationError("Set LLM_API_KEY in .env before a live run. --dry-run needs no key.")
    if not api_key.isascii() or any(ord(character) < 32 or ord(character) == 127 for character in api_key):
        raise ConfigurationError("LLM_API_KEY must contain only printable ASCII characters.")
    try:
        address = urlsplit(base_url)
        address.port  # Validate malformed or out-of-range ports as well.
        valid_address = address.hostname and not address.username and not address.password
        valid_scheme = address.scheme == "https" or (
            address.scheme == "http" and address.hostname in {"localhost", "127.0.0.1", "::1"}
        )
    except ValueError as exc:
        raise ConfigurationError("Invalid LLM_BASE_URL.") from exc
    if (not valid_address or not valid_scheme or address.query or address.fragment
            or not base_url.isascii() or any(character.isspace() for character in base_url)):
        raise ConfigurationError("LLM_BASE_URL must be an HTTPS API root (HTTP is allowed for localhost).")
    if selected_format not in {"json_schema", "json_object"}:
        raise ConfigurationError("LLM_RESPONSE_FORMAT must be json_schema or json_object.")
    try:
        timeout = float(values.get("LLM_TIMEOUT_SECONDS", "60"))
    except ValueError as exc:
        raise ConfigurationError("LLM_TIMEOUT_SECONDS must be a number.") from exc
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ConfigurationError("LLM_TIMEOUT_SECONDS must be greater than 0 and at most 300.")
    return LLMSettings(selected_model, api_key, base_url, selected_format, timeout)
