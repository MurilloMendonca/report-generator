"""Claude Messages API, with optional provider-managed fallback on Opus 5.5."""
import base64
import io
import json

import anthropic
from pypdf import PdfReader

from app.services.llm.errors import LLMContractError, ModelRefusalError
from app.services.llm.providers.base import GenerationRequest, GenerationResult, ProviderAdapter, token_count

MAX_REQUEST_BYTES = 32 * 1024 * 1024


class AnthropicAdapter(ProviderAdapter):
    id = "anthropic"
    label = "Anthropic"
    env_key = "ANTHROPIC_API_KEY"

    def build_client(self, api_key: str, timeout_seconds: int = 45):
        return anthropic.Anthropic(api_key=api_key, base_url="https://api.anthropic.com",
                                   max_retries=0, timeout=timeout_seconds)

    def list_models(self, client) -> list[str]:
        return sorted(model.id for model in client.models.list())

    def supports_pdf(self, model: str) -> bool:
        return str(model).startswith("claude-")

    def classify_error(self, exc) -> str:
        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return "auth"
        if isinstance(exc, anthropic.RateLimitError):
            return "rate_limit"
        if isinstance(exc, anthropic.APITimeoutError):
            return "timeout"
        if isinstance(exc, anthropic.APIConnectionError):
            return "retryable"
        return super().classify_error(exc)

    def generate(self, client, request: GenerationRequest) -> GenerationResult:
        content = request.user
        if request.pdf_bytes is not None:
            if not self.supports_pdf(request.model):
                raise LLMContractError("document_extraction", "Este modelo não aceita PDFs.", retryable=False)
            # Base64 expands the upload; the 32 MB cap is on the entire JSON request.
            if ((len(request.pdf_bytes) + 2) // 3) * 4 >= MAX_REQUEST_BYTES:
                raise LLMContractError("document_extraction", "O PDF excede o limite de 32 MB da requisição Anthropic.", retryable=False)
            page_limit = 600 if request.model.startswith(("claude-opus-5", "claude-sonnet-5", "claude-haiku-5", "claude-fable-5")) else 100
            try:
                pdf = PdfReader(io.BytesIO(request.pdf_bytes))
                if pdf.is_encrypted or len(pdf.pages) > page_limit:
                    raise ValueError
            except Exception:
                raise LLMContractError("document_extraction", f"Envie um PDF válido, sem senha e com até {page_limit} páginas.", retryable=False) from None
            content = [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                   "data": base64.b64encode(request.pdf_bytes).decode("ascii")}},
                {"type": "text", "text": request.user},
            ]
        output_config = {}
        if request.model.startswith(("claude-opus-5", "claude-sonnet-5", "claude-haiku-5", "claude-fable-5")):
            output_config["effort"] = "low" if request.operation in {"title_generation", "refinement_routing"} else "medium"
        if request.output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": anthropic.transform_schema(request.output_schema)}
        kwargs = {"model": request.model, "max_tokens": 16000, "system": request.system,
                  "messages": [{"role": "user", "content": content}], "timeout": request.timeout_seconds,
                  **({"output_config": output_config} if output_config else {})}
        server_fallback = request.server_side_fallback and request.model == "claude-opus-5-5"
        if server_fallback:
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        if request.pdf_bytes is not None and len(json.dumps(kwargs, ensure_ascii=False).encode()) >= MAX_REQUEST_BYTES:
            raise LLMContractError("document_extraction", "O PDF e os textos excedem o limite de 32 MB da requisição Anthropic.", retryable=False)
        response = (client.beta.messages if server_fallback else client.messages).create(**kwargs)
        reason = response.stop_reason
        if reason == "refusal":
            raise ModelRefusalError(self.id, request.model)
        if reason in {"max_tokens", "model_context_window_exceeded"}:
            raise LLMContractError("llm", "Model output was truncated; reduce the requested output size")
        if reason not in {"end_turn", "stop_sequence"}:
            raise LLMContractError("llm", "O modelo não concluiu a resposta solicitada.")
        usage = response.usage
        # Thinking/tool blocks never enter the text parser or generated code.
        text = "".join(block.text for block in response.content if block.type == "text")
        fallback_used = any(item.type == "fallback_message" for item in (getattr(usage, "iterations", None) or []))
        return GenerationResult(text, token_count(usage.input_tokens), token_count(usage.output_tokens),
                                reason, response.model, fallback_used)
