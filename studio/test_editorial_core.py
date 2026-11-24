"""TDD contracts for the editorial domain; no model or network calls."""
from __future__ import annotations

import unittest

from studio.editorial import (
    EditorialError,
    deterministic_review,
    normalize_editorial,
    suggest_angles,
    validate_claim_map,
)
from studio.generator import parse_generated_draft


class EditorialCoreTests(unittest.TestCase):
    def test_legacy_editorial_gets_readable_defaults_and_three_distinct_angles(self) -> None:
        draft = {"id": "draft-1", "mode": "explainer", "objective": "Bir mekanizmayı anlaşılır kıl.", "audience": "Türkçe okur"}
        editorial = normalize_editorial(draft)
        brief = editorial["reader_brief"]
        self.assertEqual(brief["reader_problem"], draft["objective"])
        self.assertEqual(brief["depth"], "beginner")
        self.assertIsNone(brief["selected_angle"])
        angles = suggest_angles(draft, {"id": "1844674407370955161", "title": "Kaynak"}, brief)
        self.assertEqual(len(angles), 3)
        self.assertEqual(len({angle["id"] for angle in angles}), 3)
        self.assertEqual(len({angle["content_type"] for angle in angles}), 3)

    def test_tested_external_rejects_a_string_claim_without_local_hashed_artifact_and_limitation(self) -> None:
        brief = {
            "reader_problem": "Araç gerçekten işe yarıyor mu?", "benefit": "Sınırı öğrenir.",
            "depth": "advanced", "content_type": "experiment", "provenance": "tested_external",
            "original_contribution": "Yerel tekrarlanabilir kontrol notu.", "selected_angle": None,
        }
        claim_map = {
            "claims": [{"id": "c1", "text": "Test ettim, %50 hızlı.", "kind": "experiment", "evidence_refs": ["e1"]}],
            "evidence": [{"id": "e1", "kind": "local_artifact", "asset_name": "proof.json", "sha256": "a" * 64, "limitation": ""}],
        }
        with self.assertRaisesRegex(EditorialError, "(?i)yerel|sınır"):
            validate_claim_map(claim_map, brief, [])

    def test_tested_external_accepts_matching_local_artifact_and_explicit_limit(self) -> None:
        brief = {
            "reader_problem": "Araç gerçekten işe yarıyor mu?", "benefit": "Sınırı öğrenir.",
            "depth": "advanced", "content_type": "experiment", "provenance": "tested_external",
            "original_contribution": "Yerel tekrarlanabilir kontrol notu.", "selected_angle": None,
        }
        claim_map = {
            "claims": [{"id": "c1", "text": "Bu yerel ölçümde daha hızlıydı.", "kind": "experiment", "evidence_refs": ["e1"]}],
            "evidence": [{"id": "e1", "kind": "local_artifact", "asset_name": "proof.json", "sha256": "a" * 64, "limitation": "Bu tek makine ve tek örneklemle sınırlıdır."}],
        }
        self.assertEqual(validate_claim_map(claim_map, brief, [{"name": "proof.json", "sha256": "a" * 64}]), claim_map)

    def test_two_failed_deterministic_reviews_return_to_brief(self) -> None:
        editorial = normalize_editorial({"mode": "explainer", "objective": "Açıkla"})
        draft = {"title": "", "main": "", "reply": "", "scene_texts": [], "assets": [], **editorial}
        first = deterministic_review(draft, failed_revision_count=0)
        second = deterministic_review(draft, failed_revision_count=1)
        self.assertEqual(len(first["questions"]), 6)
        self.assertEqual(first["next_action"], "revise")
        self.assertEqual(second["next_action"], "return_to_brief")

    def test_progressive_brief_roundtrips_without_being_replaced_by_legacy_defaults(self) -> None:
        brief = {"reader_problem": "", "benefit": "", "depth": "beginner", "content_type": "discovery", "provenance": "researched_external", "original_contribution": "", "selected_angle": None}
        editorial = normalize_editorial({"mode": "explainer", "objective": "Eski amaç", "editorial": {"reader_brief": brief}})
        self.assertEqual(editorial["reader_brief"], brief)

    def test_text_only_model_output_is_valid_but_one_or_two_video_scenes_are_not(self) -> None:
        text_only = parse_generated_draft('{"title":"Başlık","main":"Kısa metin.","reply":"","scene_texts":[]}')
        self.assertEqual(text_only["scene_texts"], [])
        for scenes in (["tek"], ["bir", "iki"]):
            with self.subTest(scenes=scenes):
                with self.assertRaisesRegex(Exception, "sahne"):
                    parse_generated_draft('{"title":"Başlık","main":"Kısa metin.","reply":"","scene_texts":' + repr(scenes).replace("'", '"') + '}')


if __name__ == "__main__":
    unittest.main(verbosity=2)
