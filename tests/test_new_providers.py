"""New provider contracts, SDK error mapping, and reports using only an owned key."""
import base64
import io
import json
import os
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import anthropic
import httpx2
import openai
from pypdf import PdfReader, PdfWriter

from app.services import credential_service
from app.services.cleanup_service import delete_entire_session
from app.services.executor_service import probe_bubblewrap
from app.services.llm import client
from app.services.llm.errors import LLMContractError, LLMTimeoutError, ModelRefusalError
from app.services.llm.parsing import parse_llm_artifacts, parse_llm_document_insights, parse_llm_preprocessing_plan, parse_llm_text_map
from app.services.llm.planning import generate_artifacts, generate_data_filter, generate_report_title
from app.services.llm.providers.base import GenerationRequest, GenerationResult
from app.services.llm.providers.registry import ADAPTERS, get_adapter
from app.services.llm.refinement import parse_llm_refinement_decision
from app.services.pipeline_service import DEFAULT_SETTINGS, run_pipeline
from app.services.session_manager import get_session
from app.utils.model_config import DEFAULT_MODEL_BY_PROVIDER
from app.utils.report_paths import ReportPaths
from app.utils.template_library import upsert_layout_template
from tests.auth_helpers import create_session, make_user


def request(**kwargs):
    return GenerationRequest(model='claude-opus-5-5', system='system', user='Return JSON.', timeout_seconds=12, **kwargs)


