DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL_BY_PROVIDER = {
    "deepseek": "deepseek-flash",
    "gemini": "gemini-3.8-flash",
    "anthropic": "claude-opus-5-5",
    "openai": "gpt-6.1-sol",
    "openrouter": "openai/gpt-6.1-sol",
}
DEFAULT_MODEL_NAME = DEFAULT_MODEL_BY_PROVIDER[DEFAULT_PROVIDER]

MODEL_CATALOG = {
    # Verified against the providers' official model catalogs for Phase 5.
    "anthropic": [
        {"id": "claude-opus-5-5", "label": "Claude Opus 5.5"},
        {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5"},
        {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5"},
    ],
    "openai": [
        {"id": "gpt-6.1-sol", "label": "GPT-6.1 Sol"},
        {"id": "gpt-6-astra", "label": "GPT-6 Astra"},
        {"id": "gpt-6-luna", "label": "GPT-6 Luna"},
    ],
    "openrouter": [
        {"id": "openai/gpt-6.1-sol", "label": "OpenAI: GPT-6.1 Sol"},
        {"id": "anthropic/claude-sonnet-5.5", "label": "Anthropic: Claude Sonnet 5.5"},
        {"id": "anthropic/claude-opus-5.5", "label": "Anthropic: Claude Opus 5.5"},
    ],
    "deepseek": [
        {"id": "deepseek-flash", "label": "DeepSeek Flash"},
        {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
    ],
    "gemini": [
        {"id": "gemini-3.8-flash", "label": "Gemini 3.8 Flash"},
        {"id": "gemini-3.5-flash-lite", "label": "Gemini 3.5 Flash Lite"},
        {"id": "gemini-4-argon", "label": "Gemini 4 Argon"},
        {"id": "gemini-3.6-flash", "label": "Gemini 3.6 Flash"},
        {"id": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro Preview"},
        {"id": "gemini-3.1-flash-lite", "label": "Gemini 3.1 Flash Lite"},
        {"id": "gemini-3-flash-preview", "label": "Gemini 3 Flash Preview"},
        {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro (legacy access)"},
        {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash (legacy access)"},
        {"id": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite (legacy access)"},
        {"id": "gemma-4-31b-it", "label": "Gemma 4 31B IT"},
        {"id": "gemma-4-26b-a4b-it", "label": "Gemma 4 26B A4B IT"},
    ],
}

# Keep saved sessions usable after provider shutdowns and endpoint renames.
MODEL_REPLACEMENTS = {
    "deepseek": {"deepseek-v4-flash": "deepseek-flash"},
    "gemini": {
        "gemini-3.1-flash-lite-preview": "gemini-3.1-flash-lite",
        "gemini-3-pro-preview": "gemini-3.1-pro-preview",
        "gemini-3.5-flash": "gemini-3.6-flash",
        "gemini-2.0-flash": "gemini-3.8-flash",
        "gemini-2.0-flash-001": "gemini-3.8-flash",
        "gemini-2.0-flash-lite": "gemini-3.5-flash-lite",
        "gemini-2.0-flash-lite-001": "gemini-3.5-flash-lite",
        "gemini-pro-latest": "gemini-3.1-pro-preview",
        "gemini-flash-latest": "gemini-3.8-flash",
        "gemini-flash-lite-latest": "gemini-3.5-flash-lite",
    },
}


def current_model_name(provider: str, model_name: str | None = None) -> str:
    model_name = str(model_name or DEFAULT_MODEL_BY_PROVIDER.get(provider, "")).strip()
    if provider == "gemini":
        model_name = model_name.removeprefix("models/")
    return MODEL_REPLACEMENTS.get(provider, {}).get(model_name, model_name)
