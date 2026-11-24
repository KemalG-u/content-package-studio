"""RED→GREEN contracts for editorial hardening; offline disposable SQLite only."""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.editorial import (
    EditorialError,
    MODEL_REVIEW_KEYS,
    approval_fingerprint,
    build_model_review_prompt,
    deterministic_review,
    normalize_model_review_semantics,
    parse_model_review,
)
from studio.test_backend import VALID_SOURCE


REVIEW_KEYS = MODEL_REVIEW_KEYS
LEGACY_REVIEW_KEYS = ("usefulness", "concreteness", "depth", "originality", "cliches", "consistency")


class ModelReviewSchemaTests(unittest.TestCase):
    def test_model_review_requires_exact_unique_questions_and_nonempty_explanations(self) -> None:
        valid = {
            "summary": "Metin somutlaştırılmalı.",
            "questions": [{"key": key, "ok": index % 2 == 0, "reason": f"{key} için gerekçe."} for index, key in enumerate(REVIEW_KEYS)],
            "recommended_action": "revise",
        }
        parsed = parse_model_review(json.dumps(valid))
        self.assertEqual(parsed["questions"], valid["questions"])
        self.assertEqual(parsed["semantics_version"], 2)

        repeated = json.loads(json.dumps(valid))
        repeated["questions"][-1]["key"] = "usefulness"
        with self.assertRaises(EditorialError):
            parse_model_review(json.dumps(repeated))

        blank = json.loads(json.dumps(valid))
        blank["summary"] = " \n "
        with self.assertRaises(EditorialError):
            parse_model_review(json.dumps(blank))
        blank = json.loads(json.dumps(valid))
        blank["questions"][0]["reason"] = "\t"
        with self.assertRaises(EditorialError):
            parse_model_review(json.dumps(blank))

    def test_ready_recommendation_cannot_contradict_failed_criterion(self):
        review = {'summary':'İncele','questions':[{'key':key,'ok':False,'reason':'Eksik koşul'} for key in REVIEW_KEYS],'recommended_action':'ready_for_human_review'}
        with self.assertRaises(EditorialError):
            parse_model_review(json.dumps(review))

    def test_model_review_prompt_names_each_active_standard_question(self) -> None:
        draft = {"mode": "explainer", "objective": "Açıkla", "title": "Başlık", "main": "Metin.", "reply": "", "scene_texts": [], "assets": []}
        prompt = build_model_review_prompt(draft, VALID_SOURCE)
        for key in REVIEW_KEYS:
            self.assertIn(key, prompt)
        self.assertIn("okur bu içerikten gerçek işinde", prompt.lower())
        self.assertIn("evidence:", prompt)
        self.assertIn("teknik olmayan okur", prompt.lower())
        self.assertIn("natural_voice:", prompt)

    def test_legacy_cliche_answer_is_inverted_once_to_positive_ok_semantics(self) -> None:
        legacy = {
            "kind": "model",
            "summary": "Eski soru olumsuz kurulmuştu.",
            "questions": [
                {"key": key, "ok": key != "cliches", "reason": "Belirgin klişe yok."}
                for key in LEGACY_REVIEW_KEYS
            ],
            "recommended_action": "revise",
        }
        normalized = normalize_model_review_semantics(legacy)
        self.assertTrue(next(item for item in normalized["questions"] if item["key"] == "cliches")["ok"])
        self.assertEqual(normalized["recommended_action"], "ready_for_human_review")
        self.assertEqual(normalized["semantics_version"], 2)
        self.assertTrue(normalized["legacy_semantics_corrected"])
        self.assertEqual(normalize_model_review_semantics(normalized), normalized)


