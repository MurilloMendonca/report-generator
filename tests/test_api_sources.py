import io
import socket
import threading
import unittest
import uuid
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.routers import api
from app.services import api_sources, session_manager
from app.services.cleanup_service import delete_entire_session
from app.utils.report_paths import ReportPaths
from tests.auth_helpers import create_session, default_user, signed_in_headers


def address(ip):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return (family, socket.SOCK_STREAM, 6, "", (ip, 443))


class ApiSourceTests(unittest.TestCase):
    def test_blocks_non_https_credentials_fragments_and_nonstandard_ports(self):
        for url in ["http://example.com", "file:///etc/passwd", "https://user:pass@example.com",
                    "https://example.com:8443", "https://example.com/#secret", "https://example.com/\r\nX:yes"]:
            with self.subTest(url=url), patch.object(api_sources.socket, "getaddrinfo") as resolve:
                with self.assertRaises(HTTPException):
                    api_sources.public_api_target(url)
                resolve.assert_not_called()

    def test_blocks_internal_addresses_and_mixed_dns_answers(self):
        for addresses in [["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"], ["::1"],
                          ["::ffff:127.0.0.1"], ["64:ff9b::7f00:1"], ["fc00::1"], ["0.0.0.0"], ["224.0.0.1"],
                          ["93.184.216.34", "192.168.1.1"]]:
            with self.subTest(addresses=addresses), \
                 patch.object(api_sources.socket, "getaddrinfo", return_value=[address(ip) for ip in addresses]):
                with self.assertRaises(HTTPException):
                    api_sources.public_api_target("https://example.com/data")

    def test_pinned_connection_preserves_tls_hostname_without_resolving_again(self):
        with patch.object(api_sources.socket, "socket") as create_socket, \
             patch.object(api_sources.socket, "getaddrinfo") as resolve, \
             patch.object(api_sources.ssl, "create_default_context") as context:
            connection = api_sources.PinnedHTTPSConnection("example.com", "93.184.216.34")
            connection.connect()
            create_socket.return_value.connect.assert_called_once_with(("93.184.216.34", 443))
            context.return_value.wrap_socket.assert_called_once_with(create_socket.return_value, server_hostname="example.com")
            resolve.assert_not_called()

    def fetch(self, body=b'[{"value":12}]', status=200, headers=None, pointer=""):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = status
        response.getheader.side_effect = lambda name, default=None: (headers or {"Content-Type": "application/json"}).get(name, default)
        response.read1.side_effect = io.BytesIO(body).read1
        with patch.object(api_sources.socket, "getaddrinfo", return_value=[address("93.184.216.34")]), \
             patch.object(api_sources, "PinnedHTTPSConnection", return_value=connection):
            try:
                result = api_sources.fetch_json_snapshot("https://example.com/data?limit=10", pointer)
            finally:
                connection.close.assert_called_once()
        connection.request.assert_called_once()
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ("GET", "/data?limit=10"))
        self.assertNotIn("Authorization", kwargs["headers"])
        return result

    def test_imports_json_and_selects_nested_records(self):
        body, hostname = self.fetch(b'{"data":{"items":[{"value":12}]},"other":[{"x":1}]}', pointer="/data/items")
        self.assertEqual(api_sources.parse_json(body), [{"value": 12}])
        self.assertEqual(hostname, "example.com")

    def test_rejects_redirects_error_statuses_wrong_types_compression_and_invalid_json(self):
        cases = [{"status": 302}, {"status": 500}, {"headers": {"Content-Type": "text/html"}},
                 {"headers": {"Content-Type": "application/json", "Content-Encoding": "gzip"}},
                 {"body": b'{"value":NaN}'}, {"body": b'{"value":1e999}'},
                 {"body": b'not json'}, {"pointer": "/missing"}]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(HTTPException):
                self.fetch(**case)

    def test_streaming_limit_applies_without_content_length(self):
        with patch.object(api_sources, "MAX_API_BYTES", 5), self.assertRaises(HTTPException) as error:
            self.fetch()
        self.assertEqual(error.exception.status_code, 413)

    def test_deadline_interrupts_a_stalled_request(self):
        interrupted = threading.Event()
        connection = MagicMock()
        connection.sock.shutdown.side_effect = lambda _how: interrupted.set()

        def stall(*args, **kwargs):
            interrupted.wait(1)
            raise TimeoutError

        connection.request.side_effect = stall
        with patch.object(api_sources, "API_TIMEOUT_SECONDS", 0.02), \
             patch.object(api_sources.socket, "getaddrinfo", return_value=[address("93.184.216.34")]), \
             patch.object(api_sources, "PinnedHTTPSConnection", return_value=connection):
            with self.assertRaises(HTTPException):
                api_sources.fetch_json_snapshot("https://example.com/data")
        self.assertTrue(interrupted.is_set())
        connection.close.assert_called_once()

    def test_json_pointer_escaping_and_array_index(self):
        self.assertEqual(api_sources.select_records({"a/b": [{"~key": 7}]}, "/a~1b/0/~0key"), 7)
        for pointer in ["data", "/a~2", "/-1", "/01"]:
            with self.subTest(pointer=pointer), self.assertRaises(HTTPException):
                api_sources.select_records([1, 2], pointer)


