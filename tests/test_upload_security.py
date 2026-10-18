import io
import os
import uuid
import unittest

import pandas as pd
from fastapi import HTTPException, Response, UploadFile

import app.main as main
import app.routers.api as api
import app.security as security
import app.services.pipeline_service as pipeline_service
import app.services.session_manager as session_manager
import app.services.web_helpers as web_helpers


class DummyRequest:
    def __init__(self, cookies=None, headers=None):
        self.cookies = cookies or {}
        self.headers = headers or {}


class UploadSecurityTests(unittest.TestCase):
    def tearDown(self):
        for filename in os.listdir(web_helpers.UPLOAD_DIR):
            if filename.startswith("test_upload_security_"):
                os.remove(os.path.join(web_helpers.UPLOAD_DIR, filename))

    def upload(self, filename, content):
        return UploadFile(filename=filename, file=io.BytesIO(content))

    def test_rejects_unsupported_upload_extension(self):
        with self.assertRaises(HTTPException) as error:
            web_helpers.validate_upload(self.upload("malware.py", b"print('bad')"))

        self.assertEqual(error.exception.status_code, 400)

    def test_rejects_invalid_pdf_content_after_copy(self):
        with self.assertRaises(HTTPException) as error:
            web_helpers.save_uploaded_files([self.upload("fake.pdf", b"not a pdf")])

        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("Invalid PDF", error.exception.detail)

    def test_rejects_binary_csv_content_after_copy(self):
        with self.assertRaises(HTTPException) as error:
            web_helpers.save_uploaded_files([self.upload("bad.csv", b"a,b\n1,\x002")])

        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("Invalid CSV", error.exception.detail)

    def test_rejects_excel_workbooks_with_too_many_sheets(self):
        path = os.path.join(web_helpers.UPLOAD_DIR, f"test_upload_security_many_sheets_{uuid.uuid4().hex}.xlsx")
        with pd.ExcelWriter(path) as writer:
            for index in range(web_helpers.MAX_EXCEL_SHEETS + 1):
                pd.DataFrame({"value": [index]}).to_excel(writer, sheet_name=f"sheet_{index}", index=False)

        try:
            with self.assertRaises(HTTPException) as error:
                web_helpers.validate_saved_upload(
                    path,
                    ".xlsx",
                    "many_sheets.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

            self.assertEqual(error.exception.status_code, 400)
            self.assertIn("too many sheets", error.exception.detail)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_saved_uploads_include_validation_diagnostics(self):
        saved = web_helpers.save_uploaded_files([self.upload("data.csv", b"a,b\n1,2\n")])

        self.assertEqual(saved[0]["validation"]["status"], "ok")
        self.assertEqual(saved[0]["validation"]["format"], "csv")

    def test_copy_upload_enforces_size_limit_and_removes_partial_file(self):
        old_limit = web_helpers.MAX_UPLOAD_BYTES
        web_helpers.MAX_UPLOAD_BYTES = 4
        destination = os.path.join(web_helpers.UPLOAD_DIR, "test_upload_security_limit.csv")
        try:
            with self.assertRaises(HTTPException) as error:
                web_helpers.copy_upload_with_limit(self.upload("data.csv", b"12345"), destination)
            self.assertEqual(error.exception.status_code, 413)
            self.assertFalse(os.path.exists(destination))
        finally:
            web_helpers.MAX_UPLOAD_BYTES = old_limit

    def test_total_upload_size_per_request_is_limited(self):
        old_limit = web_helpers.MAX_REQUEST_UPLOAD_BYTES
        web_helpers.MAX_REQUEST_UPLOAD_BYTES = 12
        try:
            before = set(os.listdir(web_helpers.UPLOAD_DIR))
            with self.assertRaises(HTTPException) as error:
                web_helpers.save_uploaded_files([self.upload("a.csv", b"a,b\n1,2\n"), self.upload("b.csv", b"a,b\n3,4\n")])
            self.assertEqual(error.exception.status_code, 413)
            self.assertEqual(set(os.listdir(web_helpers.UPLOAD_DIR)), before, "partial uploads are removed")
        finally:
            web_helpers.MAX_REQUEST_UPLOAD_BYTES = old_limit

    def test_delete_session_removes_runtime_artifacts(self):
        import asyncio

        job_id = f"test-delete-{uuid.uuid4().hex}"
        upload_path = os.path.join(web_helpers.UPLOAD_DIR, f"{job_id}.csv")
        script_path = os.path.join(pipeline_service.SCRIPT_DIR, f"{job_id}.py")
        pdf_path = os.path.abspath(os.path.join(pipeline_service.REPORT_DIR, f"{job_id}.pdf"))
        for path, content in [
            (upload_path, "a,b\n1,2\n"),
            (script_path, "print('x')\n"),
            (pdf_path, "%PDF-test"),
            (f"{pdf_path}.manifest.json", "{}"),
        ]:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

        session_manager.create_session(job_id, {
            "job_id": job_id,
            "initial_prompt": "Delete me",
            "files": [{"original_filename": "data.csv", "path": upload_path}],
            "script_path": script_path,
            "pdf_path": pdf_path,
        })

        response = asyncio.run(api.delete_session(job_id))

        self.assertEqual(response["status"], "OK")
        for path in [upload_path, script_path, pdf_path, f"{pdf_path}.manifest.json"]:
            self.assertFalse(os.path.exists(path), path)
        self.assertIsNone(session_manager.get_session(job_id))


if __name__ == "__main__":
    unittest.main()
