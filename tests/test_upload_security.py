import io
import os
import uuid
import unittest

from fastapi import HTTPException, Response, UploadFile

import app.main as main


class DummyRequest:
    def __init__(self, cookies=None, headers=None):
        self.cookies = cookies or {}
        self.headers = headers or {}


class UploadSecurityTests(unittest.TestCase):
    def tearDown(self):
        for filename in os.listdir(main.UPLOAD_DIR):
            if filename.startswith("test_upload_security_"):
                os.remove(os.path.join(main.UPLOAD_DIR, filename))

    def upload(self, filename, content):
        return UploadFile(filename=filename, file=io.BytesIO(content))

    def test_rejects_unsupported_upload_extension(self):
        with self.assertRaises(HTTPException) as error:
            main.validate_upload(self.upload("malware.py", b"print('bad')"))

        self.assertEqual(error.exception.status_code, 400)

    def test_rejects_invalid_pdf_content_after_copy(self):
        with self.assertRaises(HTTPException) as error:
            main.save_uploaded_files([self.upload("fake.pdf", b"not a pdf")])

        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("Invalid PDF", error.exception.detail)

    def test_rejects_binary_csv_content_after_copy(self):
        with self.assertRaises(HTTPException) as error:
            main.save_uploaded_files([self.upload("bad.csv", b"a,b\n1,\x002")])

        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("Invalid CSV", error.exception.detail)

    def test_copy_upload_enforces_size_limit_and_removes_partial_file(self):
        old_limit = main.MAX_UPLOAD_BYTES
        main.MAX_UPLOAD_BYTES = 4
        destination = os.path.join(main.UPLOAD_DIR, "test_upload_security_limit.csv")
        try:
            with self.assertRaises(HTTPException) as error:
                main.copy_upload_with_limit(self.upload("data.csv", b"12345"), destination)
            self.assertEqual(error.exception.status_code, 413)
            self.assertFalse(os.path.exists(destination))
        finally:
            main.MAX_UPLOAD_BYTES = old_limit

    def test_default_secret_is_allowed_only_in_development_mode(self):
        self.assertTrue(main.ALLOW_DEFAULT_SECRET)

    def test_session_token_is_not_raw_access_secret(self):
        token = main.create_session_token()

        self.assertNotEqual(token, main.APP_ACCESS_SECRET)
        self.assertTrue(main.is_valid_session_token(token))
        self.assertFalse(main.is_valid_session_token(main.APP_ACCESS_SECRET))

    def test_login_sets_http_only_session_cookie(self):
        import asyncio

        response = Response()

        payload = asyncio.run(main.login(response, {"secret": main.APP_ACCESS_SECRET}))

        self.assertEqual(payload["status"], "OK")
        cookie_header = response.headers.get("set-cookie", "")
        self.assertIn(main.AUTH_COOKIE_NAME, cookie_header)
        self.assertIn("HttpOnly", cookie_header)
        self.assertNotIn(main.APP_ACCESS_SECRET, cookie_header)

    def test_request_auth_accepts_cookie_or_header_session_token(self):
        token = main.create_session_token()

        self.assertTrue(main.request_has_valid_auth(DummyRequest(cookies={main.AUTH_COOKIE_NAME: token})))
        self.assertTrue(main.request_has_valid_auth(DummyRequest(headers={"x-access-token": token})))
        self.assertFalse(main.request_has_valid_auth(DummyRequest()))

    def test_delete_session_removes_runtime_artifacts(self):
        import asyncio

        job_id = f"test-delete-{uuid.uuid4().hex}"
        upload_path = os.path.join(main.UPLOAD_DIR, f"{job_id}.csv")
        script_path = os.path.join(main.SCRIPT_DIR, f"{job_id}.py")
        pdf_path = os.path.abspath(os.path.join(main.REPORT_DIR, f"{job_id}.pdf"))
        for path, content in [
            (upload_path, "a,b\n1,2\n"),
            (script_path, "print('x')\n"),
            (pdf_path, "%PDF-test"),
            (f"{pdf_path}.manifest.json", "{}"),
        ]:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

        main.create_session(job_id, {
            "job_id": job_id,
            "initial_prompt": "Delete me",
            "files": [{"original_filename": "data.csv", "path": upload_path}],
            "script_path": script_path,
            "pdf_path": pdf_path,
        })

        response = asyncio.run(main.delete_session(job_id))

        self.assertEqual(response["status"], "OK")
        for path in [upload_path, script_path, pdf_path, f"{pdf_path}.manifest.json", main.get_session_path(job_id)]:
            self.assertFalse(os.path.exists(path), path)


if __name__ == "__main__":
    unittest.main()
