"""Typed failures raised by LLM calls: contract violations, budget and timeout limits."""

from typing import Any


class MissingCredentialError(RuntimeError):
    code = "missing_provider_key"

    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(f"Adicione sua chave {provider} em Configurações → Chaves.")

    def as_error_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "provider": self.provider,
                "retryable": False, "settings_url": "/keys"}


class InvalidCredentialError(MissingCredentialError):
    code = "invalid_provider_key"

    def __init__(self, provider: str):
        super().__init__(provider)
        self.args = (f"Sua chave {provider} não é válida. Atualize-a em Configurações → Chaves.",)


class PDFCredentialUnavailableError(RuntimeError):
    def __init__(self):
        super().__init__("PDF digitalizado: adicione uma chave Gemini ou Anthropic para extrair conteúdo.")


class LLMContractError(ValueError):
    def __init__(
        self,
        stage: str,
        detail: str,
        *,
        field: str | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        raw_excerpt: str | None = None,
        cause: Exception | None = None,
    ):
        self.stage = stage
        self.field = field
        self.provider = provider
        self.model_name = model_name
        self.raw_excerpt = raw_excerpt
        self.cause = cause
        message = detail
        if field:
            message = f"{message} [field={field}]"
        if provider or model_name:
            message = f"{message} [provider={provider or 'unknown'} model={model_name or 'unknown'}]"
        super().__init__(message)

    def as_error_dict(self) -> dict[str, Any]:
        error = {
            "code": "llm_contract_error",
            "message": str(self),
            "stage": self.stage,
            "retryable": True,
        }
        if self.field:
            error["field"] = self.field
        if self.provider:
            error["provider"] = self.provider
        if self.model_name:
            error["model_name"] = self.model_name
        if self.raw_excerpt:
            error["raw_excerpt"] = self.raw_excerpt
        if self.cause:
            error["details"] = str(self.cause)
        return error


class LLMBudgetError(RuntimeError):
    def __init__(self, operation: str, budget: int):
        self.operation = operation
        self.budget = budget
        super().__init__(f"LLM call budget exceeded for {operation} ({budget})")

    def as_error_dict(self) -> dict[str, Any]:
        return {
            "code": "llm_budget_exceeded",
            "message": str(self),
            "stage": self.operation,
            "retryable": False,
            "budget": self.budget,
        }


class LLMTimeoutError(RuntimeError):
    def __init__(self, operation: str, timeout_seconds: int):
        self.operation = operation
        self.timeout_seconds = timeout_seconds
        super().__init__(f"LLM call timed out after {timeout_seconds}s for {operation}")

    def as_error_dict(self) -> dict[str, Any]:
        return {
            "code": "llm_timeout",
            "message": str(self),
            "stage": self.operation,
            "retryable": True,
            "timeout_seconds": self.timeout_seconds,
        }
