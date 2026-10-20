"""Saved input sources: ownership, encrypted secrets, snapshots and bounded connectors."""
import asyncio
import base64
import json
import os
from pathlib import Path
import socket
import threading
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import psycopg
from psycopg import sql
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from app import db
from app.main import app
from app.models.db import AuditEvent, SourceConnection, User
from app.models.sources import ApiDataset, ConnectionBody, DatasetBody, PostgresConnectionConfig, PostgresDataset, SourceSelection
from app.services import api_sources, crypto, session_manager as sessions, source_connectors as connectors, source_service as service
from app.services.cleanup_service import delete_entire_session
from app.services.source_errors import SourceError
from app.services.source_imports import import_saved_sources
from app.services.source_network import address_allowed, resolve_host
from app.services import source_network
from app.utils.report_paths import ReportPaths
from tests.auth_helpers import create_session, make_user, signed_in_headers

MASTER = base64.b64encode(b's' * 32).decode()
ENV = {'APP_ENCRYPTION_KEYS': f'v1:{MASTER}', 'APP_ENCRYPTION_ACTIVE_KEY': 'v1', 'APP_ENCRYPTION_KEYS_FILE': ''}
SECRET = 'source-secret-do-not-leak-987654321'
API_CONFIG = {'base_url': 'https://api.example.com/v1', 'auth_mode': 'bearer'}


def connection_body(**changes):
    return ConnectionBody.model_validate({'name': 'Sales API', 'kind': 'api', 'config': API_CONFIG, 'secret': SECRET, **changes})


def api_definition(**changes):
    return ApiDataset.model_validate(changes).model_dump()


