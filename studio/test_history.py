"""Revision-history contracts for the local Studio API and SQLite store."""
from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.store import RevisionConflict, StudioStore
from studio.test_backend import VALID_SOURCE
from studio.test_editorial_fixtures import BASELINE_REPLY, approval_payload, original_claim_map, original_editorial_payload


class HistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.store = StudioStore(Path(self.temp.name) / "studio.sqlite3")
        self.store.import_sources([VALID_SOURCE], "manual_bookmark", False)
        self.draft = self.store.create_draft(VALID_SOURCE["id"], "explainer", "Açıkla", "Türkiye")
        angle = self.store.list_angles(self.draft["id"])["angles"][0]
        self.draft = self.store.update_draft(self.draft["id"], self.draft["revision"], original_editorial_payload(angle))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_create_and_changed_save_record_real_snapshots_but_noop_does_not(self) -> None:
        self.assertEqual([entry["revision"] for entry in self.store.list_history(self.draft["id"])], [2, 1])
        saved = self.store.update_draft(self.draft["id"], self.draft["revision"], {"title": "İlk başlık", "main": "İlk metin."})
        history = self.store.list_history(self.draft["id"])
        self.assertEqual([entry["revision"] for entry in history], [3, 2, 1])
        self.assertEqual(history[0]["title"], "İlk başlık")
        unchanged = self.store.update_draft(self.draft["id"], saved["revision"], {"title": "İlk başlık"})
        self.assertEqual(unchanged["revision"], 3)
        self.assertEqual([entry["revision"] for entry in self.store.list_history(self.draft["id"])], [3, 2, 1])

    def test_restore_keeps_old_snapshot_and_invalidates_review_and_assets(self) -> None:
        first = self.store.update_draft(self.draft["id"], self.draft["revision"], {"title": "Önce", "main": "Önceki metin.", "claim_map": original_claim_map("Önce", "Önceki metin.", BASELINE_REPLY)})
        reviewed = self.store.approve_copy(first["id"], first["revision"], approval_payload(first)["fingerprint"])
        current = self.store.update_draft(self.draft["id"], reviewed["revision"], {"title": "Sonra", "main": "Sonraki metin."})
        asset_root = Path(self.temp.name) / "assets"
        asset_root.mkdir()
        (asset_root / "card.png").write_bytes(b"local-history-asset")
        self.store.register_media(self.draft["id"], current["revision"], asset_root, {"files": [{"name": "card.png", "kind": "photo", "mime": "image/png"}]})
        restored = self.store.restore_draft(self.draft["id"], current["revision"], first["revision"])
        self.assertEqual(restored["revision"], 5)
        self.assertEqual(restored["title"], "Önce")
        self.assertEqual(restored["main"], "Önceki metin.")
        self.assertFalse(restored["copy_approved"])
        self.assertFalse(restored["publication_ready"])
        self.assertFalse(restored["x_render_verified"])
        self.assertEqual(len(restored["assets"]), 1)
        self.assertTrue(restored["assets"][0]["stale"])
        self.assertEqual([entry["revision"] for entry in self.store.list_history(self.draft["id"])], [5, 4, 3, 2, 1])
        self.assertEqual(self.store.list_history(self.draft["id"])[2]["main"], "Önceki metin.")

    def test_restore_competes_with_save_using_current_revision_cas(self) -> None:
        changed = self.store.update_draft(self.draft["id"], self.draft["revision"], {"main": "İlk metin."})
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.store.restore_draft, self.draft["id"], changed["revision"], 1),
                pool.submit(self.store.update_draft, self.draft["id"], changed["revision"], {"title": "Eşzamanlı"}),
            ]
        outcomes = []
        for future in futures:
            try:
                outcomes.append("saved" if future.result() else "empty")
            except RevisionConflict:
                outcomes.append("conflict")
        self.assertCountEqual(outcomes, ["saved", "conflict"])


class HistoryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        root = Path(self.temp.name)
        self.app = create_app(root / "studio.sqlite3", root / "assets", run_jobs=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.headers = {"origin": "http://127.0.0.1"}
        self.client.post("/api/import", headers=self.headers, json={"items": [VALID_SOURCE], "source": "manual_bookmark", "complete": False})
        response = self.client.post("/api/drafts", headers=self.headers, json={"source_id": VALID_SOURCE["id"], "mode": "explainer", "objective": "Açıkla", "audience": "Türkiye"})
        self.assertEqual(response.status_code, 201, response.text)
        self.draft = response.json()
        angle = self.client.get(f"/api/drafts/{self.draft['id']}/angles").json()["angles"][0]
        seeded = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": self.draft["revision"], **original_editorial_payload(angle)})
        self.assertEqual(seeded.status_code, 200, seeded.text)
        self.draft = seeded.json()

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def test_history_is_limited_and_restore_returns_the_visible_draft(self) -> None:
        current = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": self.draft["revision"], "main": "İlk metin."}).json()
        current = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": current["revision"], "main": "Son metin."}).json()
        history = self.client.get(f"/api/drafts/{self.draft['id']}/history?limit=1")
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual([item["revision"] for item in history.json()["history"]], [4])
        restored = self.client.post(f"/api/drafts/{self.draft['id']}/restore", headers=self.headers, json={"revision": current["revision"], "restore_revision": 3})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["main"], "İlk metin.")
        self.assertEqual(restored.json()["revision"], 5)

    def test_restore_rejects_stale_current_revision(self) -> None:
        response = self.client.post(f"/api/drafts/{self.draft['id']}/restore", headers=self.headers, json={"revision": 0, "restore_revision": 1})
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main(verbosity=2)
