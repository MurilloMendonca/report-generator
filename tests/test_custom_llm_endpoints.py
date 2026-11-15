"""Custom endpoint configuration, owner credentials and SDK wire compatibility."""
import base64
import json
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import httpx2
import openai
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import transaction
from app.main import app
from app.models.db import LLMUsageEvent
from app.services import credential_service
from app.services.cleanup_service import delete_entire_session
from app.services.llm import client
from app.services.llm.credentials import resolve_api_key
from app.services.llm.errors import InvalidCredentialError, MissingCredentialError
from app.services.llm.providers.base import GenerationRequest
from app.services.llm.providers.openai_compatible import OpenAICompatibleAdapter
from app.services.llm.providers.registry import ADAPTERS
from app.utils.custom_llm_config import load_custom_endpoints
from app.utils.model_config import DEFAULT_MODEL_BY_PROVIDER, MODEL_CATALOG
from tests.auth_helpers import create_session, make_user, signed_in_headers


def endpoint(**values):
    return {"id": "local_test", "label": "Local test", "base_url": "http://localhost:11434/v1",
            "models": ["my-model:8b", "other-model"], **values}


class EndpointConfigurationTests(unittest.TestCase):
    def load(self, values):
        with patch.dict(os.environ, {"PDF_REPORT_LLM_ENDPOINTS": json.dumps(values)}):
            return load_custom_endpoints()

    def test_multiple_endpoints_and_safe_defaults(self):
        first, second = self.load([endpoint(), endpoint(id="private", default_model="other-model",
                                                      requires_api_key=False, json_mode=True)])
        self.assertTrue(first.requires_api_key)
        self.assertFalse(first.json_mode)
        self.assertEqual(first.env_key, "LOCAL_TEST_API_KEY")
        self.assertEqual(second.default_model, "other-model")
        self.assertEqual(self.load([]), ())

    def test_rejects_bad_urls_models_provider_collisions_and_unknown_options(self):
        bad_endpoints = [endpoint(base_url=url) for url in (
            "file:///etc/passwd", "http:///v1", "http://user:password@host/v1",
            "https://host/v1?api_key=secret", "https://host/v1#fragment", "http://host:bad/v1",
            "http://host:99999/v1", "http://[invalid/v1", "http://ho st/v1",
            "http://host/v1/chat/completions",
        )]
        bad_endpoints += [endpoint(id="openai"), endpoint(id="gemini"), endpoint(id="../evil"),
                          endpoint(id="UPPERCASE"), endpoint(models=[]), endpoint(models=[""]),
                          endpoint(models=["m", "m"]), endpoint(models=[" m"]), endpoint(models=[None]),
                          endpoint(default_model="missing"), endpoint(requires_api_key="false"),
                          endpoint(json_mode=1), endpoint(api_key="do-not-store-here")]
        for value in bad_endpoints:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load([value])
        for values in ({"id": "not-a-list"}, [None], [endpoint(), endpoint()]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.load(values)

    def test_invalid_config_never_echoes_secrets(self):
        secret = "planted-custom-endpoint-secret"
        for raw in (secret, json.dumps([endpoint(base_url=f"https://user:{secret}@host/v1")])):
            with patch.dict(os.environ, {"PDF_REPORT_LLM_ENDPOINTS": raw}), self.assertRaises(ValueError) as caught:
                load_custom_endpoints()
            self.assertNotIn(secret, str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)

    def test_process_startup_registers_custom_models_and_keeps_builtin_urls(self):
        code = """
from app.services.llm.providers.registry import get_adapter
from app.services.llm.client import resolve_llm_config
from app.utils.model_config import MODEL_CATALOG
assert resolve_llm_config('local_test', None) == ('local_test', 'other-model')
assert MODEL_CATALOG['local_test'][0]['id'] == 'my-model:8b'
assert get_adapter('local_test').base_url == 'http://localhost:11434/v1'
assert not get_adapter('local_test').requires_api_key
assert get_adapter('openai').base_url == 'https://api.openai.com/v1'
"""
        env = {**os.environ, "PDF_REPORT_LLM_ENDPOINTS": json.dumps([
            endpoint(default_model="other-model", requires_api_key=False),
        ])}
        result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


class CustomAdapterTests(unittest.TestCase):
    def test_actual_sdk_sends_chat_request_to_configured_url_without_json_mode(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx2.Response(200, json={
                "id": "custom-chat", "object": "chat.completion", "created": 1, "model": "my-model:8b",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
            })

        adapter = OpenAICompatibleAdapter("local_test", "Local", "http://local-server:11434/v1", "LOCAL_TEST_API_KEY",
                                          requires_api_key=False, json_mode=False)
        with patch("app.services.llm.providers.openai_compatible.OpenAI", side_effect=lambda **kwargs: openai.OpenAI(
                http_client=httpx2.Client(transport=httpx2.MockTransport(handle)), **kwargs)):
            with adapter.build_client("not-required", timeout_seconds=180) as sdk:
                self.assertEqual(sdk.max_retries, 0)
                self.assertEqual(sdk.timeout, 180)
                result = adapter.generate(sdk, GenerationRequest(
                    model="my-model:8b", system="Return JSON.", user="data", timeout_seconds=180,
                    operation="text_generation"))
        self.assertEqual(str(requests[0].url), "http://local-server:11434/v1/chat/completions")
        body = json.loads(requests[0].content)
        self.assertEqual(body["model"], "my-model:8b")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "Return JSON."})
        self.assertNotIn("response_format", body)
        self.assertEqual((result.text, result.input_tokens, result.output_tokens), ("{}", 9, 3))

    def test_json_mode_opt_in_and_model_discovery_do_not_apply_builtin_filters(self):
        adapter = OpenAICompatibleAdapter("private", "Private", "https://private.example/v1", "PRIVATE_API_KEY",
                                          json_mode=True)
        fake = Mock()
        fake.chat.completions.create.return_value = NS(
            choices=[NS(finish_reason="stop", message=NS(content="{}"))], usage=None, error=None)
        request = GenerationRequest(model="org/custom", system="s", user="u", timeout_seconds=90,
                                    operation="text_generation")
        adapter.generate(fake, request)
        self.assertEqual(fake.chat.completions.create.call_args.kwargs["response_format"], {"type": "json_object"})
        adapter.generate(fake, replace(request, operation="component_code"))
        self.assertNotIn("response_format", fake.chat.completions.create.call_args.kwargs)
        fake.models.list.return_value = [NS(id="my-model:8b"), NS(id="org/custom")]
        self.assertEqual(adapter.list_models(fake), ["my-model:8b", "org/custom"])
        fake.get.assert_not_called()


class CustomEndpointOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.local = OpenAICompatibleAdapter("local_test", "Local", "http://localhost:11434/v1", "LOCAL_TEST_API_KEY",
                                            requires_api_key=False, json_mode=False)
        self.private = OpenAICompatibleAdapter("private_test", "Private", "https://private.example/v1", "PRIVATE_TEST_API_KEY",
                                              json_mode=False)
        for target, additions in (
            (ADAPTERS, {"local_test": self.local, "private_test": self.private}),
            (DEFAULT_MODEL_BY_PROVIDER, {"local_test": "my-model:8b", "private_test": "private-model"}),
            (MODEL_CATALOG, {"local_test": [{"id": "my-model:8b", "label": "my-model:8b"}],
                             "private_test": [{"id": "private-model", "label": "private-model"}]}),
        ):
            self.enterContext(patch.dict(target, additions))
        self.enterContext(patch.dict(os.environ, {
            "APP_ENCRYPTION_KEYS": "v1:" + base64.b64encode(b"e" * 32).decode(),
            "APP_ENCRYPTION_ACTIVE_KEY": "v1", "APP_ENCRYPTION_KEYS_FILE": "",
            "PDF_REPORT_DEV_SERVER_KEYS": "0",
        }))
        self.a, self.b = make_user(), make_user()
        self.http = self.enterContext(TestClient(app))
        self.headers_a, self.headers_b = signed_in_headers(self.a), signed_in_headers(self.b)

    def test_keyless_catalog_preferences_and_call_still_require_owned_context(self):
        catalog = self.http.get("/api/models", headers=self.headers_a).json()
        self.assertTrue(catalog["credential_status"]["local_test"]["enabled"])
        self.assertFalse(catalog["credential_status"]["local_test"]["requires_api_key"])
        self.assertFalse(catalog["credential_status"]["private_test"]["enabled"])
        response = self.http.put("/api/model-preferences", headers=self.headers_a, json={
            "default_provider": "local_test", "default_model": "my-model:8b",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.http.get("/api/models", headers=self.headers_a).json()["default_provider"], "local_test")
        with self.assertRaises(MissingCredentialError):
            resolve_api_key(None, "local_test")
        with client.llm_operation_context(job_id="test-custom-does-not-exist", user_id=str(self.a.id)):
            with self.assertRaises(MissingCredentialError):
                resolve_api_key(self.a.id, "local_test")
        with client.llm_operation_context(user_id=str(self.a.id)):
            self.assertEqual(resolve_api_key(self.a.id, "local_test"), "not-required")
            with self.assertRaises(ValueError):
                client.resolve_llm_config("local_test", "unconfigured-model")

    def test_keyless_calls_in_production_reserve_usage_for_authoritative_owner(self):
        job_id = "test-custom-local-" + self.a.id.hex
        create_session(job_id, {"provider": "local_test", "model_name": "my-model:8b"}, owner=self.a)
        self.addCleanup(delete_entire_session, job_id)
        fake = Mock()
        fake.chat.completions.create.return_value = NS(
            choices=[NS(finish_reason="stop", message=NS(content="ok"))],
            usage=NS(prompt_tokens=9, completion_tokens=3), error=None)
        with patch.dict(os.environ, {"APP_ENV": "production"}), patch.object(self.local, "build_client", return_value=fake) as build:
            with client.llm_operation_context(job_id=job_id, user_id=str(self.b.id)):
                self.assertEqual(client.call_llm("local_test", None, "sys", "user"), "ok")
        build.assert_called_once_with("not-required", timeout_seconds=45)
        fake.close.assert_called_once()
        with transaction() as tx:
            events = list(tx.execute(select(LLMUsageEvent).where(LLMUsageEvent.report_id == job_id)).scalars())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].user_id, self.a.id)
        self.assertEqual((events[0].input_tokens, events[0].output_tokens), (9, 3))

    def test_private_endpoint_uses_each_owners_encrypted_key_and_sanitizes_failures(self):
        keys = {self.a.id: "private-key-owner-aaaaaaaa", self.b.id: "private-key-owner-bbbbbbbb"}
        with patch.object(self.private, "validate_key", return_value=["private-model"]):
            for user_id, key in keys.items():
                credential_service.put_credential(user_id, "private_test", key)
        for user_id, key in keys.items():
            with client.llm_operation_context(user_id=str(user_id)):
                self.assertEqual(resolve_api_key(user_id, "private_test"), key)
        row = credential_service.get_credential(self.a.id, "private_test")
        self.assertNotIn(keys[self.a.id].encode(), row.ciphertext)
        request = httpx2.Request("POST", self.private.base_url + "/chat/completions")
        error = openai.AuthenticationError(keys[self.a.id], response=httpx2.Response(401, request=request), body={})
        with patch.object(self.private, "build_client", return_value=Mock()), \
             patch.object(self.private, "generate", side_effect=error) as generate:
            with client.llm_operation_context(user_id=str(self.a.id)), self.assertRaises(InvalidCredentialError) as caught:
                client.call_llm("private_test", None, "sys", "user", delay=0)
        self.assertNotIn(keys[self.a.id], str(caught.exception))
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(credential_service.get_credential(self.a.id, "private_test").status, "invalid")
        self.assertEqual(credential_service.get_credential(self.b.id, "private_test").status, "valid")

    def test_production_never_uses_env_key_for_authenticated_custom_endpoint(self):
        with patch.dict(os.environ, {"APP_ENV": "production", "PRIVATE_TEST_API_KEY": "private-env-key"}):
            with client.llm_operation_context(user_id=str(self.a.id)), self.assertRaises(MissingCredentialError):
                resolve_api_key(self.a.id, "private_test")


if __name__ == "__main__":
    unittest.main()