class ConnectorTests(unittest.TestCase):
    def test_credentials_are_headers_and_scoped_to_origin(self):
        self.assertEqual(connectors.auth_headers({'auth_mode': 'bearer'}, SECRET), {'Authorization': 'Bearer ' + SECRET})
        self.assertEqual(connectors.auth_headers({'auth_mode': 'api_key', 'header_name': 'X-Key'}, SECRET), {'X-Key': SECRET})
        encoded = base64.b64encode(('me:' + SECRET).encode()).decode()
        self.assertEqual(connectors.auth_headers({'auth_mode': 'basic', 'username': 'me'}, SECRET), {'Authorization': 'Basic ' + encoded})
        definition = api_definition(endpoint='orders', parameters={'from': '2026-01-01'})
        self.assertEqual(connectors.api_dataset_url(API_CONFIG, definition, definition['parameters']), 'https://api.example.com/v1/orders?from=2026-01-01')
        url = connectors.api_dataset_url(API_CONFIG, definition, {'active': True, 'deleted': False, 'optional': None})
        self.assertTrue(url.endswith('?active=true&deleted=false'), url)
        for endpoint in ['https://evil.example/data', '//evil.example/data', 'orders?secret=abc', '\\evil.example', 'x\r\nHost: evil']:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValidationError): ApiDataset(endpoint=endpoint)
        for header in ['Host', 'Cookie', 'Content-Length', 'Proxy-Authorization', 'Sec-Token']:
            with self.subTest(header=header), self.assertRaises(ValidationError): connection_body(config={**API_CONFIG, 'auth_mode': 'api_key', 'header_name': header})

    def test_private_addresses_require_operator_allowlist_and_metadata_stays_blocked(self):
        with patch.dict(os.environ, {'PDF_REPORT_SOURCE_ALLOWED_NETWORKS': ''}):
            for ip in ['127.0.0.1', '10.1.2.3', '169.254.169.254', '::1', '224.0.0.1', '::ffff:8.8.8.8']:
                self.assertFalse(address_allowed(ip), ip)
            self.assertTrue(address_allowed('8.8.8.8'))
        with patch.dict(os.environ, {'PDF_REPORT_SOURCE_ALLOWED_NETWORKS': '10.1.0.0/16,127.0.0.1/32,169.254.0.0/16'}):
            self.assertTrue(address_allowed('10.1.2.3')); self.assertTrue(address_allowed('127.0.0.1'))
            self.assertFalse(address_allowed('10.2.2.3')); self.assertFalse(address_allowed('169.254.169.254'))
        with patch('socket.getaddrinfo', return_value=[(socket.AF_INET, 1, 6, '', ('8.8.8.8', 443)), (socket.AF_INET, 1, 6, '', ('127.0.0.1', 443))]), patch.dict(os.environ, {'PDF_REPORT_SOURCE_ALLOWED_NETWORKS': ''}):
            with self.assertRaises(ValueError): resolve_host('example.com', 443)
            with self.assertRaises(HTTPException): api_sources.public_api_target('https://example.com', address_validator=address_allowed)

    def api_pages(self, definition, pages, **kwargs):
        raw = [(json.dumps(page).encode(), page) for page in pages]
        with patch.object(connectors, '_api_fetch', side_effect=raw) as fetch:
            result = connectors.fetch_api(API_CONFIG, SECRET, definition, definition['parameters'], **kwargs)
        return result, fetch

    def test_next_pagination_imports_all_rows_and_preserves_authenticated_origin(self):
        d = api_definition(records_path='/data', pagination='next', next_path='/next')
        (rows, details), fetch = self.api_pages(d, [{'data': [{'id': 1}], 'next': '/v1/orders?page=2'}, {'data': [{'id': 2}], 'next': None}])
        self.assertEqual(rows, [{'id': 1}, {'id': 2}]); self.assertTrue(details['complete'])
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args_list[1].args[0], 'https://api.example.com/v1/orders?page=2')
        self.assertEqual(fetch.call_args.args[1], {'Authorization': 'Bearer ' + SECRET})

    def test_page_pagination_requires_empty_terminal_page(self):
        d = api_definition(pagination='page', max_pages=3)
        (rows, details), fetch = self.api_pages(d, [[{'id': 1}], [{'id': 2}], []])
        self.assertEqual(len(rows), 2); self.assertEqual(details['pages'], 3)
        self.assertIn('page=3', fetch.call_args.args[0])
        with self.assertRaisesRegex(SourceError, 'Nenhum snapshot parcial'): self.api_pages(api_definition(pagination='page', max_pages=1), [[{'id': 1}]])

    def test_cross_origin_and_loop_pagination_fail_without_dispatching_next_request(self):
        for next_link in ['https://evil.example/data', 'https://api.example.com/v1/']:
            d = api_definition(records_path='/data', pagination='next', next_path='/next')
            with patch.object(connectors, '_api_fetch', return_value=(b'{}', {'data': [{'a': 1}], 'next': next_link})) as fetch:
                with self.assertRaises(SourceError): connectors.fetch_api(API_CONFIG, SECRET, d, {})
                fetch.assert_called_once()

    def test_row_byte_caps_preview_and_auth_echo(self):
        d = api_definition()
        with patch.object(connectors, 'MAX_ROWS', 1), self.assertRaises(SourceError): self.api_pages(d, [[{'a': 1}, {'a': 2}]])
        (rows, details), _ = self.api_pages(d, [[{'a': i} for i in range(10)]], preview=True)
        self.assertEqual(len(rows), 5); self.assertFalse(details['complete'])
        with patch.object(connectors, 'fetch_api', return_value=([{'echo': SECRET}], {})):
            with self.assertRaises(SourceError) as error: connectors.read_dataset('api', API_CONFIG, SECRET, d, {})
            self.assertEqual(error.exception.code, 'source_secret_in_data'); self.assertNotIn(SECRET, str(error.exception))
        with patch.object(connectors, 'fetch_api', side_effect=RuntimeError(SECRET)):
            with self.assertRaises(SourceError) as error: connectors.read_dataset('api', API_CONFIG, SECRET, d, {})
            self.assertNotIn(SECRET, str(error.exception))
        with patch.object(connectors, 'fetch_api') as fetch, self.assertRaises(SourceError): connectors.read_dataset('api', API_CONFIG, SECRET, d, {'unknown': 1})
        fetch.assert_not_called()

    def test_dns_deadline_releases_the_request_without_unbounded_outstanding_lookups(self):
        release = threading.Event()
        def stall(*args, **kwargs):
            release.wait(1)
            return []
        try:
            with patch.object(source_network, 'DNS_TIMEOUT_SECONDS', .01), patch.object(socket, 'getaddrinfo', side_effect=stall):
                with self.assertRaises(TimeoutError): source_network.resolve_addresses('example.com', 443)
        finally:
            release.set()

    def test_auth_headers_are_used_only_by_saved_fetch_and_upstream_errors_are_sanitized(self):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheader.side_effect = lambda key, default=None: 'application/json' if key == 'Content-Type' else default
        response.read1.side_effect = [b'[{"value":1}]', b'']
        with patch.object(socket, 'getaddrinfo', return_value=[(socket.AF_INET, 1, 6, '', ('8.8.8.8', 443))]), patch.object(api_sources, 'PinnedHTTPSConnection', return_value=connection):
            rows, details = connectors.fetch_api(API_CONFIG, SECRET, api_definition(endpoint='orders'), {})
        self.assertEqual(rows, [{'value': 1}]); self.assertTrue(details['complete'])
        self.assertEqual(connection.request.call_args.kwargs['headers']['Authorization'], 'Bearer ' + SECRET)
        connection.close.assert_called_once()
        with patch.object(api_sources, 'fetch_json_snapshot', side_effect=HTTPException(400, 'API returned HTTP 401: ' + SECRET)):
            with self.assertRaises(SourceError) as error: connectors.fetch_api(API_CONFIG, SECRET, api_definition(), {})
        self.assertNotIn(SECRET, str(error.exception)); self.assertIn('401', str(error.exception))

    def test_database_fields_cannot_expand_into_libpq_connection_strings(self):
        for database in ['postgresql://evil.example/data', 'host=evil.example', 'db\x00x']:
            with self.assertRaises(ValidationError): connection_body(kind='postgres', config={'host': 'db.example.com', 'username': 'reader', 'database': database})
        with self.assertRaises(ValidationError): PostgresDataset(mode='query', query='DELETE FROM public.orders')
        with self.assertRaises(ValidationError): connection_body(name='   ')

    def test_postgres_tls_defaults_to_verified_and_allows_only_explicit_modes(self):
        config = {'host': 'db.example.com', 'database': 'data', 'username': 'reader'}
        self.assertEqual(PostgresConnectionConfig(**config).sslmode, 'verify-full')
        self.assertEqual(PostgresConnectionConfig(**config, sslmode='disable').sslmode, 'disable')
        for mode in ['prefer', 'allow', 'require', 'verify-ca', '', None, 'disable host=evil.example']:
            with self.subTest(mode=mode), self.assertRaises(ValidationError):
                PostgresConnectionConfig(**config, sslmode=mode)

    def test_failed_tls_is_never_retried_without_encryption(self):
        config = {'host': 'db.example.com', 'port': 5432, 'database': 'data', 'username': 'reader'}
        for message, code in [('server does not support SSL, but SSL was required', 'source_database_tls'),
                              ('certificate verify failed ' + SECRET, 'source_database_error')]:
            with self.subTest(message=code), patch.object(source_network, 'resolve_host', return_value=('db.example.com', '8.8.8.8')), \
                 patch.object(connectors, '_reject_application_database'), \
                 patch.object(psycopg.AsyncConnection, 'connect', new_callable=AsyncMock, side_effect=psycopg.OperationalError(message)) as connect:
                with self.assertRaises(SourceError) as error:
                    connectors.test_connection('postgres', config, SECRET)
                self.assertEqual(error.exception.code, code)
                self.assertNotIn(SECRET, str(error.exception))
                connect.assert_awaited_once()
                self.assertEqual(connect.call_args.kwargs['sslmode'], 'verify-full')
                self.assertEqual(connect.call_args.kwargs['sslrootcert'], 'system')

    def test_database_client_deadline_is_bounded_and_errors_are_safe(self):
        async def stall(): await asyncio.sleep(1)
        with self.assertRaises(SourceError) as error: connectors._bounded_database_read(stall(), .01)
        self.assertEqual(error.exception.code, 'source_timeout')


class SavedSourceHTTPTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV); self.env.start(); self.addCleanup(self.env.stop)
        self.alice, self.bob = make_user(), make_user()
        self.headers, self.bob_headers = signed_in_headers(self.alice), signed_in_headers(self.bob)
        self.client = TestClient(app); self.client.__enter__(); self.addCleanup(self.client.__exit__, None, None, None)
        self.addCleanup(self.cleanup_users)
        self.connection = service.save_connection(self.alice.id, connection_body())
        self.dataset = service.save_dataset(self.alice.id, DatasetBody(name='Orders', connection_id=self.connection['id'], definition={'endpoint': 'orders', 'parameters': {'start_date': '2026-01-01'}}))

    def cleanup_users(self):
        with db.transaction() as tx: tx.execute(delete(User).where(User.id.in_([self.alice.id, self.bob.id])))

    def select(self, **params): return {'dataset_id': self.dataset['id'], 'parameters': params}

    def test_catalog_never_exposes_secrets_and_other_accounts_cannot_mutate_or_read_sources(self):
        response = self.client.get('/api/data-sources', headers=self.headers)
        self.assertEqual(response.status_code, 200); self.assertNotIn(SECRET, response.text); self.assertNotIn('ciphertext', response.text)
        self.assertEqual(self.client.get('/api/data-sources', headers=self.bob_headers).json(), {'connections': [], 'datasets': []})
        connection_url = '/api/data-sources/connections/' + self.connection['id']
        dataset_url = '/api/data-sources/datasets/' + self.dataset['id']
        body = connection_body().model_dump(mode='json'); body.pop('secret')
        requests = [('PUT', connection_url, body), ('DELETE', connection_url, None), ('POST', connection_url + '/test', None), ('PUT', dataset_url, {'name': 'x', 'connection_id': self.connection['id'], 'definition': {}}), ('DELETE', dataset_url, None), ('POST', dataset_url + '/preview', self.select())]
        with patch.object(connectors, 'test_connection') as test, patch.object(connectors, 'read_dataset') as read:
            for method, url, payload in requests:
                r = self.client.request(method, url, headers=self.bob_headers, **({'json': payload} if payload is not None else {})); self.assertEqual(r.status_code, 404, r.text)
        test.assert_not_called(); read.assert_not_called()
        r = self.client.post('/api/data-sources/datasets', headers=self.bob_headers, json={'name': 'x', 'connection_id': self.connection['id'], 'definition': {}})
        self.assertEqual(r.status_code, 404)

    def test_credentials_are_encrypted_aad_bound_and_audit_contains_only_metadata(self):
        with db.transaction() as tx:
            row = tx.get(SourceConnection, uuid.UUID(self.connection['id']))
            self.assertNotIn(SECRET.encode(), row.ciphertext); self.assertEqual(service.decrypt(row), SECRET)
            events = tx.execute(select(AuditEvent).where(AuditEvent.user_id == self.alice.id)).scalars().all()
            self.assertNotIn(SECRET, str([event.details for event in events]))
        row.owner_id = self.bob.id
        with self.assertRaises(SourceError): service.decrypt(row)

    def test_rotation_rewraps_source_envelopes(self):
        master2 = base64.b64encode(b't' * 32).decode()
        with patch.dict(os.environ, {'APP_ENCRYPTION_KEYS': f'v1:{MASTER},v2:{master2}', 'APP_ENCRYPTION_ACTIVE_KEY': 'v2'}):
            with db.transaction() as tx: self.assertEqual(service.rewrap_source_keys(tx, 2), 1)
        with patch.dict(os.environ, {'APP_ENCRYPTION_KEYS': f'v2:{master2}', 'APP_ENCRYPTION_ACTIVE_KEY': 'v2'}):
            with db.transaction() as tx: self.assertEqual(service.decrypt(tx.get(SourceConnection, uuid.UUID(self.connection['id']))), SECRET)

    def test_cannot_select_another_accounts_dataset_to_start_a_report(self):
        with patch.object(connectors, 'read_dataset') as read:
            response = self.client.post('/api/sessions', headers=self.bob_headers, data={'provider': 'deepseek', 'source_datasets': json.dumps([self.select()])})
        self.assertEqual(response.status_code, 404); read.assert_not_called()

    def test_connection_edits_preserve_secret_but_destination_changes_require_reentry(self):
        url = '/api/data-sources/connections/' + self.connection['id']
        body = {'name': 'Renamed', 'kind': 'api', 'config': API_CONFIG}
        response = self.client.put(url, headers=self.headers, json=body); self.assertEqual(response.status_code, 200, response.text)
        with db.transaction() as tx: self.assertEqual(service.decrypt(tx.get(SourceConnection, uuid.UUID(self.connection['id']))), SECRET)
        body['config'] = {**API_CONFIG, 'base_url': 'https://other.example.com'}
        response = self.client.put(url, headers=self.headers, json=body); self.assertEqual(response.status_code, 400)
        body['secret'] = SECRET
        self.assertEqual(self.client.put(url, headers=self.headers, json=body).status_code, 200)

    def test_postgres_transport_is_saved_and_legacy_edits_preserve_credentials(self):
        config = {'host': 'db.example.com', 'port': 5432, 'database': 'data', 'username': 'reader'}
        connection = service.save_connection(self.alice.id, connection_body(kind='postgres', config=config))
        # Simulate an existing connection from before the sslmode field existed.
        with db.transaction() as tx:
            tx.get(SourceConnection, uuid.UUID(connection['id'])).config = config
        url = '/api/data-sources/connections/' + connection['id']
        body = {'name': 'Renamed database', 'kind': 'postgres', 'config': config}
        response = self.client.put(url, headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['config']['sslmode'], 'verify-full')
        body['config'] = {**config, 'sslmode': 'disable'}
        self.assertEqual(self.client.put(url, headers=self.headers, json=body).status_code, 400)
        body['secret'] = SECRET
        response = self.client.put(url, headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['config']['sslmode'], 'disable')
        with db.transaction() as tx:
            row = tx.get(SourceConnection, uuid.UUID(connection['id']))
            self.assertEqual(service.decrypt(row), SECRET)
        with patch.object(connectors, 'test_connection', return_value={'ok': True}) as test:
            response = self.client.post(url + '/test', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(test.call_args.args[1]['sslmode'], 'disable')
        self.assertNotIn(SECRET, self.client.get('/api/data-sources', headers=self.headers).text)

    def test_preview_is_explicit_bounded_and_uses_only_saved_parameters(self):
        body = json.dumps([{'id': 1}]).encode()
        with patch.object(connectors, 'read_dataset', return_value=(body, {'row_count': 1, 'complete': False})) as read:
            r = self.client.post('/api/data-sources/datasets/' + self.dataset['id'] + '/preview', headers=self.headers, json=self.select(start_date='2026-02-01'))
            self.assertEqual(r.status_code, 200, r.text); self.assertFalse(r.json()['complete']); self.assertTrue(read.call_args.kwargs['preview'])
            self.assertEqual(read.call_args.args[2], SECRET)
            r = self.client.post('/api/data-sources/datasets/' + self.dataset['id'] + '/preview', headers=self.headers, json=self.select(bad='x')); self.assertEqual(r.status_code, 400)
            self.assertEqual(read.call_count, 1)

    def test_auth_csrf_validation_and_page(self):
        self.assertEqual(self.client.get('/sources', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get('/api/data-sources', headers={'Cookie': ''}).status_code, 401)
        r = self.client.post('/api/data-sources/connections', headers={'Cookie': self.headers['Cookie']}, json={}); self.assertEqual(r.status_code, 403)
        r = self.client.post('/api/data-sources/connections', headers=self.headers, json={'name': 'bad', 'kind': 'api', 'config': {'base_url': SECRET}, 'secret': SECRET})
        self.assertEqual(r.status_code, 422); self.assertNotIn(SECRET, r.text)
        unverified = make_user(verified=False)
        try:
            self.assertEqual(self.client.post('/api/data-sources/connections', headers=signed_in_headers(unverified), json={'name': 'api', 'kind': 'api', 'config': API_CONFIG, 'secret': SECRET}).status_code, 403)
        finally:
            with db.transaction() as tx: tx.execute(delete(User).where(User.id == unverified.id))

    def new_report(self):
        with patch('app.services.source_imports.run_pipeline'), patch.object(connectors, 'read_dataset', return_value=(b'[{"amount":10}]', {'row_count': 1, 'complete': True})):
            r = self.client.post('/api/sessions', headers=self.headers, data={'provider': 'deepseek', 'source_datasets': json.dumps([self.select()])})
        self.assertEqual(r.status_code, 200, r.text)
        job_id = r.json()['job_id']; self.addCleanup(delete_entire_session, job_id)
        return job_id, sessions.get_session(job_id)

    def test_saved_only_report_import_refresh_history_and_private_download(self):
        job_id, original = self.new_report()
        self.assertEqual(len(original['files']), 1); self.assertEqual(original['source_import']['status'], 'completed')
        first = original['files'][0]
        self.assertEqual(first['source']['parameters'], {'start_date': '2026-01-01'})
        self.assertNotIn(SECRET, json.dumps(original)); self.assertEqual(Path(first['path']).read_bytes(), b'[{"amount":10}]')
        with patch('app.services.source_imports.run_pipeline'), patch.object(connectors, 'read_dataset', return_value=(b'[{"amount":20}]', {'row_count': 1, 'complete': True})):
            r = self.client.post(f'/api/sessions/{job_id}/sources/refresh', headers=self.headers, json={'sources': [self.select(start_date='2026-03-01')]})
        self.assertEqual(r.status_code, 202, r.text)
        latest = sessions.get_session(job_id); self.assertEqual(len(latest['files']), 1); self.assertEqual(len(latest['source_snapshot_history']), 2)
        self.assertNotEqual(first['path'], latest['files'][0]['path']); self.assertEqual(first['original_filename'], latest['files'][0]['original_filename'])
        self.assertEqual(Path(first['path']).read_bytes(), b'[{"amount":10}]')
        url = f"/api/sessions/{job_id}/source-snapshots/{first['source']['snapshot_id']}/download"
        response = self.client.get(url, headers=self.headers)
        self.assertEqual(response.content, b'[{"amount":10}]'); self.assertIn('attachment', response.headers['content-disposition'])
        self.assertEqual(self.client.get(url, headers=self.bob_headers).status_code, 404)
        with patch('app.routers.api.run_pipeline'):
            self.assertEqual(self.client.delete(f'/api/sessions/{job_id}/files/0', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get(url, headers=self.headers).status_code, 200)

    def test_browser_empty_file_part_does_not_block_selected_sources(self):
        def post_with_empty_upload(url, data):
            # httpx omits filename="" for an empty filename; encode the browser's
            # actual multipart part so FastAPI receives an empty UploadFile.
            boundary = 'test-browser-empty-upload'
            parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
                     for key, value in data.items()]
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename=""\r\n'
                         f'Content-Type: application/octet-stream\r\n\r\n\r\n--{boundary}--\r\n')
            return self.client.post(url, content=''.join(parts).encode(), headers={
                **self.headers, 'Content-Type': f'multipart/form-data; boundary={boundary}',
            })

        with patch('app.services.source_imports.run_pipeline') as pipeline, patch.object(
            connectors, 'read_dataset', return_value=(b'[{"amount":10}]', {'row_count': 1, 'complete': True})
        ):
            response = post_with_empty_upload(
                '/api/sessions', {'provider': 'deepseek', 'source_datasets': json.dumps([self.select()])},
            )
        self.assertEqual(response.status_code, 200, response.text)
        job_id = response.json()['job_id']
        self.addCleanup(delete_entire_session, job_id)
        session = sessions.get_session(job_id)
        self.assertEqual(session['source_import']['status'], 'completed')
        self.assertEqual(len(session['files']), 1)
        pipeline.assert_called_once_with(job_id)

        other = service.save_dataset(self.alice.id, DatasetBody(
            name='Another endpoint', connection_id=self.connection['id'], definition={}
        ))
        with patch('app.services.source_imports.run_pipeline') as pipeline, patch.object(
            connectors, 'read_dataset', return_value=(b'[{"amount":20}]', {'row_count': 1, 'complete': True})
        ):
            response = post_with_empty_upload(
                f'/api/sessions/{job_id}/files', {'source_datasets': json.dumps([{'dataset_id': other['id']}])},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(sessions.get_session(job_id)['files']), 2)
        pipeline.assert_called_once_with(job_id)
        response = post_with_empty_upload('/api/sessions', {'provider': 'deepseek'})
        self.assertEqual(response.status_code, 400, response.text)

    def test_failed_multi_source_import_is_atomic_and_cleans_staging(self):
        job_id, original = self.new_report()
        other = service.save_dataset(self.alice.id, DatasetBody(name='More', connection_id=self.connection['id'], definition={}))
        refs = service.select_datasets(self.alice.id, [SourceSelection(**self.select()), SourceSelection(dataset_id=other['id'])])
        with patch('app.services.source_imports.run_pipeline'), patch.object(connectors, 'read_dataset', return_value=(b'[{"amount":15}]', {'row_count': 1, 'complete': True})):
            response = self.client.post(f'/api/sessions/{job_id}/files', headers=self.headers, data={'source_datasets': json.dumps([{'dataset_id': other['id']}])})
        self.assertEqual(response.status_code, 200, response.text)
        original = sessions.get_session(job_id)
        token = sessions.queue_source_import(job_id, refs, refresh=True)
        paths = ReportPaths.from_session(original); before = set(paths.uploads.iterdir())
        with patch.object(service, 'read_dataset', side_effect=[(b'[{"amount":20}]', {**original['files'][0]['source']}), SourceError('source_missing', 'Fonte indisponível.')]), patch('app.services.source_imports.run_pipeline') as pipeline:
            import_saved_sources(job_id, token)
        current = sessions.get_session(job_id)
        self.assertEqual(current['files'], original['files']); self.assertEqual(current['source_snapshot_history'], original['source_snapshot_history'])
        self.assertEqual(set(paths.uploads.iterdir()), before); self.assertEqual(current['source_import']['status'], 'failed'); pipeline.assert_not_called()

    def test_selection_is_frozen_busy_imports_reject_without_appending_files(self):
        job_id, original = self.new_report()
        refs = service.select_datasets(self.alice.id, [SourceSelection(**self.select())])
        token = sessions.queue_source_import(job_id, refs, refresh=True)
        paths = ReportPaths.from_session(original); before = set(paths.uploads.iterdir())
        r = self.client.post(f'/api/sessions/{job_id}/files', headers=self.headers, data={'source_datasets': json.dumps([self.select()])}, files={'files': ('more.csv', b'x\n1\n', 'text/csv')})
        self.assertEqual(r.status_code, 409); self.assertEqual(set(paths.uploads.iterdir()), before)
        service.save_dataset(self.alice.id, DatasetBody(name='Changed', connection_id=self.connection['id'], definition={}), self.dataset['id'])
        with patch.object(connectors, 'read_dataset') as read:
            import_saved_sources(job_id, token)
        self.assertEqual(sessions.get_session(job_id)['error']['code'], 'source_changed'); read.assert_not_called()
        self.assertIsNone(sessions.begin_source_import(job_id, token))

    def test_refresh_invalidates_cached_metrics_components_and_preprocessing_before_rebuilding(self):
        job_id, original = self.new_report()
        sessions.update_session(job_id, {'components': {'chart': 'old code'}, 'calculated_metrics': {'total': 10},
            'data_preprocessing': {'evaluated': True, 'needs_preprocessing': False}, 'script': '# stale script'})
        token = sessions.queue_source_import(job_id, service.select_datasets(self.alice.id, [SourceSelection(**self.select())]), refresh=True)
        at_rebuild = []
        with patch('app.services.source_imports.run_pipeline', side_effect=lambda job: at_rebuild.append(sessions.get_session(job))), patch.object(connectors, 'read_dataset', return_value=(b'[{"amount":99}]', {'row_count': 1, 'complete': True})):
            import_saved_sources(job_id, token)
        self.assertEqual(len(at_rebuild), 1)
        rebuilt = at_rebuild[0]
        self.assertEqual(rebuilt['components'], {}); self.assertEqual(rebuilt['calculated_metrics'], {})
        self.assertFalse(rebuilt['data_preprocessing']['evaluated']); self.assertFalse(rebuilt['data_preprocessing']['needs_preprocessing'])
        self.assertEqual(rebuilt['script'], '')
        self.assertNotEqual(rebuilt['files'][0]['path'], original['files'][0]['path'])

    def test_failed_report_rebuild_restores_last_usable_state_but_retains_import_history(self):
        job_id, original = self.new_report()
        paths = ReportPaths.from_session(original)
        pdf = paths.output / (job_id + '.pdf'); pdf.write_bytes(b'%PDF-1.4 previous version')
        sessions.update_session(job_id, {'pdf_version': 1, 'pdf_path': str(pdf), 'current_step': 'completed',
            'artifacts': [{'id': 'summary', 'type': 'text', 'indicator': 'Summary'}], 'template': [{'type': 'text', 'id': 'summary'}]})
        original = sessions.get_session(job_id)
        token = sessions.queue_source_import(job_id, service.select_datasets(self.alice.id, [SourceSelection(**self.select())]), refresh=True)
        def fail_pipeline(job):
            sessions.update_session(job, {'current_step': 'failed', 'artifacts': [], 'template': [], 'error': {'code': 'mock_provider_failure'}})
        with patch('app.services.source_imports.run_pipeline', side_effect=fail_pipeline), patch.object(connectors, 'read_dataset', return_value=(b'[{"amount":99}]', {'row_count': 1, 'complete': True})):
            import_saved_sources(job_id, token)
        latest = sessions.get_session(job_id)
        self.assertEqual(latest['files'], original['files']); self.assertEqual(latest['artifacts'], original['artifacts'])
        self.assertEqual(latest['pdf_version'], 1); self.assertEqual(len(latest['source_snapshot_history']), 2)
        self.assertEqual(pdf.read_bytes(), b'%PDF-1.4 previous version')

    def test_restart_marks_even_a_queued_import_after_a_finished_pipeline_as_interrupted(self):
        job_id, original = self.new_report()
        token = sessions.queue_source_import(job_id, service.select_datasets(self.alice.id, [SourceSelection(**self.select())]), refresh=True)
        sessions.update_session(job_id, {'current_step': 'completed'})
        self.assertIn(job_id, sessions.recover_interrupted_sessions())
        latest = sessions.get_session(job_id)
        self.assertEqual(latest['source_import']['status'], 'failed'); self.assertEqual(latest['error']['code'], 'interrupted')
        self.assertIsNone(sessions.begin_source_import(job_id, token))
        self.assertIsNotNone(sessions.queue_source_import(job_id, service.select_datasets(self.alice.id, [SourceSelection(**self.select())]), refresh=True))

    def test_deleting_connection_removes_definitions_and_keeps_report_snapshot(self):
        job_id, original = self.new_report()
        r = self.client.delete('/api/data-sources/connections/' + self.connection['id'], headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(service.catalog(self.alice.id), {'connections': [], 'datasets': []})
        self.assertTrue(Path(original['files'][0]['path']).is_file())
        self.assertEqual(sessions.get_session(job_id)['files'], original['files'])


class PostgreSQLConnectorTests(unittest.TestCase):
    """Disposable source DB and non-admin role; never query production data."""
    @classmethod
    def setUpClass(cls):
        db.get_engine()
        cls.url = make_url(db.database_url())
        if not cls.url.database.endswith('_test'): raise RuntimeError('Disposable test database required')
        cls.database = 'source_' + uuid.uuid4().hex[:12] + '_test'
        cls.role = 'source_reader_' + uuid.uuid4().hex[:12]
        cls.admin_params = {'host': cls.url.host, 'port': cls.url.port or 5432, 'user': cls.url.username, 'password': cls.url.password}
        with psycopg.connect(**cls.admin_params, dbname='postgres', autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(cls.role), sql.Literal(SECRET)))
            connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.database)))
        with psycopg.connect(**cls.admin_params, dbname=cls.database, autocommit=True) as connection:
            connection.execute('CREATE TABLE public.orders (id integer, amount numeric, created_at date, metadata jsonb)')
            connection.execute("INSERT INTO public.orders SELECT i, 12.50, DATE '2026-01-01', '{\"ok\":true}'::jsonb FROM generate_series(1,8) AS i")
            connection.execute(sql.SQL('GRANT SELECT, INSERT ON public.orders TO {}').format(sql.Identifier(cls.role)))
        cls.config = {'host': cls.url.host, 'port': cls.url.port or 5432, 'database': cls.database, 'username': cls.role}

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(**cls.admin_params, dbname='postgres', autocommit=True) as connection:
            connection.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(cls.database)))
            connection.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(cls.role)))

    def setUp(self):
        self.env = patch.dict(os.environ, {'PDF_REPORT_SOURCE_ALLOWED_NETWORKS': f'{self.url.host}/32'}); self.env.start(); self.addCleanup(self.env.stop)
        self.transport_settings = []
        original = psycopg.AsyncConnection.connect
        async def local_connect(**kwargs):
            self.transport_settings.append(dict(kwargs))
            kwargs['sslmode'] = 'disable'; kwargs.pop('sslrootcert', None)
            return await original(**kwargs)
        self.transport = patch.object(psycopg.AsyncConnection, 'connect', side_effect=local_connect); self.transport.start(); self.addCleanup(self.transport.stop)

    def read(self, **definition):
        d = PostgresDataset.model_validate(definition).model_dump()
        return connectors.read_dataset('postgres', self.config, SECRET, d, {})

    def test_table_and_parameterized_query_stream_typed_records_with_pinned_verified_tls(self):
        body, details = self.read(table_name='orders')
        rows = json.loads(body); self.assertEqual(len(rows), 8); self.assertEqual(rows[0]['created_at'], '2026-01-01'); self.assertEqual(rows[0]['metadata'], {'ok': True}); self.assertTrue(details['complete'])
        body, _ = self.read(mode='query', query='SELECT * FROM public.orders WHERE id = %(id)s', parameters={'id': 3})
        self.assertEqual(json.loads(body)[0]['id'], 3)
        for config in self.transport_settings:
            self.assertEqual(config['sslmode'], 'verify-full'); self.assertEqual(config['sslrootcert'], 'system'); self.assertEqual(config['hostaddr'], self.url.host)
            self.assertIn('default_transaction_read_only=on', config['options']); self.assertEqual(config['passfile'], '/dev/null')

    def test_explicit_plaintext_connection_reads_without_a_transport_override(self):
        self.transport.stop()
        config = {**self.config, 'sslmode': 'disable'}
        self.assertTrue(connectors.test_connection('postgres', config, SECRET)['ok'])
        definition = PostgresDataset(mode='query', query='SELECT * FROM public.orders WHERE id <= %(last)s', parameters={'last': 3}).model_dump()
        body, details = connectors.read_dataset('postgres', config, SECRET, definition, {})
        self.assertEqual([row['id'] for row in json.loads(body)], [1, 2, 3])
        self.assertTrue(details['complete'])
        # The plaintext option must not loosen internal-database protection.
        with self.assertRaises(SourceError) as error:
            connectors.test_connection('postgres', {**config, 'database': self.url.database}, SECRET)
        self.assertEqual(error.exception.code, 'source_destination')

    def test_writes_multiple_statements_and_application_database_are_blocked(self):
        for query in ["WITH inserted AS (INSERT INTO public.orders(id) VALUES (99) RETURNING *) SELECT * FROM inserted", "SELECT 1; INSERT INTO public.orders(id) VALUES (99)", "SELECT 1); INSERT INTO public.orders(id) VALUES (99); --"]:
            with self.subTest(query=query), self.assertRaises(SourceError): self.read(mode='query', query=query)
        with psycopg.connect(**self.admin_params, dbname=self.database) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM public.orders').fetchone()[0], 8)
        config = {**self.config, 'database': self.url.database}
        with self.assertRaises(SourceError) as error: connectors.fetch_postgres(config, SECRET, PostgresDataset(table_name='users').model_dump(), {})
        self.assertEqual(error.exception.code, 'source_destination'); self.assertEqual(len(self.transport_settings), 3)

    def test_client_deadline_cancels_a_live_query(self):
        with patch.object(connectors, 'TIMEOUT_SECONDS', .05), self.assertRaises(SourceError) as error:
            self.read(mode='query', query='SELECT pg_catalog.pg_sleep(2) AS wait')
        self.assertEqual(error.exception.code, 'source_timeout')

    def test_admin_roles_and_oversized_results_are_rejected_and_preview_is_partial(self):
        config = {**self.config, 'username': self.url.username}
        with self.assertRaises(SourceError) as error: connectors.fetch_postgres(config, self.url.password, PostgresDataset(table_name='orders').model_dump(), {})
        self.assertEqual(error.exception.code, 'source_database_role')
        with patch.object(connectors, 'MAX_ROWS', 2), self.assertRaises(SourceError): self.read(table_name='orders')
        with patch.object(connectors, 'MAX_BYTES', 50), self.assertRaises(SourceError): self.read(mode='query', query="SELECT repeat('x', 1000) AS huge")
        rows, details = connectors.fetch_postgres(self.config, SECRET, PostgresDataset(table_name='orders').model_dump(), {}, preview=True)
        self.assertEqual(len(rows), 5); self.assertFalse(details['complete'])


if __name__ == '__main__': unittest.main()
