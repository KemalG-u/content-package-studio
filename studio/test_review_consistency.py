"""Text-only approval boundaries, revision CAS, and ZIP review metadata."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.text_budget import measure_many
from studio.test_backend import VALID_SOURCE
from studio.test_editorial_fixtures import approval_payload, original_claim_map, original_editorial_payload


class ReviewConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        root = Path(self.temp.name)
        self.app = create_app(root / "studio.sqlite3", root / "assets", run_jobs=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.headers = {"origin": "http://127.0.0.1"}
        imported = self.client.post(
            "/api/import", headers=self.headers,
            json={"items": [VALID_SOURCE], "source": "manual_bookmark", "complete": False},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        created = self.client.post(
            "/api/drafts", headers=self.headers,
            json={"source_id": VALID_SOURCE["id"], "mode": "explainer", "objective": "Açıkla", "audience": "Türkiye"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.draft = created.json()
        angle = self.client.get(f"/api/drafts/{self.draft['id']}/angles").json()["angles"][0]
        seeded = self.client.put(
            f"/api/drafts/{self.draft['id']}", headers=self.headers,
            json={"revision": self.draft["revision"], **original_editorial_payload(angle)},
        )
        self.assertEqual(seeded.status_code, 200, seeded.text)
        self.draft = seeded.json()

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def save(self, **changes: object) -> dict:
        copy_fields = ("title", "main", "reply")
        if set(changes).intersection(copy_fields) and "claim_map" not in changes:
            title = str(changes.get("title", self.draft["title"]))
            main = str(changes.get("main", self.draft["main"]))
            reply = str(changes.get("reply", self.draft["reply"]))
            changes["claim_map"] = original_claim_map(title, main, reply)
        response = self.client.put(
            f"/api/drafts/{self.draft['id']}", headers=self.headers,
            json={"revision": self.draft["revision"], **changes},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.draft = response.json()
        return self.draft

    def review(self, revision: object | None = None):
        payload = approval_payload(self.draft)
        if revision is not None:
            payload["revision"] = revision
        return self.client.post(
            f"/api/drafts/{self.draft['id']}/approvals/copy", headers=self.headers,
            json=payload,
        )

    def current(self) -> dict:
        return self.app.state.store.get_draft(self.draft["id"])

    def test_short_main_with_empty_reply_is_text_approved_only(self) -> None:
        self.save(main="Kısa ve açık ana metin.", reply="")
        reviewed = self.review()
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        payload = reviewed.json()
        self.assertTrue(payload["copy_approved"])
        self.assertFalse(payload["publication_ready"])
        self.assertFalse(payload["x_render_verified"])
        self.assertFalse(self.client.get("/api/health").json()["x_writes_enabled"])

    def test_review_rejects_empty_or_over_limit_main_and_nonempty_invalid_reply_without_mutating_state(self) -> None:
        cases = [
            {"main": "   ", "reply": "", "claim_map": original_claim_map("", "İddia haritası boş metni onaylamaz.", "")},
            {"main": "a" * 281, "reply": ""},
            {"main": "Geçerli ana metin.", "reply": "b" * 281},
        ]
        for index, copy in enumerate(cases):
            with self.subTest(index=index):
                self.save(**copy)
                before = self.current()
                history_before = self.app.state.store.list_history(self.draft["id"])
                rejected = self.review()
                self.assertEqual(rejected.status_code, 422, rejected.text)
                self.assertIn("X", rejected.json()["error"])
                self.assertEqual(self.current(), before)
                self.assertEqual(self.app.state.store.list_history(self.draft["id"]), history_before)

    def test_review_uses_pinned_x_weighting_for_turkish_family_emoji_url_and_281_boundary(self) -> None:
        rows = measure_many(["Türkçe", "👨‍👩‍👧‍👦", "https://example.com/uzun/baglanti", "a" * 281])
        self.assertEqual([(row["weighted_length"], row["valid"]) for row in rows], [(6, True), (2, True), (23, True), (281, False)])
        self.save(main="Türkçe 👨‍👩‍👧‍👦 https://example.com/uzun/baglanti", reply="")
        self.assertEqual(self.review().status_code, 200)

    def test_review_rejects_boolean_revision_and_stale_revision_without_changing_approved_state_or_history(self) -> None:
        self.save(main="Onaylanabilir metin.", reply="")
        approved = self.review()
        self.assertEqual(approved.status_code, 200, approved.text)
        before = self.current()
        history_before = self.app.state.store.list_history(self.draft["id"])
        boolean = self.review(True)
        self.assertEqual(boolean.status_code, 422, boolean.text)
        stale = self.review(self.draft["revision"] - 1)
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(self.current(), before)
        self.assertEqual(self.app.state.store.list_history(self.draft["id"]), history_before)

    def test_overweight_manual_draft_exports_with_actual_zip_text_lengths_and_separate_text_and_package_warnings(self) -> None:
        self.save(main="a" * 281, reply="")
        exported = self.client.get(f"/api/drafts/{self.draft['id']}/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            main = archive.read("main.txt").decode("utf-8")
            reply = archive.read("reply.txt").decode("utf-8")
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual([count["weighted_length"] for count in measure_many([main, reply])], [281, 0])
        self.assertTrue(manifest["review_required"])
        self.assertTrue(manifest["text_review_required"])
        self.assertTrue(manifest["package_review_required"])
        self.assertEqual(manifest["approval_scope"], "text_only")
        self.assertTrue(any("280" in warning for warning in manifest["warnings"]))
        self.assertTrue(any("medya" in warning.lower() or "paket" in warning.lower() for warning in manifest["warnings"]))
        self.assertFalse(manifest["publication_ready"])
        self.assertFalse(manifest["x_writes_enabled"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
