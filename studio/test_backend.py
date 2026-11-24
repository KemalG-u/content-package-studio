"""Contract tests for the local KarıncaAI Studio backend."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.test_editorial_fixtures import BASELINE_REPLY, approval_payload, original_claim_map, original_editorial_payload


VALID_SOURCE = {
    "id": "1844674407370955161",
    "url": "https://x.com/karincaai/status/1844674407370955161",
    "title": "Sentetik kaynak",
    "text": "Bu yalnızca test için sentetik kaynak metnidir.",
    "author": "KarıncaAI",
    "observed_at": "2026-09-06T12:00:00+03:00",
    "evidence_note": "Yerel test verisi",
    "verified_urls": ["https://example.invalid/evidence"],
}


class BackendContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        root = Path(self.temp.name)
        self.app = create_app(db_path=root / "studio.sqlite3", asset_root=root / "assets", run_jobs=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def request(self, method: str, path: str, **kwargs):
        kwargs.setdefault("headers", {"origin": "http://127.0.0.1"})
        return self.client.request(method, path, **kwargs)

    def import_source(self) -> None:
        response = self.request(
            "POST", "/api/import", json={"items": [VALID_SOURCE], "source": "browser_bookmarks", "complete": False}
        )
        self.assertEqual(response.status_code, 200, response.text)

    def create_draft(self, *, editorial_ready: bool = True) -> dict:
        self.import_source()
        response = self.request(
            "POST", "/api/drafts",
            json={"source_id": VALID_SOURCE["id"], "mode": "explainer", "objective": "Açıkla", "audience": "Türkiye"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        draft = response.json()
        if not editorial_ready:
            return draft
        angles = self.client.get(f"/api/drafts/{draft['id']}/angles")
        self.assertEqual(angles.status_code, 200, angles.text)
        seeded = self.request(
            "PUT", f"/api/drafts/{draft['id']}",
            json={"revision": draft["revision"], **original_editorial_payload(angles.json()["angles"][0])},
        )
        self.assertEqual(seeded.status_code, 200, seeded.text)
        return seeded.json()

    def test_import_is_idempotent_and_never_fetches_network(self) -> None:
        payload = {"items": [VALID_SOURCE], "source": "browser_bookmarks", "complete": False}
        first = self.request("POST", "/api/import", json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), {"imported": 1, "duplicates": 0, "total": 1, "complete": False})
        second = self.request("POST", "/api/import", json=payload)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json(), {"imported": 0, "duplicates": 1, "total": 1, "complete": False})
        state = self.client.get("/api/state")
        self.assertEqual(state.status_code, 200)
        self.assertEqual(len(state.json()["bookmarks"]), 1)
        self.assertFalse(state.json()["x_writes_enabled"])

    def test_import_rejects_nonmatching_x_id_and_url(self) -> None:
        bad = dict(VALID_SOURCE, id="not-an-x-id")
        response = self.request("POST", "/api/import", json={"items": [bad], "source": "manual_bookmark", "complete": False})
        self.assertEqual(response.status_code, 422)
        self.assertIn("error", response.json())

    def test_draft_edit_requires_current_revision_and_invalidates_approval(self) -> None:
        draft = self.create_draft()
        self.assertEqual(draft["revision"], 2)
        changed = self.request(
            "PUT", f"/api/drafts/{draft['id']}",
            json={
                "revision": draft["revision"], "main": "İnsan tarafından yazılmış ana metin.", "title": "Başlık",
                "claim_map": original_claim_map("Başlık", "İnsan tarafından yazılmış ana metin.", BASELINE_REPLY),
            },
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        current = changed.json()
        self.assertEqual(current["revision"], 3)
        reviewed = self.request("POST", f"/api/drafts/{draft['id']}/approvals/copy", json=approval_payload(current))
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertTrue(reviewed.json()["copy_approved"])
        stale = self.request(
            "PUT", f"/api/drafts/{draft['id']}",
            json={"revision": current["revision"], "main": "Yeni metin.", "claim_map": original_claim_map("Başlık", "Yeni metin.", BASELINE_REPLY)},
        )
        self.assertEqual(stale.status_code, 200, stale.text)
        self.assertFalse(stale.json()["copy_approved"])
        conflict = self.request("PUT", f"/api/drafts/{draft['id']}", json={"revision": current["revision"], "title": "Eski"})
        self.assertEqual(conflict.status_code, 409)
        self.assertIn("error", conflict.json())

    def test_legacy_review_route_stays_closed_without_creating_an_approval(self) -> None:
        draft = self.create_draft()
        rejected = self.request("POST", f"/api/drafts/{draft['id']}/review", json={"revision": draft["revision"]})
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertIn("kapalı", rejected.json()["error"])
        self.assertFalse(self.app.state.store.get_draft(draft["id"])["copy_approved"])

    def test_media_requires_approved_input_and_never_returns_a_fake_asset(self) -> None:
        draft = self.create_draft(editorial_ready=False)
        response = self.request("POST", f"/api/drafts/{draft['id']}/media", json={"revision": 1, "kind": "card"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("ana metin", response.json()["error"].lower())

    def test_generation_creates_a_real_worker_job(self) -> None:
        draft = self.create_draft()
        response = self.request(
            "POST", f"/api/drafts/{draft['id']}/generate", json={"revision": draft["revision"], "instruction": "Yalnız sentetik kaynakla kısa taslak oluştur."}
        )
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["job_id"]
        state = self.client.get("/api/state").json()
        job = next(job for job in state["jobs"] if job["id"] == job_id)
        self.assertIn(job["status"], {"pending", "running", "succeeded", "failed"})
        self.assertEqual(job["kind"], "generate")

    @unittest.skipUnless(os.environ.get('STUDIO_LIVE_MODEL') == '1', 'needs a Hermes agent checkout and a model subscription; set STUDIO_LIVE_MODEL=1')
    def test_generation_route_uses_isolated_real_hermes_worker(self) -> None:
        self.client.close()
        root = Path(self.temp.name)
        self.app = create_app(db_path=root / "studio.sqlite3", asset_root=root / "assets", run_jobs=True)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        draft = self.create_draft()
        response = self.request(
            "POST", f"/api/drafts/{draft['id']}/generate", json={"revision": draft["revision"], "instruction": "Yalnız sentetik kaynakla kısa ve dürüst taslak oluştur."}
        )
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["job_id"]
        import time
        for _ in range(30):
            state = self.client.get("/api/state").json()
            job = next(job for job in state["jobs"] if job["id"] == job_id)
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(4)
        self.assertEqual(job["status"], "succeeded", job)
        self.assertIn("response_id=", job["detail"])
        self.assertTrue((root / "assets" / "model-worker.jsonl").is_file())

    def test_media_assets_and_export_are_real_and_path_safe(self) -> None:
        self.client.close()
        root = Path(self.temp.name)
        self.app = create_app(db_path=root / "studio.sqlite3", asset_root=root / "assets", run_jobs=True)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        draft = self.create_draft()
        updated = self.request(
            "PUT", f"/api/drafts/{draft['id']}",
            json={"revision": draft["revision"], "title": "Sentetik başlık", "main": "Bu test için üretilen özgün açıklayıcı metindir.", "claim_map": original_claim_map("Sentetik başlık", "Bu test için üretilen özgün açıklayıcı metindir.", BASELINE_REPLY)},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        response = self.request("POST", f"/api/drafts/{draft['id']}/media", json={"revision": updated.json()["revision"], "kind": "card"})
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["job_id"]
        import time
        for _ in range(15):
            state = self.client.get("/api/state").json()
            job = next(job for job in state["jobs"] if job["id"] == job_id)
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(1)
        self.assertEqual(job["status"], "succeeded", job)
        state = self.client.get("/api/state").json()
        saved = next(item for item in state["drafts"] if item["id"] == draft["id"])
        asset = next(item for item in saved["assets"] if item["name"] == "card.png")
        served = self.client.get(f"/api/assets/{draft['id']}/{asset['name']}")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers["content-type"], "image/png")
        unsafe = self.client.get(f"/api/assets/{draft['id']}/%2e%2e%2fstudio.sqlite3")
        self.assertIn(unsafe.status_code, {404, 422})
        exported = self.client.get(f"/api/drafts/{draft['id']}/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertIn("card.png", archive.namelist())
            self.assertIn("main.txt", archive.namelist())
            self.assertEqual(manifest["files"]["card.png"], asset["sha256"])
            self.assertTrue(manifest["review_required"])

    def test_changed_copy_marks_media_stale_and_excludes_it_from_export(self) -> None:
        self.client.close()
        root = Path(self.temp.name)
        self.app = create_app(db_path=root / "studio.sqlite3", asset_root=root / "assets", run_jobs=True)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        draft = self.create_draft()
        first = self.request(
            "PUT", f"/api/drafts/{draft['id']}",
            json={"revision": draft["revision"], "main": "İlk özgün metin.", "title": "İlk", "claim_map": original_claim_map("İlk", "İlk özgün metin.", BASELINE_REPLY)},
        )
        self.assertEqual(first.status_code, 200, first.text)
        job_id = self.request("POST", f"/api/drafts/{draft['id']}/media", json={"revision": first.json()["revision"], "kind": "card"}).json()["job_id"]
        import time
        for _ in range(15):
            job = next(job for job in self.client.get("/api/state").json()["jobs"] if job["id"] == job_id)
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(1)
        self.assertEqual(job["status"], "succeeded", job)
        changed = self.request(
            "PUT", f"/api/drafts/{draft['id']}",
            json={"revision": first.json()["revision"], "main": "Düzeltilmiş özgün metin.", "claim_map": original_claim_map("İlk", "Düzeltilmiş özgün metin.", BASELINE_REPLY)},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertTrue(changed.json()["assets"][0]["stale"])
        bundle = self.client.get(f"/api/drafts/{draft['id']}/export")
        self.assertEqual(bundle.status_code, 200, bundle.text)
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            self.assertNotIn("card.png", archive.namelist())

    def test_local_same_origin_policy_returns_turkish_json_errors(self) -> None:
        rejected = self.client.post("/api/import", json={"items": [], "source": "manual_bookmark", "complete": False})
        self.assertEqual(rejected.status_code, 403)
        self.assertIn("error", rejected.json())
        wrong_origin = self.client.post(
            "/api/import", headers={"origin": "http://localhost"}, json={"items": [], "source": "manual_bookmark", "complete": False}
        )
        self.assertEqual(wrong_origin.status_code, 403)
        self.assertIn("error", wrong_origin.json())
        missing = self.client.get("/api/assets/nope/not-found.png")
        self.assertEqual(missing.status_code, 422)
        self.assertIn("error", missing.json())


if __name__ == "__main__":
    unittest.main(verbosity=2)