def pdf_bytes(pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def claude_response(text='{}', reason='end_turn', model='claude-opus-5-5', fallback=False):
    return NS(content=[NS(type='thinking', thinking='private'), NS(type='text', text=text)], stop_reason=reason,
              model=model, usage=NS(input_tokens=17, output_tokens=9,
                                   iterations=[NS(type='fallback_message')] if fallback else None))


def claude_client(response=None):
    fake = Mock()
    fake.messages.create.return_value = response or claude_response()
    fake.beta.messages.create.return_value = response or claude_response()
    return fake


def chat_response(text='{}', finish='stop', refusal=None):
    return NS(choices=[NS(finish_reason=finish, message=NS(content=text, refusal=refusal))],
              usage=NS(prompt_tokens=7, completion_tokens=4))


class AnthropicAdapterTests(unittest.TestCase):
    adapter = get_adapter('anthropic')

    def test_client_uses_fixed_url_timeout_and_no_retries(self):
        with self.adapter.build_client('fake-key', timeout_seconds=12) as sdk:
            self.assertEqual(sdk.max_retries, 0)
            self.assertEqual(sdk.timeout, 12)
            self.assertEqual(str(sdk.base_url).rstrip('/'), 'https://api.anthropic.com')

    def test_text_blocks_tokens_effort_and_default_server_fallback(self):
        fake = claude_client()
        result = self.adapter.generate(fake, request(operation='title_generation'))
        call = fake.beta.messages.create.call_args.kwargs
        self.assertEqual(call['max_tokens'], 16000)
        self.assertEqual(call['system'], 'system')
        self.assertEqual(call['fallbacks'], 'default')
        self.assertEqual(call['betas'], ['server-side-fallback-2026-07-01'])
        self.assertEqual(call['output_config']['effort'], 'low')
        self.assertNotIn('thinking', call)
        self.assertEqual(result.text, '{}')
        self.assertEqual((result.input_tokens, result.output_tokens), (17, 9))

    def test_fallback_can_be_disabled_and_is_not_sent_to_other_models(self):
        for model, enabled in [('claude-opus-5-5', False), ('claude-sonnet-5-5', True), ('claude-haiku-5-5', True)]:
            with self.subTest(model=model):
                fake = claude_client()
                self.adapter.generate(fake, replace(request(), model=model, server_side_fallback=enabled))
                fake.beta.messages.create.assert_not_called()
                self.assertNotIn('fallbacks', fake.messages.create.call_args.kwargs)

    def test_structured_schema_and_prompt_json_without_a_schema(self):
        fake = claude_client()
        self.adapter.generate(fake, request(operation='data_preprocessing', output_schema={
            'type': 'object', 'properties': {'code': {'type': 'string'}}, 'required': ['code'], 'additionalProperties': False,
        }))
        config = fake.beta.messages.create.call_args.kwargs['output_config']
        self.assertEqual(config['format']['type'], 'json_schema')
        self.assertEqual(config['format']['schema']['properties']['code']['type'], 'string')
        self.adapter.generate(fake, request(operation='text_generation'))
        self.assertNotIn('format', fake.beta.messages.create.call_args.kwargs['output_config'])

    def test_pdf_is_base64_before_text(self):
        fake = claude_client()
        raw = pdf_bytes()
        self.adapter.generate(fake, request(pdf_bytes=raw))
        blocks = fake.beta.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertEqual(blocks[0]['type'], 'document')
        self.assertEqual(base64.b64decode(blocks[0]['source']['data']), raw)
        self.assertEqual(blocks[1], {'type': 'text', 'text': 'Return JSON.'})
        self.assertTrue(self.adapter.supports_pdf('claude-sonnet-5-5'))

    def test_invalid_encrypted_oversized_and_too_many_page_pdfs_fail_before_request(self):
        fake = claude_client()
        for raw in (b'not a PDF', pdf_bytes(601)):
            with self.subTest(size=len(raw)), self.assertRaises(LLMContractError) as error:
                self.adapter.generate(fake, request(pdf_bytes=raw))
            self.assertFalse(error.exception.retryable)
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        writer.encrypt('password')
        stream = io.BytesIO()
        writer.write(stream)
        with self.assertRaises(LLMContractError):
            self.adapter.generate(fake, request(pdf_bytes=stream.getvalue()))
        with patch('app.services.llm.providers.anthropic.MAX_REQUEST_BYTES', 10):
            with self.assertRaises(LLMContractError):
                self.adapter.generate(fake, request(pdf_bytes=pdf_bytes()))
        fake.beta.messages.create.assert_not_called()

    def test_request_size_includes_base64_and_prompt(self):
        raw = pdf_bytes()
        fake = claude_client()
        limit = ((len(raw) + 2) // 3) * 4 + 100
        with patch('app.services.llm.providers.anthropic.MAX_REQUEST_BYTES', limit), self.assertRaises(LLMContractError):
            self.adapter.generate(fake, replace(request(pdf_bytes=raw), user='x' * limit))
        fake.beta.messages.create.assert_not_called()

    def test_refusal_has_specific_non_retryable_code_and_discards_partial_content(self):
        with self.assertRaises(ModelRefusalError) as error:
            self.adapter.generate(claude_client(claude_response('partial', reason='refusal')), request())
        self.assertEqual(error.exception.as_error_dict()['code'], 'model_refusal')
        self.assertFalse(error.exception.as_error_dict()['retryable'])
        self.assertNotIn('partial', str(error.exception))

    def test_truncation_and_unfinished_responses_never_reach_parser(self):
        for reason in ['max_tokens', 'model_context_window_exceeded', 'tool_use', 'pause_turn']:
            with self.subTest(reason=reason), self.assertRaises(LLMContractError):
                self.adapter.generate(claude_client(claude_response(reason=reason)), request())

    def test_fallback_response_identifies_serving_model(self):
        result = self.adapter.generate(claude_client(claude_response(model='claude-sonnet-5-5', fallback=True)), request())
        self.assertEqual(result.served_model, 'claude-sonnet-5-5')
        self.assertTrue(result.fallback_used)

    def test_model_list_validation_is_free_and_closes_client(self):
        fake = claude_client()
        fake.models.list.return_value = [NS(id='claude-sonnet-5-5'), NS(id='claude-opus-5-5')]
        with patch.object(self.adapter, 'build_client', return_value=fake) as build:
            self.assertEqual(self.adapter.validate_key('fake'), ['claude-opus-5-5', 'claude-sonnet-5-5'])
        build.assert_called_once_with('fake', timeout_seconds=10)
        fake.close.assert_called_once()
        fake.messages.create.assert_not_called()

    def test_every_sdk_error_class_is_classified(self):
        req = httpx2.Request('POST', 'https://api.anthropic.com/v1/messages')
        for status, cls, category in [(401, anthropic.AuthenticationError, 'auth'), (403, anthropic.PermissionDeniedError, 'auth'),
                                       (429, anthropic.RateLimitError, 'rate_limit'), (529, anthropic.APIStatusError, 'retryable'),
                                       (400, anthropic.BadRequestError, 'fatal')]:
            with self.subTest(status=status):
                error = cls('planted-secret', response=httpx2.Response(status, request=req), body={})
                self.assertEqual(self.adapter.classify_error(error), category)
        self.assertEqual(self.adapter.classify_error(anthropic.APITimeoutError(request=req)), 'timeout')
        self.assertEqual(self.adapter.classify_error(anthropic.APIConnectionError(request=req)), 'retryable')

    def test_actual_sdk_serializes_beta_fields_without_network(self):
        captured = []
        def handle(req):
            captured.append(json.loads(req.content))
            return httpx2.Response(200, json={'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5-5',
                                             'content': [{'type': 'text', 'text': '{}'}], 'stop_reason': 'end_turn',
                                             'stop_sequence': None, 'usage': {'input_tokens': 2, 'output_tokens': 1}})
        with anthropic.Anthropic(api_key='fake', max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handle))) as sdk:
            self.adapter.generate(sdk, request())
        self.assertEqual(captured[0]['fallbacks'], 'default')
        self.assertEqual(captured[0]['output_config']['effort'], 'medium')


