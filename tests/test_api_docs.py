"""API documentation: only for signed-in users, JSON API only, every JSON body typed."""
import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services import api_token_service
from tests.auth_helpers import make_user, signed_in_headers


class ApiDocsTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, follow_redirects=False)
        self.addCleanup(self.client.close)

    def schema(self):
        response = self.client.get("/api/openapi.json", headers=signed_in_headers())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_framework_defaults_are_off_and_docs_need_a_sign_in(self):
        self.assertEqual(self.client.get("/openapi.json").status_code, 404)
        self.assertEqual(self.client.get("/redoc").status_code, 404)
        self.assertEqual(self.client.get("/api/openapi.json").status_code, 401)
        self.assertEqual(self.client.get("/docs").status_code, 303)
        page = self.client.get("/docs", headers=signed_in_headers())
        self.assertEqual(page.status_code, 200)
        self.assertIn("/static/vendor/swagger-ui/swagger-ui-bundle.js", page.text)
        self.assertNotIn("<script>", page.text)

    def test_api_tokens_can_read_the_schema(self):
        token = api_token_service.create_token(make_user().id, "docs", 1)["token"]
        response = self.client.get("/api/openapi.json", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 200)

    def test_schema_covers_the_json_api_without_admin_routes_or_pages(self):
        paths = self.schema()["paths"]
        self.assertTrue(all(path.startswith("/api/") for path in paths))
        self.assertFalse([path for path in paths if path.startswith("/api/admin")])
        for path in ("/api/sessions", "/api/sessions/{job_id}/tasks/{task_id}", "/api/sessions/{job_id}/docx",
                     "/api/tokens", "/api/shared-reports/{token}/docx"):
            self.assertIn(path, paths)
        self.assertEqual(paths["/api/sessions"]["post"]["tags"], ["Relatórios"])

    def test_bearer_and_cookie_authentication_are_documented(self):
        schema = self.schema()
        self.assertEqual(schema["components"]["securitySchemes"]["apiToken"]["scheme"], "bearer")
        self.assertIn({"apiToken": []}, schema["security"])

    def test_every_json_body_is_a_typed_model(self):
        schema = self.schema()
        untyped = []
        for path, item in schema["paths"].items():
            for method, operation in item.items():
                body = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
                if body is None:
                    continue
                if "$ref" not in body and not body.get("properties") and not body.get("items", {}).get("type"):
                    untyped.append(f"{method.upper()} {path}")
        self.assertEqual(untyped, [], "Describe JSON bodies with a Pydantic model")
        settings = schema["components"]["schemas"]["SettingsUpdate"]["properties"]
        self.assertEqual(settings["llm_timeout_seconds"]["anyOf"][0]["maximum"], 600)


if __name__ == "__main__":
    unittest.main()