class EditorialHardeningApiTests(unittest.TestCase):
    def setUp(self) -> None:
        # Disposable DB beside the code, never inside the real studio/data folder.
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        root = Path(self.temp.name)
        self.app = create_app(root / "studio.sqlite3", root / "assets", run_jobs=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.headers = {"origin": "http://127.0.0.1"}
        imported = self.client.post("/api/import", headers=self.headers, json={"items": [VALID_SOURCE], "source": "manual_bookmark", "complete": False})
        self.assertEqual(imported.status_code, 200, imported.text)
        made = self.client.post("/api/drafts", headers=self.headers, json={"source_id": VALID_SOURCE["id"], "mode": "explainer", "objective": "Koşulları anlamak.", "audience": "Türkçe okur"})
        self.assertEqual(made.status_code, 201, made.text)
        self.draft = made.json()

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def _brief(self, angle: dict, *, provenance: str = "researched_external") -> dict:
        return {"reader_problem": "Koşulları anlamak.", "benefit": "Doğru seçimi yapar.", "depth": "intermediate", "content_type": angle["content_type"], "provenance": provenance, "original_contribution": "Sınırları Türkçe ayırır.", "selected_angle": angle}

    def _source_map(self, text: str) -> dict:
        return {"claims": [{"id": "c1", "text": text, "kind": "fact", "evidence_refs": ["e1"]}], "evidence": [{"id": "e1", "kind": "source", "url": VALID_SOURCE["url"]}]}

    def _save_brief(self, *, provenance: str = "researched_external") -> dict:
        angle = self.client.get(f"/api/drafts/{self.draft['id']}/angles").json()["angles"][0]
        response = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": self.draft["revision"], "reader_brief": self._brief(angle, provenance=provenance)})
        self.assertEqual(response.status_code, 200, response.text)
        self.draft = response.json()
        return angle

    def _failed_model_review(self) -> dict:
        return {"summary": "Metin revize edilmeli.", "questions": [{"key": key, "ok": False, "reason": "Somut gerekçe."} for key in REVIEW_KEYS], "recommended_action": "revise"}

    def test_loading_existing_draft_normalizes_legacy_cliche_semantics(self) -> None:
        legacy = {
            "summary": "Eski inceleme.",
            "questions": [
                {"key": key, "ok": key != "cliches", "reason": "Belirgin klişe yok."}
                for key in LEGACY_REVIEW_KEYS
            ],
            "recommended_action": "revise",
        }
        self.app.state.store.save_model_review(self.draft["id"], self.draft["revision"], legacy)
        loaded = self.app.state.store.get_draft(self.draft["id"])
        review = loaded["editorial"]["reviews"]["model"][-1]
        self.assertEqual(review["semantics_version"], 2)
        self.assertTrue(next(item for item in review["questions"] if item["key"] == "cliches")["ok"])
        self.assertEqual(review["recommended_action"], "ready_for_human_review")

    def test_tested_external_rejects_empty_and_original_only_claim_maps(self) -> None:
        angle = self._save_brief(provenance="tested_external")
        for claim_map in (
            {"claims": [], "evidence": []},
            {"claims": [{"id": "c1", "text": "Bu yalnız yorumdur.", "kind": "original", "evidence_refs": ["e1"]}], "evidence": [{"id": "e1", "kind": "original"}]},
        ):
            with self.subTest(claim_map=claim_map):
                response = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": self.draft["revision"], "reader_brief": self._brief(angle, provenance="tested_external"), "claim_map": claim_map})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("deney", response.json()["error"].lower())

    def test_repeating_deterministic_review_does_not_inflate_failed_revisions(self) -> None:
        self._save_brief()
        path=f"/api/drafts/{self.draft['id']}/editorial-review"
        first=self.client.post(path,headers=self.headers,json={'revision':self.draft['revision']}).json()
        second=self.client.post(path,headers=self.headers,json={'revision':self.draft['revision']}).json()
        self.assertEqual(first['failed_revision_count'],1)
        self.assertEqual(second['failed_revision_count'],1)
        self.assertEqual(first,second)
        saved=self.client.put(f"/api/drafts/{self.draft['id']}",headers=self.headers,json={'revision':self.draft['revision'],'title':'İkinci başarısız revizyon'}).json()
        third=self.client.post(path,headers=self.headers,json={'revision':saved['revision']}).json()
        self.assertEqual(third['failed_revision_count'],2)
        self.assertEqual(third['next_action'],'return_to_brief')

    def test_partial_claim_map_cannot_be_human_approved(self) -> None:
        self._save_brief()
        saved=self.client.put(f"/api/drafts/{self.draft['id']}",headers=self.headers,json={'revision':self.draft['revision'],'main':'Metin. Haritada olmayan ikinci iddia.','claim_map':self._source_map('Metin.')})
        self.assertEqual(saved.status_code,200,saved.text)
        draft=saved.json()
        approved=self.client.post(f"/api/drafts/{draft['id']}/approvals/copy",headers=self.headers,json={'revision':draft['revision'],'confirmed':True,'fingerprint':draft['approval_fingerprint']})
        self.assertEqual(approved.status_code,422,approved.text)
        self.assertIn('Kural',approved.json()['error'])

    def test_deterministic_review_is_not_true_when_claim_text_does_not_cover_copy(self) -> None:
        self._save_brief()
        response = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": self.draft["revision"], "main": "Metindeki özgül iddia kanıt haritasında yok.", "claim_map": self._source_map("Haritadaki başka bir iddia.")})
        self.assertEqual(response.status_code, 200, response.text)
        self.draft = response.json()
        review = self.client.post(f"/api/drafts/{self.draft['id']}/editorial-review", headers=self.headers, json={"revision": self.draft["revision"]})
        self.assertEqual(review.status_code, 200, review.text)
        self.assertFalse(review.json()["passed"])
        coverage = next(question for question in review.json()["questions"] if question["key"] == "claim_coverage")
        self.assertFalse(coverage["ok"])
        self.assertIn("anlamsal", review.json()["semantic_truth_note"].lower())

    def test_fingerprint_changes_for_source_snapshot_and_selected_asset_accessibility_context(self) -> None:
        draft = {
            "revision": 3, "title": "Başlık", "main": "Metin", "reply": "", "scene_texts": [], "alt_text": "Taslak alt metni",
            "source_url": "https://x.com/a/status/123", "observed_at": "2026-09-08T10:00:00+03:00", "evidence_note": "İlk gözlem.",
            "source_evidence": {"id": "source-v1"}, "primary_media": {"name": "photo.png", "revision": 3, "sha256": "a" * 64},
            "assets": [
                {"name": "photo.png", "kind": "photo", "revision": 3, "sha256": "a" * 64, "size": 11, "rights": "owned", "alt_text": "İlk alt metin", "for_asset": None},
                {"name": "caption.vtt", "kind": "subtitle", "revision": 3, "sha256": "b" * 64, "size": 12, "rights": "licensed", "alt_text": "", "for_asset": "photo.png"},
            ],
        }
        before = approval_fingerprint(draft)
        changes = (
            ("source_url", "https://x.com/b/status/456"), ("observed_at", "2026-09-09T10:00:00+03:00"),
            ("evidence_note", "Yeni gözlem."), ("source_evidence", {"id": "source-v2"}), ("alt_text", "Yeni taslak alt metni"),
        )
        for key, value in changes:
            changed = {**draft, key: value}
            self.assertNotEqual(before, approval_fingerprint(changed), key)
        for asset_key, value in (("alt_text", "Yeni alt metin"), ("for_asset", "other.mp4"), ("rights", "licensed"), ("sha256", "c" * 64), ("size", 99)):
            changed = json.loads(json.dumps(draft))
            changed["assets"][1 if asset_key == "for_asset" else 0][asset_key] = value
            self.assertNotEqual(before, approval_fingerprint(changed), asset_key)

    def test_copy_and_claim_map_changes_mark_audit_stale_and_legacy_review_cannot_bypass_explicit_approval(self) -> None:
        self._save_brief()
        saved = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": self.draft["revision"], "main": "Haritadaki iddia.", "claim_map": self._source_map("Haritadaki iddia.")})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.draft = saved.json()
        reviewed = self.client.post(f"/api/drafts/{self.draft['id']}/editorial-review", headers=self.headers, json={"revision": self.draft["revision"]})
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        legacy = self.client.post(f"/api/drafts/{self.draft['id']}/review", headers=self.headers, json={"revision": self.draft["revision"]})
        self.assertEqual(legacy.status_code, 422, legacy.text)
        self.assertFalse(self.app.state.store.get_draft(self.draft["id"])["copy_approved"])
        changed = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": self.draft["revision"], "main": "Değişmiş harita iddiası."})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["audit"]["status"], "stale")
        self.assertIn("metin", changed.json()["audit"]["reason"].lower())
        self.assertIsNone(changed.json()["claim_map"])
        unbound_approval = self.client.post(f"/api/drafts/{self.draft['id']}/approvals/copy", headers=self.headers, json={"revision": changed.json()["revision"], "fingerprint": changed.json()["approval_fingerprint"], "confirmed": True})
        self.assertEqual(unbound_approval.status_code, 422, unbound_approval.text)
        fresh = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": changed.json()["revision"], "main": "Yeni harita iddiası.", "claim_map": self._source_map("Yeni harita iddiası.")})
        self.assertEqual(fresh.status_code, 200, fresh.text)
        self.assertEqual(fresh.json()["claim_map"], self._source_map("Yeni harita iddiası."))

    def test_tested_external_packages_historic_artifact_at_its_bound_revision(self) -> None:
        root = Path(self.temp.name)
        asset_dir = root / "assets" / self.draft["id"] / "r1"
        asset_dir.mkdir(parents=True)
        body = b"r1-proof"
        (asset_dir / "proof.png").write_bytes(body)
        registered = self.app.state.store.register_media(self.draft["id"], 1, asset_dir, {"files": [{"name": "proof.png", "kind": "photo", "mime": "image/png"}], "rights": "owned", "provenance": "local_upload", "alt_text": "İlk deney kanıtı"})
        self.draft = registered
        angle = self.client.get(f"/api/drafts/{self.draft['id']}/angles").json()["angles"][0]
        historic_map = {"claims": [{"id": "c1", "text": "Bu deneyde daha hızlıydı.", "kind": "experiment", "evidence_refs": ["e1"]}], "evidence": [{"id": "e1", "kind": "local_artifact", "asset_name": "proof.png", "sha256": hashlib.sha256(body).hexdigest(), "limitation": "Yalnız ilk revizyon ve tek örneklemle sınırlıdır."}]}
        saved = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": self.draft["revision"], "main": "Bu deneyde daha hızlıydı.", "reader_brief": self._brief(angle, provenance="tested_external"), "claim_map": historic_map})
        self.assertEqual(saved.status_code, 200, saved.text)
        exported = self.client.get(f"/api/drafts/{self.draft['id']}/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            self.assertEqual(archive.read("evidence/r1/proof.png"), body)
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["files"]["evidence/r1/proof.png"], hashlib.sha256(body).hexdigest())

    def test_two_failed_model_reviews_on_distinct_revisions_block_generation_until_angle_changes(self) -> None:
        first_angle = self._save_brief()
        self.app.state.store.save_model_review(self.draft["id"], self.draft["revision"], self._failed_model_review())
        allowed_after_repeat = self.client.post(f"/api/drafts/{self.draft['id']}/generate", headers=self.headers, json={"revision": self.draft["revision"], "instruction": "Taslak üret."})
        self.assertEqual(allowed_after_repeat.status_code, 202, allowed_after_repeat.text)
        repeated = self.app.state.store.save_model_review(self.draft["id"], self.draft["revision"], self._failed_model_review())
        self.assertIsNone(repeated)
        changed = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": self.draft["revision"], "main": "İkinci farklı revizyon."})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.draft = changed.json()
        self.app.state.store.save_model_review(self.draft["id"], self.draft["revision"], self._failed_model_review())
        blocked = self.client.post(f"/api/drafts/{self.draft['id']}/generate", headers=self.headers, json={"revision": self.draft["revision"], "instruction": "Üçüncü taslak."})
        self.assertEqual(blocked.status_code, 422, blocked.text)
        self.assertIn("brief", blocked.json()["error"].lower())
        offered = self.client.get(f"/api/drafts/{self.draft['id']}/angles").json()["angles"]
        reset = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": self.draft["revision"], "reader_brief": self._brief(next(angle for angle in offered if angle != first_angle))})
        self.assertEqual(reset.status_code, 200, reset.text)
        self.draft = reset.json()
        unblocked = self.client.post(f"/api/drafts/{self.draft['id']}/generate", headers=self.headers, json={"revision": self.draft["revision"], "instruction": "Yeni açıyla taslak."})
        self.assertEqual(unblocked.status_code, 202, unblocked.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
