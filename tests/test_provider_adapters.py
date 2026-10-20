import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.llm.client import _document_pdf_provider, call_llm, llm_operation_context
from app.services.llm.errors import LLMContractError
from app.services.llm.providers.base import GenerationRequest
from app.services.llm.providers.registry import ADAPTERS, get_adapter


class FakeGemini:
    def __init__(self, usage=None):
        self.calls = []
        outer = self

        class models:
            @staticmethod
            def generate_content(**kwargs):
                outer.calls.append(kwargs)
                return SimpleNamespace(text="{}", usage_metadata=usage)

        self.models = models


class FakeOpenAI:
    def __init__(self, finish_reason="stop", usage=None, content="{}"):
        self.calls = []
        outer = self

        class completions:
            @staticmethod
            def create(**kwargs):
                outer.calls.append(kwargs)
                choice = SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=content))
                return SimpleNamespace(choices=[choice], usage=usage)

        self.chat = SimpleNamespace(completions=completions)


def request(operation="", model="m", pdf_bytes=None):
    return GenerationRequest(model=model, system="sys", user="user", timeout_seconds=30, operation=operation, pdf_bytes=pdf_bytes)


class RegistryTests(unittest.TestCase):
    def test_known_providers_and_unknown_error(self):
        self.assertEqual(set(ADAPTERS), {"gemini", "deepseek", "anthropic", "openai", "openrouter"})
        with self.assertRaises(ValueError):
            get_adapter("nope")

    def test_every_adapter_names_its_env_key(self):
        for adapter in ADAPTERS.values():
            self.assertTrue(adapter.env_key.endswith("_API_KEY"), adapter.id)


class GeminiAdapterTests(unittest.TestCase):
    adapter = get_adapter("gemini")

    def test_json_mime_for_object_operations_and_artifact_plan(self):
        for operation, expected in (("text_generation", True), ("artifact_plan", True), ("component_code", False)):
            client = FakeGemini()
            self.adapter.generate(client, request(operation, "gemini-3.8-flash"))
            config = client.calls[0]["config"]
            self.assertEqual("response_mime_type" in config, expected, operation)
            self.assertEqual(config["http_options"]["retry_options"], {"attempts": 1})
            self.assertEqual(config["http_options"]["timeout"], 30000)

    def test_gemma_never_gets_json_mime_or_pdf(self):
        client = FakeGemini()
        self.adapter.generate(client, request("text_generation", "gemma-4-31b-it"))
        self.assertNotIn("response_mime_type", client.calls[0]["config"])
        self.assertFalse(self.adapter.supports_pdf("gemma-4-31b-it"))
        self.assertTrue(self.adapter.supports_pdf("gemini-3.8-flash"))

    def test_pdf_is_sent_as_a_part(self):
        client = FakeGemini()
        self.adapter.generate(client, request("document_extraction_pdf", "gemini-3.8-flash", b"%PDF-1.4"))
        contents = client.calls[0]["contents"]
        self.assertEqual(contents[0], "user")
        self.assertEqual(len(contents), 2)

    def test_reports_token_counts(self):
        usage = SimpleNamespace(prompt_token_count=120, candidates_token_count=30)
        result = self.adapter.generate(FakeGemini(usage), request(model="gemini-3.8-flash"))
        self.assertEqual((result.input_tokens, result.output_tokens), (120, 30))
        result = self.adapter.generate(FakeGemini(None), request(model="gemini-3.8-flash"))
        self.assertEqual((result.input_tokens, result.output_tokens), (None, None))


class OpenAICompatibleAdapterTests(unittest.TestCase):
    adapter = get_adapter("deepseek")

    def test_json_object_only_for_object_operations(self):
        for operation, expected in (("workspace_translation", True), ("artifact_plan", False)):
            client = FakeOpenAI()
            self.adapter.generate(client, request(operation))
            self.assertEqual("response_format" in client.calls[0], expected, operation)
            self.assertEqual(client.calls[0]["messages"][0], {"role": "system", "content": "sys"})

    def test_truncated_output_is_a_contract_error(self):
        with self.assertRaises(LLMContractError):
            self.adapter.generate(FakeOpenAI(finish_reason="length"), request())

    def test_pdf_is_refused(self):
        self.assertFalse(self.adapter.supports_pdf("deepseek-flash"))
        with self.assertRaises(ValueError):
            self.adapter.generate(FakeOpenAI(), request(pdf_bytes=b"%PDF"))

    def test_reports_token_counts(self):
        usage = SimpleNamespace(prompt_tokens=50, completion_tokens=7)
        result = self.adapter.generate(FakeOpenAI(usage=usage), request())
        self.assertEqual((result.input_tokens, result.output_tokens), (50, 7))

    def test_sdk_retries_are_disabled(self):
        client = self.adapter.build_client("test-key")
        self.assertEqual(client.max_retries, 0)
        self.assertEqual(str(client.base_url).rstrip("/"), "https://api.deepseek.com")


class CallLlmIntegrationTests(unittest.TestCase):
    def test_token_counts_reach_usage_records(self):
        usage = SimpleNamespace(prompt_tokens=11, completion_tokens=4)
        with patch("app.services.llm.client.get_deepseek_client", return_value=FakeOpenAI(usage=usage, content="ok")), \
             patch("app.services.llm.client.record_llm_usage") as record:
            with llm_operation_context(job_id="job-tokens", operation="component_code"):
                self.assertEqual(call_llm("deepseek", "deepseek-flash", "sys", "user"), "ok")
        self.assertEqual(record.call_args.kwargs["input_tokens"], 11)
        self.assertEqual(record.call_args.kwargs["output_tokens"], 4)

    def test_empty_response_is_a_contract_error(self):
        with patch("app.services.llm.client.get_deepseek_client", return_value=FakeOpenAI(content="  ")), \
             patch("app.services.llm.client.record_llm_usage"):
            with self.assertRaises(LLMContractError):
                call_llm("deepseek", "deepseek-flash", "sys", "user", retries=1)

    def test_pdf_provider_falls_back_to_a_pdf_capable_model(self):
        with patch("app.services.llm.client.resolve_api_key", return_value="fake-owned-key"), \
             patch("app.services.llm.client.operation_user_id", return_value="fake-owner"), \
             patch("app.services.llm.client.credential_service.model_catalog", return_value={
                 "providers": {"deepseek": [], "gemini": [{"id": "gemini-3.8-flash", "label": "Gemini"}]},
             }):
            self.assertEqual(_document_pdf_provider("gemini", "gemini-3.8-flash"), ("gemini", "gemini-3.8-flash"))
            self.assertEqual(_document_pdf_provider("deepseek", "deepseek-flash")[0], "gemini")
            provider, model = _document_pdf_provider("gemini", "gemma-4-31b-it")
            self.assertEqual(provider, "gemini")
            self.assertFalse(model.startswith("gemma-"))


if __name__ == "__main__":
    unittest.main()
