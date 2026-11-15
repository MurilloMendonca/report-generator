"""Available provider adapters, keyed by provider ID."""
from app.services.llm.providers.base import ProviderAdapter
from app.services.llm.providers.anthropic import AnthropicAdapter
from app.services.llm.providers.gemini import GeminiAdapter
from app.services.llm.providers.openai_compatible import OpenAICompatibleAdapter
from app.utils.custom_llm_config import CUSTOM_LLM_ENDPOINTS

ADAPTERS: dict[str, ProviderAdapter] = {
    adapter.id: adapter
    for adapter in (
        GeminiAdapter(),
        OpenAICompatibleAdapter("deepseek", "DeepSeek", "https://api.deepseek.com", "DEEPSEEK_API_KEY"),
        AnthropicAdapter(),
        OpenAICompatibleAdapter("openai", "OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY"),
        OpenAICompatibleAdapter("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    )
}

for endpoint in CUSTOM_LLM_ENDPOINTS:
    ADAPTERS[endpoint.id] = OpenAICompatibleAdapter(
        endpoint.id, endpoint.label, endpoint.base_url, endpoint.env_key,
        requires_api_key=endpoint.requires_api_key, json_mode=endpoint.json_mode,
    )


def get_adapter(provider: str) -> ProviderAdapter:
    try:
        return ADAPTERS[provider]
    except KeyError as exc:
        raise ValueError(f"Unknown LLM provider '{provider}'.") from exc
