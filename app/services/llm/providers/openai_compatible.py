"""Providers that speak the OpenAI chat-completions API (DeepSeek today; OpenAI
and OpenRouter in Phase 5), configured by base URL."""
from openai import OpenAI

from app.services.llm.errors import LLMContractError
from app.services.llm.providers.base import GenerationRequest, GenerationResult, ProviderAdapter, token_count


class OpenAICompatibleAdapter(ProviderAdapter):
    def __init__(self, provider_id: str, label: str, base_url: str | None, env_key: str):
        self.id = provider_id
        self.label = label
        self.base_url = base_url
        self.env_key = env_key

    def build_client(self, api_key: str, timeout_seconds: int = 45):
        return OpenAI(api_key=api_key, base_url=self.base_url, max_retries=0, timeout=timeout_seconds)

    def generate(self, client, request: GenerationRequest) -> GenerationResult:
        if request.pdf_bytes is not None:
            raise ValueError(f"Provider '{self.id}' does not support PDF input.")
        response = client.chat.completions.create(
            model=request.model,
            messages=[
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            stream=False,
            timeout=request.timeout_seconds,
            **({"response_format": {"type": "json_object"}} if request.wants_json_object else {}),
        )
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise LLMContractError("llm", "Model output was truncated; reduce the requested output size")
        usage = getattr(response, "usage", None)
        return GenerationResult(
            text=choice.message.content,
            input_tokens=token_count(getattr(usage, "prompt_tokens", None)),
            output_tokens=token_count(getattr(usage, "completion_tokens", None)),
        )

    def list_models(self, client) -> list[str]:
        return sorted(str(model.id) for model in client.models.list())