class CompatibleProviderTests(unittest.TestCase):
    def test_every_openai_sdk_error_class_is_classified_for_both_providers(self):
        req = httpx2.Request('POST', 'https://api.openai.com/v1/chat/completions')
        for provider in ('openai', 'openrouter'):
            adapter = get_adapter(provider)
            for status, cls, category in [(401, openai.AuthenticationError, 'auth'), (403, openai.PermissionDeniedError, 'auth'),
                                           (429, openai.RateLimitError, 'rate_limit'), (503, openai.APIStatusError, 'retryable'),
                                           (400, openai.BadRequestError, 'fatal')]:
                with self.subTest(provider=provider, status=status):
                    self.assertEqual(adapter.classify_error(cls('private', response=httpx2.Response(status, request=req), body={})), category)
            self.assertEqual(adapter.classify_error(openai.APITimeoutError(request=req)), 'timeout')
            self.assertEqual(adapter.classify_error(openai.APIConnectionError(request=req)), 'retryable')

    def test_openai_and_openrouter_success_json_tokens_and_refusals(self):
        for provider in ('openai', 'openrouter'):
            adapter = get_adapter(provider)
            fake = Mock()
            fake.chat.completions.create.return_value = chat_response()
            req = replace(request(operation='text_generation'), model=DEFAULT_MODEL_BY_PROVIDER[provider])
            result = adapter.generate(fake, req)
            self.assertEqual(result.text, '{}')
            self.assertEqual(result.output_tokens, 4)
            self.assertEqual(fake.chat.completions.create.call_args.kwargs['response_format'], {'type': 'json_object'})
            for response in [chat_response(finish='length'), chat_response(finish='content_filter'), chat_response(refusal='no')]:
                fake.chat.completions.create.return_value = response
                with self.assertRaises(LLMContractError):
                    adapter.generate(fake, req)
            with self.assertRaises(ValueError):
                adapter.generate(fake, replace(req, pdf_bytes=b'%PDF'))

    def test_openrouter_unknown_models_use_prompt_only_json(self):
        fake = Mock()
        fake.chat.completions.create.return_value = chat_response()
        get_adapter('openrouter').generate(fake, replace(request(operation='text_generation'), model='vendor/other'))
        self.assertNotIn('response_format', fake.chat.completions.create.call_args.kwargs)

    def test_fixed_urls_and_optional_attribution_headers(self):
        with patch.dict(os.environ, {'PUBLIC_BASE_URL': 'https://reports.example.com'}):
            for provider, url in [('openai', 'https://api.openai.com/v1'), ('openrouter', 'https://openrouter.ai/api/v1')]:
                with get_adapter(provider).build_client('fake', timeout_seconds=12) as sdk:
                    self.assertEqual(str(sdk.base_url).rstrip('/'), url)
                    self.assertEqual(sdk.max_retries, 0)
                    self.assertEqual(sdk.timeout, 12)
                    if provider == 'openrouter':
                        self.assertEqual(sdk.default_headers['HTTP-Referer'], 'https://reports.example.com')
                        self.assertEqual(sdk.default_headers['X-Title'], 'Report Generator')

    def test_openrouter_key_is_authenticated_before_listing_text_models(self):
        fake = Mock()
        fake.models.list.return_value = [NS(id='vendor/text', architecture={'input_modalities': ['text'], 'output_modalities': ['text']}),
                                         NS(id='vendor/image', architecture={'input_modalities': ['text'], 'output_modalities': ['image']})]
        self.assertEqual(get_adapter('openrouter').list_models(fake), ['vendor/text'])
        fake.get.assert_called_once_with('/key', cast_to=dict)
        fake.get.side_effect = RuntimeError('bad key')
        fake.models.list.reset_mock()
        with self.assertRaises(RuntimeError):
            get_adapter('openrouter').list_models(fake)
        fake.models.list.assert_not_called()

    def test_openai_catalog_excludes_non_chat_endpoints(self):
        fake = Mock()
        fake.models.list.return_value = [NS(id=model) for model in ['gpt-6.1-sol', 'gpt-image-2.5', 'text-embedding-3-small', 'gpt-realtime', 'gpt-5.5-pro']]
        self.assertEqual(get_adapter('openai').list_models(fake), ['gpt-6.1-sol'])

    def test_openrouter_embedded_error_never_returns_partial_response(self):
        fake = Mock()
        fake.chat.completions.create.return_value = NS(error={'code': 503, 'message': 'sk-planted-secret'}, choices=[])
        with self.assertRaises(RuntimeError) as error:
            get_adapter('openrouter').generate(fake, request())
        self.assertTrue(error.exception.retryable)
        self.assertNotIn('sk-planted-secret', str(error.exception))


