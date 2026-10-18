"""The interface every LLM provider adapter implements."""
from dataclasses import dataclass

# Operations whose prompt contracts expect a JSON object back.
JSON_OBJECT_OPERATIONS = frozenset({
    "document_extraction", "document_extraction_pdf", "data_preprocessing",
    "text_generation", "text_condensation", "workspace_translation",
})


@dataclass(frozen=True)
class GenerationRequest:
    model: str
    system: str
    user: str
    timeout_seconds: int
    operation: str = ""
    pdf_bytes: bytes | None = None

    @property
    def wants_json_object(self) -> bool:
        return self.operation in JSON_OBJECT_OPERATIONS


@dataclass(frozen=True)
class GenerationResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderAdapter:
    """One LLM provider. Adapters never retry: call_llm owns retries, budgets
    and timeouts, so SDK clients are built with their own retries disabled."""

    id: str = ""
    label: str = ""
    # Environment variable holding the operator key (development only after BYOK).
    env_key: str = ""

    def build_client(self, api_key: str, timeout_seconds: int = 45):
        raise NotImplementedError

    def generate(self, client, request: GenerationRequest) -> GenerationResult:
        raise NotImplementedError

    def list_models(self, client) -> list[str]:
        """Model IDs the key can use; also the cheapest way to check a key."""
        raise NotImplementedError

    def supports_pdf(self, model: str) -> bool:
        return False

    def classify_error(self, exc) -> str:
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status in {401, 403}:
            return "auth"
        if status == 429:
            return "rate_limit"
        if status in {408, 500, 502, 503, 504}:
            return "retryable"
        return "fatal"

    def validate_key(self, api_key: str) -> list[str]:
        client = self.build_client(api_key, timeout_seconds=10)
        try:
            return self.list_models(client)
        finally:
            client.close()


def token_count(value) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None
