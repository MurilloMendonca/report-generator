import unittest

from app.services.llm_service import DEFAULT_MODEL_NAME, DEFAULT_PROVIDER, model_catalog, prompt_fingerprint


class LlmContractTests(unittest.TestCase):
    def test_model_catalog_exposes_defaults_and_providers(self):
        catalog = model_catalog()

        self.assertEqual(catalog["default_provider"], DEFAULT_PROVIDER)
        self.assertEqual(catalog["default_model"], DEFAULT_MODEL_NAME)
        self.assertIn(DEFAULT_PROVIDER, catalog["providers"])
        self.assertTrue(catalog["providers"][DEFAULT_PROVIDER])

    def test_prompt_fingerprint_is_stable_sha256_metadata(self):
        fingerprint = prompt_fingerprint("GENERATE_ARTIFACTS.md")

        self.assertEqual(fingerprint["filename"], "GENERATE_ARTIFACTS.md")
        self.assertEqual(len(fingerprint["sha256"]), 64)
        self.assertTrue(fingerprint["version"])


if __name__ == "__main__":
    unittest.main()
