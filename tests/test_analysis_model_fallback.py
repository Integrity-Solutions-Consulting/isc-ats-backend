"""Model fallback must survive retired models without hiding auth failures."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.modules.ai.application import analysis_service


class ModelError(Exception):
    def __init__(self, code):
        super().__init__(f"API error {code}")
        self.code = code


class AnalysisModelFallbackTests(unittest.TestCase):
    def call_with(self, outcomes):
        generate = Mock(side_effect=outcomes)
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        with (
            patch.object(analysis_service.settings, "gemini_api_key", "test-key"),
            patch.object(analysis_service.settings, "openrouter_api_key", ""),
            patch.object(analysis_service.genai, "Client", return_value=client),
            patch.object(analysis_service, "_FALLBACK_MODELS", ["first", "second"]),
        ):
            result = analysis_service._call_gemini(["test content"])
        return result, generate

    def test_retired_model_falls_back(self):
        result, generate = self.call_with([
            ModelError(404), SimpleNamespace(text='{"score": 75}')
        ])
        self.assertEqual(result["score"], 75)
        self.assertEqual(
            [call.kwargs["model"] for call in generate.call_args_list],
            ["first", "second"],
        )

    def test_busy_model_falls_back(self):
        result, _ = self.call_with([
            ModelError(503), SimpleNamespace(text='{"score": 50}')
        ])
        self.assertEqual(result["score"], 50)

    def test_invalid_key_does_not_try_other_models(self):
        error = ModelError(403)
        with self.assertRaises(RuntimeError) as raised:
            self.call_with([error, AssertionError("must not call another model")])
        self.assertIs(raised.exception.__cause__, error)

    def test_all_models_fail_without_a_fabricated_score(self):
        with self.assertRaises(RuntimeError):
            self.call_with([ModelError(503), ModelError(404)])


if __name__ == "__main__":
    unittest.main()
