"""One non-streaming Chat Completions request using the Python standard library."""
import json
from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import LLMSettings
from .invoice import InvoiceFormatError, parse_invoice_fields

MAX_RESPONSE_BYTES = 1_000_000


class APIError(Exception):
    """A transport/API error; stop the batch rather than repeat failing calls."""


class NoRedirect(HTTPRedirectHandler):
    """Keep the Authorization header on the configured endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class LLMResult:
    fields: dict[str, str | None]
    model: str
    usage: dict[str, int]


def parse_completion(response: object, requested_model: str) -> LLMResult:
    if not isinstance(response, dict):
        raise InvoiceFormatError("LLM API returned an unexpected response shape.")
    if response.get("error"):
        raise APIError("LLM API reported an error. Check the provider dashboard.")
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise InvoiceFormatError("LLM API must return exactly one completion choice.")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise InvoiceFormatError(
            "Model did not finish normally (possibly output limit or filtering). "
            "No invoice fields were saved."
        )
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("refusal") or message.get("tool_calls"):
        raise InvoiceFormatError("Model refused extraction or requested an unsupported tool.")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise InvoiceFormatError("Model returned no JSON text.")
    fields = parse_invoice_fields(content)
    actual_model = response.get("model")
    if not isinstance(actual_model, str) or not actual_model.strip():
        actual_model = requested_model
    usage = response.get("usage")
    safe_usage = {}
    if isinstance(usage, dict):
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(name)
            if type(value) is int and value >= 0:
                safe_usage[name] = value
    return LLMResult(fields, actual_model, safe_usage)


def request_invoice(payload: dict, settings: LLMSettings) -> LLMResult:
    """Do not retry automatically; never expose raw API errors or credentials."""
    request = Request(
        settings.base_url + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + settings.api_key,
                 "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=settings.timeout_seconds) as stream:
            raw = stream.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        explanations = {
            400: "Check model ID and response-format support; try --response-format json_object if supported.",
            401: "Check LLM_API_KEY.",
            402: "Check the provider account balance.",
            403: "Check key permissions and model access.",
            404: "Check LLM_BASE_URL and LLM_MODEL.",
            429: "Rate limit reached. Wait before starting another run.",
        }
        explanation = explanations.get(exc.code, "Check the provider service status and configuration.")
        raise APIError(f"LLM API returned HTTP {exc.code}. {explanation}") from None
    except (TimeoutError, URLError, OSError, HTTPException):
        raise APIError("Cannot reach the LLM API or the request timed out. Check connection and LLM_BASE_URL.") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise APIError("LLM API response exceeded the allowed size.")
    try:
        response = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise APIError("LLM API returned invalid JSON; check LLM_BASE_URL.") from None
    return parse_completion(response, settings.model)
