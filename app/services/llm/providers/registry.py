"""Available provider adapters, keyed by provider ID."""
from app.services.llm.providers.base import ProviderAdapter
from app.services.llm.providers.gemini import GeminiAdapter
from app.services.llm.providers.openai_compatible import OpenAICompatibleAdapter

ADAPTERS: dict[str, ProviderAdapter] = {
    adapter.id: adapter
    for adapter in (
        GeminiAdapter(),
        OpenAICompatibleAdapter("deepseek", "DeepSeek", "https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    )
}


def get_adapter(provider: str) -> ProviderAdapter:
    try:
        return ADAPTERS[provider]
    except KeyError as exc:
        raise ValueError(f"Unknown LLM provider '{provider}'.") from exc
