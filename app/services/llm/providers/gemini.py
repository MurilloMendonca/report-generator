"""Google Gemini (and Gemma) through the google-genai SDK."""
from app.services.llm.providers.base import GenerationRequest, GenerationResult, ProviderAdapter, token_count


class GeminiAdapter(ProviderAdapter):
    id = "gemini"
    label = "Google Gemini"
    env_key = "GEMINI_API_KEY"

    def build_client(self, api_key: str, timeout_seconds: int = 45):
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError("google-genai is required for Gemini. Install dependencies with `pip install -r requirements.txt`.") from exc
        return genai.Client(api_key=api_key, http_options={"timeout": timeout_seconds * 1000, "retry_options": {"attempts": 1}})

    def generate(self, client, request: GenerationRequest) -> GenerationResult:
        contents = request.user
        if request.pdf_bytes is not None:
            from google.genai import types
            contents = [request.user, types.Part.from_bytes(data=request.pdf_bytes, mime_type="application/pdf")]
        # Gemma models reject the JSON mime type; the strict parsers cover them.
        wants_json = (request.wants_json_object or request.operation == "artifact_plan") and not request.model.startswith("gemma-")
        response = client.models.generate_content(
            model=request.model,
            contents=contents,
            config={
                "system_instruction": request.system,
                **({"response_mime_type": "application/json"} if wants_json else {}),
                "http_options": {
                    "timeout": request.timeout_seconds * 1000,
                    "retry_options": {"attempts": 1},
                },
            },
        )
        usage = getattr(response, "usage_metadata", None)
        return GenerationResult(
            text=response.text,
            input_tokens=token_count(getattr(usage, "prompt_token_count", None)),
            output_tokens=token_count(getattr(usage, "candidates_token_count", None)),
        )

    def list_models(self, client) -> list[str]:
        return sorted(str(model.name).removeprefix("models/") for model in client.models.list()
                      if "generateContent" in (getattr(model, "supported_actions", None)
                                               or getattr(model, "supported_generation_methods", [])))

    def supports_pdf(self, model: str) -> bool:
        return not str(model).startswith("gemma-")
