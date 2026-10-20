"""DeepSeek, OpenAI and OpenRouter through their fixed chat-completions URLs."""
import os

from openai import OpenAI

from app.services.llm.errors import LLMContractError, ModelRefusalError, ProviderRequestError
from app.services.llm.providers.base import GenerationRequest, GenerationResult, ProviderAdapter, token_count
from app.utils.model_config import MODEL_CATALOG


class OpenAICompatibleAdapter(ProviderAdapter):
    def __init__(self, provider_id: str, label: str, base_url: str | None, env_key: str):
        self.id = provider_id
        self.label = label
        self.base_url = base_url
        self.env_key = env_key

    def build_client(self, api_key: str, timeout_seconds: int = 45):
        headers = {}
        if self.id == "openrouter" and os.getenv("PUBLIC_BASE_URL"):
            headers = {"HTTP-Referer": os.environ["PUBLIC_BASE_URL"],
                       "X-Title": "Report Generator", "X-OpenRouter-Title": "Report Generator"}
        return OpenAI(api_key=api_key, base_url=self.base_url, max_retries=0, timeout=timeout_seconds,
                      default_headers=headers)

    def generate(self, client, request: GenerationRequest) -> GenerationResult:
        if request.pdf_bytes is not None:
            raise ValueError(f"Provider '{self.id}' does not support PDF input.")
        wants_json = request.wants_json_object
        if self.id == "openrouter":
            # The curated models support JSON mode. Unknown routed models keep
            # prompt-only JSON so unsupported response_format never blocks them.
            wants_json = wants_json and request.model in {model["id"] for model in MODEL_CATALOG["openrouter"]}
        response = client.chat.completions.create(
            model=request.model,
            messages=[
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            stream=False,
            timeout=request.timeout_seconds,
            **({"response_format": {"type": "json_object"}} if wants_json else {}),
        )
        error = getattr(response, "error", None)
        if error:
            code = error.get("code") if isinstance(error, dict) else getattr(error, "code", None)
            # OpenRouter can return an error in an HTTP 200 response.
            failure = ProviderRequestError(self.id, "retryable" if code == 429 or (isinstance(code, int) and code >= 500) else "fatal", status_code=code)
            raise failure
        if not response.choices:
            raise LLMContractError("llm", "Model returned no response choices")
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "content_filter" or getattr(choice.message, "refusal", None):
            raise ModelRefusalError(self.id, request.model)
        if getattr(choice, "finish_reason", None) == "length":
            raise LLMContractError("llm", "Model output was truncated; reduce the requested output size")
        usage = getattr(response, "usage", None)
        return GenerationResult(
            text=choice.message.content,
            input_tokens=token_count(getattr(usage, "prompt_tokens", None)),
            output_tokens=token_count(getattr(usage, "completion_tokens", None)),
            stop_reason=getattr(choice, "finish_reason", None),
        )

    def list_models(self, client) -> list[str]:
        if self.id == "openrouter":
            # /models is public; this authenticated call verifies the actual key.
            client.get("/key", cast_to=dict)
        result = []
        for model in client.models.list():
            model_id = str(model.id)
            if self.id == "openai":
                if not model_id.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4", "ft:")):
                    continue
                if any(part in model_id for part in ("image", "audio", "realtime", "transcribe", "tts", "deep-research", "-pro", "-codex")):
                    continue
            if self.id == "openrouter":
                architecture = getattr(model, "architecture", None) or {}
                if "text" not in architecture.get("input_modalities", []) or "text" not in architecture.get("output_modalities", []):
                    continue
            result.append(model_id)
        return sorted(result)
