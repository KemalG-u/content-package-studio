"""API contracts for editorial persistence, immutable media and package integrity."""
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
from studio.test_backend import VALID_SOURCE


class ModernizationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / "studio.sqlite3", self.root / "assets", run_jobs=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.headers = {"origin": "http://127.0.0.1"}
        self.client.post("/api/import", headers=self.headers, json={"items": [VALID_SOURCE], "source": "manual_bookmark", "complete": False})
        made = self.client.post("/api/drafts", headers=self.headers, json={"source_id": VALID_SOURCE["id"], "mode": "explainer", "objective": "Neyi ve ne zaman kullanacağını anlamak.", "audience": "Türkçe okur"})
        self.assertEqual(made.status_code, 201, made.text)
        self.draft = made.json()

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def _brief(self, angle: dict) -> dict:
        return {"reader_problem": "Neyi ve ne zaman kullanacağını anlamak.", "benefit": "Koşula göre doğru seçimi yapar.", "depth": "intermediate", "content_type": angle["content_type"], "provenance": "researched_external", "original_contribution": "Kaynak sınırlarını açıkça ayıran Türkçe açıklama.", "selected_angle": angle}

    def test_editorial_endpoints_persist_a_selected_angle_reviews_and_budget_without_live_model(self) -> None:
        angles = self.client.get(f"/api/drafts/{self.draft['id']}/angles")
        self.assertEqual(angles.status_code, 200, angles.text)
        offered = angles.json()["angles"]
        self.assertEqual(len(offered), 3)
        saved = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={
            "revision": 1, "main": "Bu kaynak hangi koşulda hangi yaklaşımın seçileceğini açıklıyor.",
            "reader_brief": self._brief(offered[0]),
            "claim_map": {"claims": [{"id": "c1", "text": "Bu kaynak hangi koşulda hangi yaklaşımın seçileceğini açıklıyor.", "kind": "fact", "evidence_refs": ["e1"]}], "evidence": [{"id": "e1", "kind": "source", "url": VALID_SOURCE["url"]}]},
            "rights_note": "Harici medya yeniden yüklenmez; kaynak URL'si pakette kalır.",
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        current = saved.json()
        self.assertEqual(current["reader_brief"]["selected_angle"], offered[0])
        self.assertEqual(len(current["approval_fingerprint"]), 64)
        approved = self.client.post(f"/api/drafts/{self.draft['id']}/approvals/copy", headers=self.headers, json={"revision": current["revision"], "fingerprint": current["approval_fingerprint"], "confirmed": True})
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertTrue(approved.json()["copy_approved"])
        first = self.client.post(f"/api/drafts/{self.draft['id']}/editorial-review", headers=self.headers, json={"revision": current["revision"]})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(len(first.json()["questions"]), 6)
        queued = self.client.post(f"/api/drafts/{self.draft['id']}/model-review", headers=self.headers, json={"revision": current["revision"]})
        self.assertEqual(queued.status_code, 202, queued.text)
        review_state = self.client.get(f"/api/drafts/{self.draft['id']}/reviews")
        self.assertEqual(review_state.status_code, 200, review_state.text)
        state = self.client.get("/api/state").json()
        self.assertEqual(state["model_budget"]["limit"], 12)
        self.assertEqual(state["model_budget"]["used"], 0)
        self.assertEqual(state["model_budget"]["pending"], 1)

    def test_first_generation_accepts_complete_brief_but_approval_and_tested_claims_stay_closed(self) -> None:
        from studio.store import StoreError
        angle = self.client.get(f"/api/drafts/{self.draft['id']}/angles").json()['angles'][1]
        saved = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers,
            json={'revision': 1, 'reader_brief': self._brief(angle)}).json()
        store = self.app.state.store
        store.require_editorial_ready(saved['id'], saved['revision'], for_generation=True)
        with self.assertRaises(StoreError):
            store.require_editorial_ready(saved['id'], saved['revision'])
        brief = dict(saved['reader_brief'], provenance='tested_external')
        saved = self.client.put(f"/api/drafts/{saved['id']}", headers=self.headers,
            json={'revision': saved['revision'], 'reader_brief': brief}).json()
        with self.assertRaises(StoreError):
            store.require_editorial_ready(saved['id'], saved['revision'], for_generation=True)

    def test_tested_external_claim_fails_closed_then_package_and_query_immutable_asset_are_consistent(self) -> None:
        asset_root = self.root / "assets" / self.draft["id"] / "r1"
        asset_root.mkdir(parents=True)
        asset_body = b"local-proof-png-bytes"
        (asset_root / "proof.png").write_bytes(asset_body)
        registered = self.app.state.store.register_media(self.draft["id"], 1, asset_root, {"files": [{"name": "proof.png", "kind": "photo", "mime": "image/png"}], "rights": "owned", "provenance": "local_upload", "alt_text": "Yerel test kanıtı"})
        digest = hashlib.sha256(asset_body).hexdigest()
        angles = self.client.get(f"/api/drafts/{self.draft['id']}/angles").json()["angles"]
        brief = self._brief(angles[0])
        brief.update({"content_type": "experiment", "provenance": "tested_external"})
        bad = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": 1, "reader_brief": brief, "claim_map": {"claims": [{"id": "c1", "text": "Test ettim.", "kind": "experiment", "evidence_refs": ["e1"]}], "evidence": [{"id": "e1", "kind": "local_artifact", "asset_name": "proof.png", "sha256": digest, "limitation": ""}]}})
        self.assertEqual(bad.status_code, 422, bad.text)
        good_map = {"claims": [{"id": "c1", "text": "Bu yerel ölçümde daha hızlıydı.", "kind": "experiment", "evidence_refs": ["e1"]}], "evidence": [{"id": "e1", "kind": "local_artifact", "asset_name": "proof.png", "sha256": digest, "limitation": "Tek Windows makinesi ve tek örneklemle sınırlıdır."}]}
        saved = self.client.put(f"/api/drafts/{self.draft['id']}", headers=self.headers, json={"revision": registered["revision"], "main": "Yerel ölçüm bir sınırla birlikte okunmalı.", "reader_brief": brief, "claim_map": good_map})
        self.assertEqual(saved.status_code, 200, saved.text)
        current = saved.json()
        served = self.client.get(f"/api/assets/{self.draft['id']}/proof.png?revision=1&sha256={digest}")
        self.assertEqual(served.status_code, 200, served.text)
        self.assertEqual(served.headers["cache-control"], "public, max-age=31536000, immutable")
        wrong = self.client.get(f"/api/assets/{self.draft['id']}/proof.png?revision=1&sha256={'0' * 64}")
        self.assertEqual(wrong.status_code, 422, wrong.text)
        exported = self.client.get(f"/api/drafts/{self.draft['id']}/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            self.assertTrue({"reader-brief.json", "claim-map.json", "deterministic-review.json", "model-review.json", "audit.json", "rights.json", "manifest.json"}.issubset(names))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["files"]["claim-map.json"], hashlib.sha256(archive.read("claim-map.json")).hexdigest())
            self.assertEqual(manifest["revision"], current["revision"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