class NewProviderCallTests(unittest.TestCase):
    def test_anthropic_timeout_maps_to_timeout_error_and_closes_client(self):
        fake = claude_client()
        fake.beta.messages.create.side_effect = anthropic.APITimeoutError(request=httpx2.Request('POST', 'https://api.anthropic.com'))
        with patch.object(get_adapter('anthropic'), 'build_client', return_value=fake), \
             patch('app.services.llm.client.resolve_api_key', return_value='fake'), \
             patch('app.services.llm.client.operation_preferences', return_value={}):
            with self.assertRaises(LLMTimeoutError):
                client.call_llm('anthropic', None, 'sys', 'prompt', retries=1)
        fake.close.assert_called_once()

    def test_rate_limit_honors_retry_after_and_sdk_transport_is_not_httpx(self):
        fake = claude_client()
        rate_error = anthropic.RateLimitError('private body', response=httpx2.Response(429, headers={'retry-after': '2'},
                                                                                   request=httpx2.Request('POST', 'https://api.anthropic.com')), body={})
        fake.beta.messages.create.side_effect = [rate_error, claude_response()]
        with patch.object(get_adapter('anthropic'), 'build_client', return_value=fake), \
             patch('app.services.llm.client.resolve_api_key', return_value='fake'), \
             patch('app.services.llm.client.operation_preferences', return_value={}), \
             patch('app.services.llm.client.time.sleep') as sleep:
            self.assertEqual(client.call_llm('anthropic', None, 'sys', 'prompt', delay=0), '{}')
        sleep.assert_called_once_with(2)
        self.assertEqual(fake.beta.messages.create.call_count, 2)

    def test_refusal_stops_transport_and_contract_retries(self):
        for provider in ('anthropic', 'openai', 'openrouter'):
            fake = claude_client(claude_response(reason='refusal')) if provider == 'anthropic' else Mock()
            if provider != 'anthropic':
                fake.chat.completions.create.return_value = chat_response(refusal='no')
            with patch.object(get_adapter(provider), 'build_client', return_value=fake), \
                 patch('app.services.llm.client.resolve_api_key', return_value='fake'), \
                 patch('app.services.llm.client.operation_preferences', return_value={}), \
                 patch('app.services.llm.client.time.sleep') as sleep:
                with self.assertRaises(ModelRefusalError):
                    generate_artifacts('[]', 'Summary', provider, DEFAULT_MODEL_BY_PROVIDER[provider])
                create = fake.beta.messages.create if provider == 'anthropic' else fake.chat.completions.create
                self.assertEqual(create.call_count, 1)
                sleep.assert_not_called()

    def test_truncation_does_not_multiply_contract_retries_with_transport_retries(self):
        fake = Mock()
        fake.chat.completions.create.return_value = chat_response(finish='length')
        with patch.object(get_adapter('openai'), 'build_client', return_value=fake), \
             patch('app.services.llm.client.resolve_api_key', return_value='fake'), \
             patch('app.services.llm.client.time.sleep') as sleep:
            with self.assertRaises(LLMContractError):
                client.call_llm('openai', None, 'sys', 'prompt', retries=3)
        self.assertEqual(fake.chat.completions.create.call_count, 1)
        sleep.assert_not_called()

    def test_preprocessing_schema_and_fallback_opt_out_reach_adapter(self):
        fake = claude_client()
        fake.messages.create.return_value = claude_response('{"needs_preprocessing":false,"description":"","code":""}')
        with patch.object(get_adapter('anthropic'), 'build_client', return_value=fake), \
             patch('app.services.llm.client.resolve_api_key', return_value='fake'), \
             patch('app.services.llm.client.operation_preferences', return_value={'anthropic_server_side_fallback': False}):
            self.assertFalse(generate_data_filter('[]', 'Summary', 'anthropic', 'claude-opus-5-5')['needs_preprocessing'])
        self.assertIn('format', fake.messages.create.call_args.kwargs['output_config'])
        fake.beta.messages.create.assert_not_called()

    def test_all_json_prompt_contracts_survive_each_adapter(self):
        payloads = [
            ('artifact_plan', '[{"id":"t1","indicator":"Summary","type":"text"}]', parse_llm_artifacts),
            ('data_preprocessing', '{"needs_preprocessing":false,"description":"","code":""}', parse_llm_preprocessing_plan),
            ('text_generation', '{"t1":"Summary"}', parse_llm_text_map),
            ('text_condensation', '{"t1":"Short"}', parse_llm_text_map),
            ('document_extraction', '{"summary":"Summary","metrics":[]}', parse_llm_document_insights),
            ('workspace_translation', '{"artifacts":[],"components":{}}', lambda text: json.loads(text)),
            ('refinement_routing', '{"intent":"clarify","scope":"report","instruction":"Clarify","clarification_question":"What changes?"}',
             lambda text: parse_llm_refinement_decision(text, {})),
        ]
        for provider in ('anthropic', 'openai', 'openrouter'):
            for operation, text, parser in payloads:
                with self.subTest(provider=provider, operation=operation):
                    fake = claude_client(claude_response(text)) if provider == 'anthropic' else Mock()
                    if provider != 'anthropic':
                        fake.chat.completions.create.return_value = chat_response(text)
                    with patch.object(get_adapter(provider), 'build_client', return_value=fake), \
                         patch('app.services.llm.client.resolve_api_key', return_value='fake'), \
                         patch('app.services.llm.client.operation_preferences', return_value={}):
                        with client.llm_operation_context(operation=operation):
                            result = client.call_llm(provider, None, 'Return JSON.', 'Summary')
                    self.assertIsNotNone(parser(result))


class NewProviderOwnedReportTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'APP_ENCRYPTION_KEYS': 'v1:' + base64.b64encode(b'5' * 32).decode(),
            'APP_ENCRYPTION_ACTIVE_KEY': 'v1', 'APP_ENCRYPTION_KEYS_FILE': '', 'PDF_REPORT_DEV_SERVER_KEYS': '0',
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def store_key(self, owner, provider, key='sk-phase5-planted-abcdefghijklmnop'):
        with patch.object(get_adapter(provider), 'validate_key', return_value=[DEFAULT_MODEL_BY_PROVIDER[provider]]):
            credential_service.put_credential(owner.id, provider, key)
        return key

    def report(self, owner, provider):
        job_id = f'test-phase5-{uuid.uuid4().hex}'
        paths = ReportPaths.for_new_report(job_id, str(owner.id)).ensure()
        create_session(job_id, {'provider': provider, 'model_name': DEFAULT_MODEL_BY_PROVIDER[provider],
                                'initial_prompt': 'Summarize revenue.', **paths.session_fields()}, owner=owner)
        self.addCleanup(delete_entire_session, job_id)
        return job_id, paths

    def test_auth_failure_invalidates_only_the_owners_new_provider_key(self):
        for provider in ('anthropic', 'openai', 'openrouter'):
            owner, other = make_user(), make_user()
            self.store_key(owner, provider)
            self.store_key(other, provider, 'sk-other-owner-abcdefghijklmnop')
            module = anthropic if provider == 'anthropic' else openai
            error = module.PermissionDeniedError('sk-planted-secret', response=httpx2.Response(403,
                                                request=httpx2.Request('POST', 'https://provider.example')), body={})
            fake = claude_client() if provider == 'anthropic' else Mock()
            generate = fake.beta.messages.create if provider == 'anthropic' else fake.chat.completions.create
            generate.side_effect = error
            with patch.object(get_adapter(provider), 'build_client', return_value=fake), patch('app.services.llm.client.time.sleep') as sleep:
                with client.llm_operation_context(user_id=owner.id), self.assertRaises(Exception) as failure:
                    client.call_llm(provider, None, 'sys', 'prompt')
            self.assertEqual(failure.exception.as_error_dict()['code'], 'invalid_provider_key')
            self.assertEqual(generate.call_count, 1)
            sleep.assert_not_called()
            self.assertEqual(credential_service.get_credential(owner.id, provider).status, 'invalid')
            self.assertEqual(credential_service.get_credential(other.id, provider).status, 'valid')

    def test_served_by_metadata_and_account_opt_out_survive_real_usage_storage(self):
        from app.routers.credentials import model_preferences, PreferencesBody, save_preferences
        from tests.auth_helpers import request_as
        owner = make_user()
        self.store_key(owner, 'anthropic')
        job_id, _ = self.report(owner, 'anthropic')
        fake = claude_client(claude_response(model='claude-sonnet-5-5', fallback=True))
        with patch.object(get_adapter('anthropic'), 'build_client', return_value=fake):
            with client.llm_operation_context(job_id=job_id):
                client.call_llm('anthropic', None, 'sys', 'prompt')
        fallback = get_session(job_id)['llm_usage']['last_fallback']
        self.assertEqual(fallback['requested_model'], 'claude-opus-5-5')
        self.assertEqual(fallback['served_model'], 'claude-sonnet-5-5')
        self.assertTrue(model_preferences(request_as(owner))['anthropic_server_side_fallback'])
        save_preferences(request_as(owner), PreferencesBody(anthropic_server_side_fallback=False))
        self.assertFalse(model_preferences(request_as(owner))['anthropic_server_side_fallback'])
        with patch.object(get_adapter('anthropic'), 'build_client', return_value=fake):
            with client.llm_operation_context(job_id=job_id):
                client.call_llm('anthropic', None, 'sys', 'prompt')
        self.assertNotIn('fallbacks', fake.messages.create.call_args.kwargs)

    def test_full_report_pipeline_with_only_each_new_providers_own_key(self):
        available, reason = probe_bubblewrap()
        if not available:
            self.skipTest(reason)
        for provider in ('anthropic', 'openai', 'openrouter'):
            with self.subTest(provider=provider):
                owner = make_user()
                key = self.store_key(owner, provider)
                job_id, paths = self.report(owner, provider)
                source = paths.uploads / 'data.csv'
                source.write_text('region,revenue\nNorth,40\nSouth,50\nWest,60\n')
                template = upsert_layout_template({'name': 'Provider contract', 'slots': [
                    {'slot_id': 'kpi_resumo', 'name': 'Summary', 'type': 'text', 'page': 1, 'x': 10, 'y': 30, 'w': 190, 'h': 40},
                    {'slot_id': 'g_chart', 'name': 'Revenue', 'type': 'graphic', 'page': 1, 'x': 10, 'y': 75, 'w': 190, 'h': 100},
                    {'slot_id': 'kpi_conclusao', 'name': 'Conclusion', 'type': 'text', 'page': 1, 'x': 10, 'y': 190, 'w': 190, 'h': 50},
                ]}, owner.id)
                from app.services.session_manager import update_session
                update_session(job_id, {'files': [{'original_filename': 'data.csv', 'path': str(source)}],
                                        'settings': {**DEFAULT_SETTINGS, 'selected_template_id': template['id']}})
                artifacts = [
                    {'id': 'kpi_resumo', 'indicator': 'Summary', 'type': 'text'},
                    {'id': 'g_chart', 'indicator': 'Revenue', 'type': 'graphic', 'columns': ['region', 'revenue'],
                     'render_mode': 'generated_code', 'component_preset_id': 'bar_ranked',
                     'data_binding': {'source': 'data.csv', 'roles': {'category': 'region', 'value': 'revenue'}, 'aggregation': 'sum'}},
                    {'id': 'kpi_conclusao', 'indicator': 'Conclusion', 'type': 'text'},
                    {'id': 'm_total', 'indicator': 'Total revenue', 'type': 'metric', 'columns': ['revenue']},
                ]
                texts = {'kpi_resumo': 'Revenue totals 150 across three regions.', 'kpi_conclusao': 'West has the highest revenue at 60.'}
                outputs = {
                    'data_preprocessing': json.dumps({'needs_preprocessing': False, 'description': '', 'code': ''}),
                    'artifact_plan': json.dumps(artifacts), 'title_generation': 'Revenue summary',
                    'metric_value': "print(json.dumps({'value': float(dataframes['data.csv']['revenue'].sum())}))",
                    'text_generation': json.dumps(texts), 'text_condensation': json.dumps(texts),
                    'component_code': "def render_g_chart(pdf, dataframes, x, y, w, h):\n    pdf.set_xy(x, y)\n    pdf.set_font('helvetica', '', 10)\n    pdf.cell(w, 8, 'Revenue: North 40, South 50, West 60')",
                }
                operations = []
                def emit(**kwargs):
                    operation = client.current_llm_context()['operation']
                    operations.append(operation)
                    self.assertIn(operation, outputs, 'unexpected repair or generation step')
                    text = outputs[operation]
                    return claude_response(text) if provider == 'anthropic' else chat_response(text)
                fake = claude_client() if provider == 'anthropic' else Mock()
                create = fake.beta.messages.create if provider == 'anthropic' else fake.chat.completions.create
                create.side_effect = emit
                with patch.object(get_adapter(provider), 'build_client', return_value=fake) as build:
                    run_pipeline(job_id)
                session = get_session(job_id)
                self.assertEqual(session['current_step'], 'completed', session.get('error'))
                self.assertTrue(Path(session['pdf_path']).is_file())
                self.assertEqual(session['calculated_metrics']['m_total'], 150)
                self.assertEqual({row.provider for row in credential_service.list_credentials(owner.id)}, {provider})
                self.assertTrue({'artifact_plan', 'title_generation', 'metric_value', 'text_generation'} <= set(operations))
                self.assertTrue(all(call.args[0] == key for call in build.call_args_list))
                self.assertNotIn(key, json.dumps(session))
                self.assertIn('Revenue summary', ''.join(page.extract_text() for page in PdfReader(session['pdf_path']).pages))


if __name__ == '__main__':
    unittest.main()