class ApiSourceRouteTests(unittest.TestCase):
    def test_creates_api_only_report_with_private_downloadable_snapshot(self):
        with patch.object(api_sources, "fetch_json_snapshot", return_value=(b'[{"value":12}]', "example.com")) as fetch, \
             patch.object(api, "run_pipeline") as pipeline, TestClient(app) as client:
            headers = signed_in_headers()
            response = client.post("/api/sessions", headers=headers, data={
                "provider": "deepseek", "api_url": "https://example.com/data?secret=never-store-this",
                "api_records_path": "/items",
            })
            self.assertEqual(response.status_code, 200, response.text)
            job_id = response.json()["job_id"]
            self.addCleanup(delete_entire_session, job_id)
            session = session_manager.get_session(job_id)
            snapshot = session["files"][0]
            self.assertEqual(snapshot["source"]["type"], "api_snapshot")
            self.assertNotIn("never-store-this", str(session))
            self.assertTrue(str(snapshot["path"]).startswith(str(ReportPaths.from_session(session).uploads)))
            downloaded = client.get(f"/api/sessions/{job_id}/files/0/download", headers=headers)
            self.assertEqual(downloaded.json(), [{"value": 12}])
            self.assertEqual(client.get(f"/api/sessions/{job_id}/files/0/download", headers=signed_in_headers(name="Other")).status_code, 404)
            pipeline.assert_called_once_with(job_id)
            fetch.assert_called_once()

    def test_adds_api_snapshot_alongside_an_uploaded_file(self):
        job_id = "test-api-source-" + uuid.uuid4().hex
        paths = ReportPaths.for_new_report(job_id, str(default_user().id)).ensure()
        create_session(job_id, {"job_id": job_id, "files": [], **paths.session_fields()})
        self.addCleanup(delete_entire_session, job_id)
        with patch.object(api_sources, "fetch_json_snapshot", return_value=(b'[{"value":12}]', "example.com")), \
             patch.object(api, "run_pipeline"), TestClient(app) as client:
            response = client.post(f"/api/sessions/{job_id}/files", headers=signed_in_headers(),
                                   data={"api_url": "https://example.com/data"},
                                   files={"files": ("brief.txt", b"Approved budget: 12 EUR.", "text/plain")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(session_manager.get_session(job_id)["files"]), 2)

    def test_empty_sources_are_rejected(self):
        with TestClient(app) as client:
            response = client.post("/api/sessions", headers=signed_in_headers(), data={"provider": "deepseek"})
        self.assertEqual(response.status_code, 400, response.text)


if __name__ == "__main__":
    unittest.main()
