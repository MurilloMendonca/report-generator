import io
import os
import unittest

from fastapi import HTTPException, UploadFile

import app.main as main


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


if __name__ == "__main__":
    unittest.main()
