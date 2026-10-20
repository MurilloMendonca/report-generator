"""BYOK crypto, HTTP isolation, billing identity and planted-secret regression tests."""
import base64
import io
import json
import logging
import os
import tempfile
import unittest
import uuid
from dataclasses import replace
from unittest.mock import patch

import httpx
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from openai import AuthenticationError
from sqlalchemy import select

from app.db import transaction
from app.main import app
from app.models.db import AuditEvent, ProviderCredential, User
from app.services import credential_service as service, crypto
from app.services.cleanup_service import delete_entire_session
from app.services.llm import client
from app.services.llm.credentials import resolve_api_key
from app.services.llm.errors import InvalidCredentialError, MissingCredentialError, PDFCredentialUnavailableError
from app.services.llm.providers.base import GenerationResult
from app.services.llm.providers.registry import get_adapter
from app.services.redaction import SecretRedactionFilter
from app.services.session_manager import add_log, get_session, update_session
from tests.auth_helpers import TEST_PASSWORD, create_session, make_user, signed_in_headers

KEY_A = 'sk-' + 'ownerAsecret' * 4
KEY_B = 'sk-' + 'ownerBsecret' * 4
MASTER_1 = base64.b64encode(b'1' * 32).decode()
MASTER_2 = base64.b64encode(b'2' * 32).decode()
ENV = {'APP_ENCRYPTION_KEYS': f'v1:{MASTER_1}', 'APP_ENCRYPTION_ACTIVE_KEY': 'v1',
       'APP_ENCRYPTION_KEYS_FILE': '', 'PDF_REPORT_DEV_SERVER_KEYS': '0'}


class CryptoTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_round_trip_randomizes_both_layers(self):
        first = crypto.encrypt_secret(KEY_A, b'owner|provider|id')
        second = crypto.encrypt_secret(KEY_A, b'owner|provider|id')
        self.assertEqual(crypto.decrypt_secret(first, b'owner|provider|id'), KEY_A)
        for field in ('ciphertext', 'nonce', 'wrapped_dek', 'dek_nonce'):
            self.assertNotEqual(getattr(first, field), getattr(second, field))

    def test_wrong_aad_and_every_tampered_field_fail(self):
        secret = crypto.encrypt_secret(KEY_A, b'owner|provider|id')
        with self.assertRaises(InvalidTag):
            crypto.decrypt_secret(secret, b'other|provider|id')
        for field in ('ciphertext', 'nonce', 'wrapped_dek', 'dek_nonce'):
            original = getattr(secret, field)
            changed = bytes([original[0] ^ 1]) + original[1:]
            with self.subTest(field=field), self.assertRaises(InvalidTag):
                crypto.decrypt_secret(replace(secret, **{field: changed}), b'owner|provider|id')

    def test_rotation_preserves_api_ciphertext_and_removes_old_dependency(self):
        secret = crypto.encrypt_secret(KEY_A, b'aad')
        with patch.dict(os.environ, {'APP_ENCRYPTION_KEYS': f'v1:{MASTER_1},v2:{MASTER_2}', 'APP_ENCRYPTION_ACTIVE_KEY': 'v2'}):
            rotated = crypto.rewrap(secret, b'aad')
        self.assertEqual((rotated.ciphertext, rotated.nonce), (secret.ciphertext, secret.nonce))
        self.assertEqual(rotated.kek_version, 2)
        with patch.dict(os.environ, {'APP_ENCRYPTION_KEYS': f'v2:{MASTER_2}', 'APP_ENCRYPTION_ACTIVE_KEY': 'v2'}):
            self.assertEqual(crypto.decrypt_secret(rotated, b'aad'), KEY_A)
            with self.assertRaises(crypto.EncryptionConfigError):
                crypto.decrypt_secret(secret, b'aad')

    def test_bad_configuration_never_echoes_key_material(self):
        for raw in ('', f'v1:{KEY_A}', f'v1:{MASTER_1},v1:{MASTER_2}', 'v1:YWJj'):
            with patch.dict(os.environ, {'APP_ENCRYPTION_KEYS': raw}), self.assertRaises(crypto.EncryptionConfigError) as error:
                crypto.keyring()
            self.assertNotIn(KEY_A, str(error.exception))
            self.assertNotIn(MASTER_1, str(error.exception))

    def test_docker_secret_file_takes_precedence(self):
        with tempfile.NamedTemporaryFile(mode='w') as secret_file:
            secret_file.write(f'v2:{MASTER_2}')
            secret_file.flush()
            with patch.dict(os.environ, {'APP_ENCRYPTION_KEYS_FILE': secret_file.name, 'APP_ENCRYPTION_ACTIVE_KEY': 'v2'}):
                keys, active = crypto.keyring()
                self.assertEqual((keys, active), ({2: b'2' * 32}, 2))

    def test_dev_server_keys_are_refused_in_production_at_startup_and_resolution(self):
        with patch.dict(os.environ, {'APP_ENV': 'production', 'PDF_REPORT_DEV_SERVER_KEYS': '1'}):
            with self.assertRaises(crypto.EncryptionConfigError):
                crypto.validate_configuration()
            with self.assertRaises(crypto.EncryptionConfigError):
                resolve_api_key(None, 'deepseek')
            with self.assertRaises(crypto.EncryptionConfigError):
                with TestClient(app):
                    pass


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.a, self.b = make_user(), make_user()
        self.headers_a, self.headers_b = signed_in_headers(self.a), signed_in_headers(self.b)
        self.http = TestClient(app)
        self.adapter = get_adapter('deepseek')
        self.validation = patch.object(self.adapter, 'validate_key', return_value=['deepseek-flash', 'new-private-model'])
        self.validate = self.validation.start()
        self.addCleanup(self.validation.stop)
        self.addCleanup(self.http.close)

    def put(self, key=KEY_A, owner=None, provider='deepseek'):
        headers = self.headers_b if owner == 'b' else self.headers_a
        return self.http.put(f'/api/credentials/{provider}', headers=headers, json={'api_key': key})

    def job(self, owner=None):
        job_id = f'test-byok-{uuid.uuid4().hex}'
        create_session(job_id, {'provider': 'deepseek', 'model_name': 'deepseek-flash'}, owner=owner or self.a)
        self.addCleanup(delete_entire_session, job_id)
        return job_id

    def test_crud_scopes_by_user_and_never_returns_secret_or_envelope(self):
        response = self.put()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['key_last4'], KEY_A[-4:])
        self.assertEqual(response.json()['model_count'], 2)
        self.assertNotIn(KEY_A, response.text)
        self.assertNotIn('ciphertext', response.text)
        self.assertEqual(self.http.get('/api/credentials', headers=self.headers_b).json(), {'credentials': []})
        self.http.delete('/api/credentials/deepseek', headers=self.headers_b)
        self.assertIsNotNone(service.get_credential(self.a.id, 'deepseek'))
        self.assertEqual(self.http.post('/api/credentials/deepseek/test', headers=self.headers_b).status_code, 400)
        self.assertEqual(self.http.post('/api/credentials/deepseek/test', headers=self.headers_a).status_code, 200)
        self.put(KEY_B)
        row = service.get_credential(self.a.id, 'deepseek')
        self.assertEqual(service.decrypt(row), KEY_B)
        self.assertEqual(len(service.list_credentials(self.a.id)), 1)
        self.http.delete('/api/credentials/deepseek', headers=self.headers_a)
        self.assertIsNone(service.get_credential(self.a.id, 'deepseek'))
        with transaction() as tx:
            events = list(tx.execute(select(AuditEvent.event).where(AuditEvent.user_id == self.a.id)).scalars())
        self.assertTrue({'key_added', 'key_replaced', 'key_tested', 'key_removed'} <= set(events))

    def test_unverified_csrf_and_unknown_provider(self):
        self.assertEqual(self.http.put('/api/credentials/deepseek', json={'api_key': KEY_A}).status_code, 403)
        headers = signed_in_headers(make_user(verified=False))
        self.assertEqual(self.http.put('/api/credentials/deepseek', headers=headers, json={'api_key': KEY_A}).status_code, 403)
        self.assertEqual(self.http.put('/api/credentials/unknown', headers=self.headers_a, json={'api_key': KEY_A}).status_code, 404)
        self.validate.assert_not_called()
        self.assertEqual(self.http.get('/keys', follow_redirects=False).status_code, 303)
        self.assertEqual(self.http.get('/keys', headers=self.headers_a).status_code, 200)

    def test_validation_failure_is_safe_and_preserves_existing_key(self):
        self.put()
        request = httpx.Request('GET', 'https://api.deepseek.com/models')
        response = httpx.Response(401, request=request)
        self.validate.side_effect = AuthenticationError(KEY_B, response=response, body={'key': KEY_B})
        result = self.put(KEY_B)
        self.assertEqual(result.status_code, 400)
        self.assertNotIn(KEY_B, result.text)
        self.assertEqual(service.decrypt(service.get_credential(self.a.id, 'deepseek')), KEY_A)
        result = self.http.post('/api/credentials/deepseek/test', headers=self.headers_a)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(service.get_credential(self.a.id, 'deepseek').status, 'invalid')
        self.validate.side_effect = TimeoutError(KEY_A)
        self.assertEqual(self.put(KEY_B).status_code, 503)
        self.assertEqual(service.decrypt(service.get_credential(self.a.id, 'deepseek')), KEY_A)

    def test_catalog_is_per_user_and_accepts_new_live_model_ids(self):
        self.put()
        mine = self.http.get('/api/models', headers=self.headers_a).json()
        theirs = self.http.get('/api/models', headers=self.headers_b).json()
        self.assertTrue(mine['credential_status']['deepseek']['enabled'])
        self.assertFalse(theirs['credential_status']['deepseek']['enabled'])
        self.assertEqual(theirs['providers']['deepseek'], [])
        self.assertEqual({m['id'] for m in mine['providers']['deepseek']}, {'deepseek-flash', 'new-private-model'})
        with client.llm_operation_context(user_id=str(self.a.id)):
            self.assertEqual(client.resolve_llm_config('deepseek', 'new-private-model'), ('deepseek', 'new-private-model'))
        with client.llm_operation_context(user_id=str(self.b.id)), self.assertRaises(ValueError):
            client.resolve_llm_config('deepseek', 'new-private-model')

    def test_user_preferences_only_allow_own_keys(self):
        self.put()
        body = {'default_provider': 'deepseek', 'default_model': 'new-private-model',
                'llm_fallback_provider': 'deepseek', 'llm_fallback_model_name': 'deepseek-flash'}
        result = self.http.put('/api/model-preferences', headers=self.headers_a, json=body)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.http.get('/api/models', headers=self.headers_a).json()['default_model'], 'new-private-model')
        self.assertEqual(self.http.put('/api/model-preferences', headers=self.headers_b, json=body).status_code, 400)
        self.assertIsNone(self.http.get('/api/model-preferences', headers=self.headers_b).json()['llm_fallback_provider'])

    def test_real_call_uses_report_owner_even_with_conflicting_context_user(self):
        self.put()
        self.put(KEY_B, owner='b')
        job_a, job_b = self.job(), self.job(self.b)
        fake = object()
        with patch.object(self.adapter, 'build_client', return_value=fake) as build, \
             patch.object(self.adapter, 'generate', return_value=GenerationResult('ok')):
            with client.llm_operation_context(job_id=job_a, user_id=str(self.b.id)):
                self.assertEqual(client.call_llm('deepseek', None, 'sys', 'prompt'), 'ok')
            self.assertEqual(build.call_args.args, (KEY_A,))
            with client.llm_operation_context(job_id=job_b):
                client.call_llm('deepseek', None, 'sys', 'prompt')
            self.assertEqual(build.call_args.args, (KEY_B,))
        self.assertNotIn(KEY_A, json.dumps(get_session(job_a)))

    def test_missing_key_does_not_build_client_or_call_llm(self):
        with patch.object(self.adapter, 'build_client') as build, patch.object(self.adapter, 'generate') as generate:
            with client.llm_operation_context(job_id=self.job()), self.assertRaises(MissingCredentialError) as error:
                client.call_llm('deepseek', None, 'sys', 'prompt', delay=0)
            self.assertFalse(error.exception.as_error_dict()['retryable'])
            build.assert_not_called()
            generate.assert_not_called()
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': KEY_A}):
            with self.assertRaises(MissingCredentialError):
                client.get_deepseek_client()
            with client.llm_operation_context(user_id=str(self.a.id)), self.assertRaises(MissingCredentialError):
                client.get_deepseek_client()

    def test_development_env_key_requires_explicit_flag_and_owner(self):
        with patch.dict(os.environ, {'PDF_REPORT_DEV_SERVER_KEYS': '1', 'DEEPSEEK_API_KEY': KEY_A}):
            with client.llm_operation_context(user_id=str(self.a.id)):
                self.assertEqual(resolve_api_key(self.a.id, 'deepseek'), KEY_A)
            with self.assertRaises(MissingCredentialError):
                client.get_deepseek_client()

    def test_auth_error_invalidates_key_once_without_retry_or_fallback(self):
        self.put()
        request = httpx.Request('POST', 'https://api.deepseek.com/chat/completions')
        error = AuthenticationError(KEY_A, response=httpx.Response(401, request=request), body={'key': KEY_A})
        with patch.object(self.adapter, 'build_client', return_value=object()), \
             patch.object(self.adapter, 'generate', side_effect=error) as generate:
            with client.llm_operation_context(job_id=self.job()), self.assertRaises(InvalidCredentialError) as result:
                client.call_llm('deepseek', None, 'sys', 'prompt', delay=0)
            self.assertEqual(generate.call_count, 1)
            self.assertNotIn(KEY_A, str(result.exception))
            with client.llm_operation_context(user_id=str(self.a.id)), self.assertRaises(InvalidCredentialError):
                client.call_llm('deepseek', None, 'sys', 'prompt', delay=0)
            self.assertEqual(generate.call_count, 1)
        row = service.get_credential(self.a.id, 'deepseek')
        self.assertEqual(row.status, 'invalid')
        self.assertNotIn(KEY_A, row.last_error)

    def test_operation_cache_observes_replacement_and_immediate_deletion(self):
        self.put()
        with patch('app.services.credential_service.decrypt', wraps=service.decrypt) as decrypt:
            with client.llm_operation_context(user_id=str(self.a.id)) as context:
                self.assertEqual(resolve_api_key(self.a.id, 'deepseek'), KEY_A)
                with client.llm_operation_context(operation='nested'):
                    self.assertEqual(resolve_api_key(self.a.id, 'deepseek'), KEY_A)
                self.assertEqual(decrypt.call_count, 1)
                self.put(KEY_B)
                self.assertEqual(resolve_api_key(self.a.id, 'deepseek'), KEY_B)
                self.assertEqual(decrypt.call_count, 2)
                self.http.delete('/api/credentials/deepseek', headers=self.headers_a)
                with self.assertRaises(MissingCredentialError):
                    resolve_api_key(self.a.id, 'deepseek')
            self.assertEqual(context['_credentials'], {})

    def test_old_failure_cannot_invalidate_replaced_key(self):
        self.put()
        old = service.get_credential(self.a.id, 'deepseek')
        self.put(KEY_B)
        service.mark_invalid(self.a.id, 'deepseek', old.id, old.ciphertext)
        self.assertEqual(service.get_credential(self.a.id, 'deepseek').status, 'valid')

    def test_fallback_reads_user_preferences_and_ignores_server_and_report_settings(self):
        self.put()
        with patch.object(get_adapter('gemini'), 'validate_key', return_value=['gemini-3.8-flash']):
            self.put(KEY_B, provider='gemini')
        with transaction() as tx:
            user = tx.get(User, self.a.id)
            user.preferences = {'llm_fallback_provider': 'gemini', 'llm_fallback_model_name': 'gemini-3.8-flash'}
        with patch.object(self.adapter, 'build_client', return_value=object()), \
             patch.object(self.adapter, 'generate', side_effect=RuntimeError('down')), \
             patch.object(get_adapter('gemini'), 'build_client', return_value=object()) as build, \
             patch.object(get_adapter('gemini'), 'generate', return_value=GenerationResult('fallback')):
            with client.llm_operation_context(job_id=self.job()):
                self.assertEqual(client.call_llm('deepseek', None, 'sys', 'user', retries=1), 'fallback')
            self.assertEqual(build.call_args.args, (KEY_B,))
        with patch.dict(os.environ, {'PDF_REPORT_LLM_FALLBACK_PROVIDER': 'gemini'}):
            with client.llm_operation_context(user_id=str(self.b.id), settings={'llm_fallback_provider': 'gemini'}) as context:
                self.assertEqual(client._llm_fallback(context), (None, None))

    def test_pdf_selection_only_uses_own_valid_pdf_capable_key(self):
        self.put()
        with client.llm_operation_context(user_id=str(self.a.id)), self.assertRaises(PDFCredentialUnavailableError):
            client._document_pdf_provider('deepseek', 'deepseek-flash')
        with patch.object(get_adapter('gemini'), 'validate_key', return_value=['gemma-4-31b-it', 'gemini-3.8-flash']):
            self.put(KEY_B, provider='gemini')
        with client.llm_operation_context(user_id=str(self.a.id)):
            self.assertEqual(client._document_pdf_provider('deepseek', None), ('gemini', 'gemini-3.8-flash'))
            self.assertEqual(client._document_pdf_provider('gemini', 'gemma-4-31b-it'), ('gemini', 'gemini-3.8-flash'))
        with client.llm_operation_context(user_id=str(self.b.id)), self.assertRaises(PDFCredentialUnavailableError):
            client._document_pdf_provider('deepseek', None)

    def test_database_rewrap_command_only_changes_envelope(self):
        self.put()
        old = service.get_credential(self.a.id, 'deepseek')
        with patch.dict(os.environ, {'APP_ENCRYPTION_KEYS': f'v1:{MASTER_1},v2:{MASTER_2}', 'APP_ENCRYPTION_ACTIVE_KEY': 'v2'}):
            service.rewrap_keys()
            row = service.get_credential(self.a.id, 'deepseek')
            self.assertEqual(row.kek_version, 2)
            self.assertEqual((row.ciphertext, row.nonce), (old.ciphertext, old.nonce))
            self.assertEqual(service.decrypt(row), KEY_A)

    def test_account_deletion_cascades_credentials(self):
        self.put()
        result = self.http.request('DELETE', '/api/auth/account', headers=self.headers_a, json={'password': TEST_PASSWORD})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIsNone(service.get_credential(self.a.id, 'deepseek'))

    def test_explorer_and_translation_keep_credential_error_codes(self):
        from app.services.exploration_service import ExplorationError, run_exploration
        from app.services.web_helpers import translate_then_continue
        job_id = self.job()
        with patch('app.services.exploration_service.generate_exploration_script', side_effect=MissingCredentialError('deepseek')):
            with self.assertRaises(ExplorationError) as error:
                run_exploration(job_id, 'How many rows?')
        self.assertEqual(error.exception.details['code'], 'missing_provider_key')
        self.assertEqual(get_session(job_id)['explore_history'][-1]['error']['settings_url'], '/keys')
        with patch('app.services.web_helpers.translate_workspace', side_effect=InvalidCredentialError('deepseek')):
            translate_then_continue(job_id, 'English', lambda *_: self.fail('must not continue'))
        self.assertEqual(get_session(job_id)['error']['code'], 'invalid_provider_key')

    def test_planted_key_scan_in_responses_logs_and_report_state(self):
        responses = [self.put().text]
        job_id = self.job()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(SecretRedactionFilter())
        logger = logging.getLogger('test.byok.planted')
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.setLevel(logging.INFO)
        logger.info('provider key=%s', KEY_A)
        try:
            raise RuntimeError(KEY_A)
        except RuntimeError:
            logger.exception('failed %s', KEY_A)
        with patch.object(self.adapter, 'build_client', return_value=object()), \
             patch.object(self.adapter, 'generate', side_effect=RuntimeError(KEY_A)):
            with client.llm_operation_context(job_id=job_id):
                try:
                    client.call_llm('deepseek', None, 'sys', 'prompt', retries=1)
                except RuntimeError as error:
                    self.assertNotIn(KEY_A, str(error))
                    logger.exception('provider failed')
        with patch.object(self.adapter, 'build_client', return_value=object()), \
             patch.object(self.adapter, 'generate', return_value=GenerationResult(KEY_A)):
            with client.llm_operation_context(job_id=job_id):
                self.assertEqual(client.call_llm('deepseek', None, 'sys', 'prompt'), '[REDACTED]')
        add_log(job_id, KEY_A)
        update_session(job_id, {'error': {'message': KEY_A}, 'status': KEY_A})
        responses.extend(self.http.get(url, headers=self.headers_a).text for url in (
            '/api/credentials', '/api/models', f'/api/sessions/{job_id}', '/api/sessions',
        ))
        responses.append(self.http.put('/api/credentials/deepseek', headers=self.headers_a, content='{"api_key":"' + KEY_A).text)
        responses.append(self.http.put('/api/credentials/deepseek', headers=self.headers_a, json={'api_key': {'secret': KEY_A}}).text)
        with transaction() as tx:
            row = tx.execute(select(ProviderCredential).where(ProviderCredential.user_id == self.a.id)).scalar_one()
            self.assertNotIn(KEY_A.encode(), row.ciphertext)
            audit = [item.details for item in tx.execute(select(AuditEvent).where(AuditEvent.user_id == self.a.id)).scalars()]
        for output in [*responses, stream.getvalue(), json.dumps(get_session(job_id)), json.dumps(audit)]:
            self.assertNotIn(KEY_A, output)
        self.assertIn('[REDACTED]', stream.getvalue())


if __name__ == '__main__':
    unittest.main()
