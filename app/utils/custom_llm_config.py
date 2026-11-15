"""Operator-defined chat-completions endpoints, loaded once at process startup."""
import json
import os
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


BUILTIN_PROVIDER_IDS = frozenset({"gemini", "deepseek", "anthropic", "openai", "openrouter"})


class CustomLLMEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    label: str = Field(min_length=1, max_length=80)
    base_url: str
    models: list[str] = Field(min_length=1, max_length=1000)
    default_model: str | None = None
    requires_api_key: StrictBool = True
    json_mode: StrictBool = False

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username is not None or url.password is not None
                or url.query or url.fragment or any(char.isspace() for char in value)):
            raise ValueError("Use an HTTP(S) API base URL without credentials, query or fragment.")
        url.port  # Validate the port, including malformed IPv6/port combinations.
        if url.path.rstrip("/").endswith("/chat/completions"):
            raise ValueError("Use the API base URL, not the chat-completions URL.")
        return value.rstrip("/")

    @field_validator("models")
    @classmethod
    def validate_models(cls, values):
        if any(not model.strip() or model != model.strip() or len(model) > 256 for model in values):
            raise ValueError("Model IDs must be nonempty and have no surrounding whitespace.")
        if len(set(values)) != len(values):
            raise ValueError("Model IDs must be unique.")
        return values

    @model_validator(mode="after")
    def validate_provider(self):
        if self.id in BUILTIN_PROVIDER_IDS:
            raise ValueError("Custom endpoints cannot replace a built-in provider.")
        if self.default_model is not None and self.default_model not in self.models:
            raise ValueError("The default model must be in models.")
        return self

    @property
    def env_key(self):
        return self.id.upper().replace("-", "_") + "_API_KEY"


def load_custom_endpoints() -> tuple[CustomLLMEndpoint, ...]:
    try:
        values = json.loads(os.getenv("PDF_REPORT_LLM_ENDPOINTS", "[]"))
        if not isinstance(values, list):
            raise ValueError
        endpoints = tuple(CustomLLMEndpoint.model_validate(value) for value in values)
        if len({endpoint.id for endpoint in endpoints}) != len(endpoints):
            raise ValueError
        return endpoints
    except (ValueError, TypeError):
        # Configuration can contain secrets by mistake; never echo its values.
        raise ValueError(
            "Invalid PDF_REPORT_LLM_ENDPOINTS: expected a JSON array of endpoints with unique "
            "non-built-in id, label, HTTP(S) base_url and nonempty models; optional default_model, "
            "requires_api_key and json_mode. URLs cannot contain credentials, queries or fragments."
        ) from None


# Load .env before the registry and model catalog are imported by web/CLI entry points.
load_dotenv()
CUSTOM_LLM_ENDPOINTS = load_custom_endpoints()
