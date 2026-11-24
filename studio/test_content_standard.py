"""KarıncaAI içerik standardının kalıcı ve aktif sözleşmesi."""
from __future__ import annotations

import copy
import unittest

from studio.content_standard import load_content_standard, validate_content_standard
from studio.generator import build_prompt
from studio.editorial import MODEL_REVIEW_KEYS, build_model_review_prompt, default_reader_brief


class ContentStandardTests(unittest.TestCase):
    def test_active_standard_defines_the_reader_promise_and_never_a_quota(self) -> None:
        standard = load_content_standard()

        self.assertEqual(standard["standard_id"], "karincaai-content-v1")
        self.assertEqual(standard["status"], "active-calibration")
        self.assertIn("teknik olmayan", standard["audience"]["primary"].casefold())
        self.assertIn("gerçek iş", standard["promise"].casefold())
        self.assertIn("ajan kur", standard["promise"].casefold())
        self.assertIn("insan onayı", standard["promise"].casefold())
        self.assertEqual(standard["operation"]["content_quota"], None)
        self.assertEqual(standard["operation"]["publishing_quota"], None)

    def test_standard_makes_evidence_voice_originality_and_review_measurable(self) -> None:
        standard = load_content_standard()

        self.assertEqual(
            [item["key"] for item in standard["review"]["dimensions"]],
            [
                "usefulness", "evidence", "concreteness", "accessibility",
                "originality", "natural_voice", "focus", "boundaries",
            ],
        )
        self.assertTrue(all(item["question"] and item["pass"] for item in standard["review"]["dimensions"]))
        self.assertIn("uydurulmuş deney", " ".join(standard["review"]["hard_failures"]).casefold())
        self.assertIn("özgün katkı", " ".join(standard["originality"]["requirements"]).casefold())
        self.assertIn("gündelik", " ".join(standard["language"]["rules"]).casefold())
        self.assertFalse(standard["voice"]["mandatory_slogan"])
        self.assertGreaterEqual(len(standard["anti_patterns"]), 8)
        self.assertEqual(standard["learning"]["automatic_global_rule_creation"], False)

    def test_generation_prompt_receives_the_versioned_standard(self) -> None:
        draft = {
            "mode": "explainer", "objective": "Bir iş durumunu açıkla", "audience": "Teknik olmayan çalışan",
            "title": "", "main": "", "reply": "", "scene_texts": [], "feedback": "",
        }
        source = {
            "url": "https://example.com/source", "title": "Kaynak", "text": "Kaynak metni",
            "author": "Yazar", "observed_at": "2026-09-23T00:00:00+00:00", "evidence_note": "",
            "verified_urls": [], "kind": "web", "project_sources": [],
        }

        prompt = build_prompt(draft, source, "İlk taslağı üret.")

        self.assertIn('CONTENT_STANDARD={"standard_id":"karincaai-content-v1"', prompt)
        self.assertIn('"content_quota":null', prompt)
        self.assertIn('"key":"evidence"', prompt)
        self.assertIn('"mandatory_slogan":false', prompt)

    def test_new_content_projects_default_to_nontechnical_reader_depth(self) -> None:
        brief = default_reader_brief({"mode": "explainer", "objective": "Açıkla"})

        self.assertEqual(brief["depth"], "beginner")

    def test_model_review_uses_every_active_standard_dimension(self) -> None:
        standard = load_content_standard()
        expected_keys = tuple(item["key"] for item in standard["review"]["dimensions"])
        draft = {"mode": "explainer", "objective": "Açıkla", "title": "Başlık", "main": "Ana metin", "reply": "", "scene_texts": []}
        source = {"url": "https://example.com/source", "title": "Kaynak", "text": "Kaynak metni", "evidence_note": "", "verified_urls": []}

        prompt = build_model_review_prompt(draft, source)

        self.assertEqual(MODEL_REVIEW_KEYS, expected_keys)
        self.assertIn('"standard_id":"karincaai-content-v1"', prompt)
        for dimension in standard["review"]["dimensions"]:
            self.assertIn(f'{dimension["key"]}: {dimension["question"]}', prompt)
            self.assertIn(dimension["pass"], prompt)

    def test_standard_validation_rejects_duplicate_review_dimensions(self) -> None:
        malformed = copy.deepcopy(load_content_standard())
        malformed["review"]["dimensions"][1]["key"] = malformed["review"]["dimensions"][0]["key"]

        with self.assertRaisesRegex(ValueError, "benzersiz"):
            validate_content_standard(malformed)

    def test_standard_contains_contrastive_calibration_examples(self) -> None:
        examples = load_content_standard()["calibration_examples"]

        self.assertEqual([item["verdict"] for item in examples], ["near_target", "needs_reframe"])
        self.assertIn("dosyalarını hemen taşıtma", examples[0]["text"])
        self.assertIn("geri alınabilir", " ".join(examples[0]["reasons"]).casefold())
        self.assertIn("status=continue", examples[1]["text"])
        self.assertIn("teknik", " ".join(examples[1]["reasons"]).casefold())


if __name__ == "__main__":
    unittest.main()
