"""Son yerel paket kapısı sunucuda: onaysız/bayat/öneri bekleyen taslak son paket olarak yazılamaz."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import sqlite3
import tempfile
import time
import unittest
import zipfile
from contextlib import closing
from threading import Event, Thread
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from studio.app import create_app
from studio.test_backend import VALID_SOURCE
from studio.test_editorial_fixtures import approval_payload, original_editorial_payload


class FinalPackageGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / "studio.sqlite3", self.root / "assets", run_jobs=False)
        self.store = self.app.state.store
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.headers = {"origin": "http://127.0.0.1"}
        self.store.import_sources([VALID_SOURCE], "manual_bookmark", False)
        created = self.store.create_draft(VALID_SOURCE["id"], "explainer", "Açıkla", "Okur")
        self.angle = self.store.list_angles(created["id"])["angles"][0]
        self.draft = self.store.update_draft(created["id"], 1, original_editorial_payload(self.angle))
        self.exports = self.root / "exports"

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def exported(self) -> list[str]:
        return sorted(path.name for path in self.exports.glob("*.zip")) if self.exports.exists() else []

    def approve(self) -> dict:
        response = self.client.post(f"/api/drafts/{self.draft['id']}/approvals/copy", headers=self.headers, json=approval_payload(self.draft))
        self.assertEqual(response.status_code, 200, response.text)
        self.draft = response.json()
        return self.draft

    def save(self, body: dict):
        return self.client.post(f"/api/drafts/{self.draft['id']}/save-package", headers=self.headers, json=body)

    def final_body(self, draft: dict | None = None) -> dict:
        draft = draft or self.draft
        return {"revision": draft["revision"], "fingerprint": draft["approval_fingerprint"], "confirmed": True}

    @staticmethod
    def manifest(path: Path) -> dict:
        with zipfile.ZipFile(path) as archive:
            return json.loads(archive.read("manifest.json"))

    def test_studio_html_revalidates_before_loading_versioned_scripts(self) -> None:
        for path in ("/", "/index.html"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("no-cache", response.headers.get("cache-control", ""))

    def test_unapproved_final_request_is_rejected_and_writes_nothing(self) -> None:
        response = self.save(self.final_body())
        self.assertIn(response.status_code, {409, 422}, response.text)
        self.assertEqual(self.exported(), [])

    def test_interim_body_stays_backward_compatible_and_is_labelled(self) -> None:
        response = self.save({"revision": self.draft["revision"]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        path = Path(body["path"])
        self.assertNotIn("son-onayli", path.name)
        self.assertEqual(body["purpose"], "interim_review")
        manifest = self.manifest(path)
        self.assertEqual(manifest["package_purpose"], "interim_review")
        self.assertNotIn("final_gate", manifest)
        self.assertFalse(manifest["publication_ready"])

    def test_approved_final_package_is_distinct_and_records_the_gate(self) -> None:
        approved = self.approve()
        response = self.save(self.final_body())
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["purpose"], "final_local")
        self.assertFalse(body["publication_ready"])
        path = Path(body["path"])
        self.assertIn("-son-onayli-", path.name)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), body["sha256"])
        manifest = self.manifest(path)
        self.assertEqual(manifest["package_purpose"], "final_local")
        self.assertFalse(manifest["publication_ready"])
        self.assertFalse(manifest["x_writes_enabled"])
        gate = manifest["final_gate"]
        self.assertEqual(gate["approval_fingerprint"], approved["approval_fingerprint"])
        self.assertEqual(gate["copy_approved_at"], approved["approvals"]["copy"]["at"])
        self.assertIs(gate["human_preview_confirmation"], True)
        self.assertEqual(gate["pending_proposals"], 0)
        self.assertEqual(self.exported(), [path.name])

    def test_final_save_holds_sqlite_write_lock_through_zip_persist(self) -> None:
        """A writer cannot add a pending proposal after the gate but before the ZIP is saved."""
        self.approve()
        reached_file_write, allow_file_write = Event(), Event()
        proposal_started, proposal_done = Event(), Event()
        responses: list = []
        errors: list[BaseException] = []
        proposal_errors: list[BaseException] = []
        original_open = Path.open

        def pause_export(path: Path, mode: str = "r", *args, **kwargs):
            if path.parent == self.exports and mode == "xb":
                reached_file_write.set()
                if not allow_file_write.wait(10):
                    raise TimeoutError("Export did not resume")
            return original_open(path, mode, *args, **kwargs)

        def save_in_thread() -> None:
            try:
                responses.append(self.save(self.final_body()))
            except BaseException as exc:
                errors.append(exc)

        def add_pending_proposal() -> None:
            proposal_started.set()
            try:
                self.store.save_generated_proposal({"id": "post-final", "draft_id": self.draft["id"], "revision": self.draft["revision"]},
                                                   {"title": "Sonraki öneri", "main": "Yeni", "reply": "", "scene_texts": []})
            except BaseException as exc:
                proposal_errors.append(exc)
            finally:
                proposal_done.set()

        proposer = None
        with patch.object(Path, "open", pause_export):
            worker = Thread(target=save_in_thread, daemon=True)
            worker.start()
            try:
                self.assertTrue(reached_file_write.wait(5), "Final save never reached the ZIP write")
                with closing(sqlite3.connect(self.root / "studio.sqlite3", timeout=0)) as other:
                    with self.assertRaises(sqlite3.OperationalError):
                        other.execute("BEGIN IMMEDIATE")
                proposer = Thread(target=add_pending_proposal, daemon=True)
                proposer.start()
                self.assertTrue(proposal_started.wait(5))
                self.assertFalse(proposal_done.wait(0.1), "Proposal committed before final ZIP")
            finally:
                allow_file_write.set()
                worker.join(timeout=10)
                if proposer is not None:
                    proposer.join(timeout=10)
        self.assertFalse(worker.is_alive(), "Final save remained blocked")
        self.assertFalse(proposer is not None and proposer.is_alive(), "Proposal remained blocked")
        self.assertFalse(errors, errors)
        self.assertFalse(proposal_errors, proposal_errors)
        self.assertEqual(responses[0].status_code, 200, responses[0].text)
        manifest = self.manifest(Path(responses[0].json()["path"]))
        self.assertEqual(manifest["final_gate"]["pending_proposals"], 0)
        self.assertEqual([item["status"] for item in self.store.list_proposals()], ["pending"])

    def test_stale_preview_fingerprint_is_rejected(self) -> None:
        self.approve()
        response = self.save({"revision": self.draft["revision"], "fingerprint": "a" * 64, "confirmed": True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.exported(), [])

    def test_older_revision_final_request_conflicts(self) -> None:
        self.approve()
        response = self.save({"revision": self.draft["revision"] - 1, "fingerprint": self.draft["approval_fingerprint"], "confirmed": True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.exported(), [])

    def test_media_change_after_approval_invalidates_the_previewed_package(self) -> None:
        previewed = self.approve()
        picture = io.BytesIO()
        Image.new("RGB", (320, 200), (20, 95, 80)).save(picture, format="PNG")
        uploaded = self.client.post(f"/api/drafts/{self.draft['id']}/upload", headers=self.headers, json={
            "revision": self.draft["revision"], "kind": "photo", "filename": "gate.png",
            "data_base64": base64.b64encode(picture.getvalue()).decode(), "rights": "owned", "alt_text": "Kapı testi görseli.",
        })
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        response = self.save(self.final_body(previewed))
        self.assertIn(response.status_code, {409, 422}, response.text)
        self.assertEqual(self.exported(), [])

    def test_pending_proposal_from_older_revision_blocks_until_discarded(self) -> None:
        self.store.save_generated_proposal({"id": "older-proposal", "draft_id": self.draft["id"], "revision": self.draft["revision"]},
                                           {"title": "Öneri", "main": "Eski öneri", "reply": "", "scene_texts": []})
        proposal_revision = self.draft["revision"]
        self.draft = self.store.update_draft(self.draft["id"], self.draft["revision"], original_editorial_payload(self.angle, title="Yeni başlık"))
        self.approve()
        blocked = self.save(self.final_body())
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self.exported(), [])
        discarded = self.client.post(f"/api/drafts/{self.draft['id']}/proposals/older-proposal/discard", headers=self.headers, json={"revision": proposal_revision})
        self.assertEqual(discarded.status_code, 200, discarded.text)
        allowed = self.save(self.final_body())
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_pending_proposal_before_final_gate_prevents_the_write(self) -> None:
        self.approve()
        self.store.save_generated_proposal({"id": "late", "draft_id": self.draft["id"], "revision": self.draft["revision"]},
                                           {"title": "Geç öneri", "main": "Geç", "reply": "", "scene_texts": []})
        response = self.save(self.final_body())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.exported(), [])

    def test_malformed_approval_record_is_rejected_without_server_error(self) -> None:
        for approvals in ({}, {"copy": "bozuk"}, "bozuk"):
            with self.subTest(approvals=approvals):
                with closing(sqlite3.connect(self.root / "studio.sqlite3")) as conn:
                    data = json.loads(conn.execute("SELECT data FROM drafts WHERE id=?", (self.draft["id"],)).fetchone()[0])
                    data["copy_approved"] = True
                    data["editorial"]["approvals"] = approvals
                    conn.execute("UPDATE drafts SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), self.draft["id"]))
                    conn.commit()
                current = self.store.get_draft(self.draft["id"])
                response = self.save(self.final_body(current))
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.exported(), [])

    def test_final_body_types_are_strict(self) -> None:
        self.approve()
        fingerprint = self.draft["approval_fingerprint"]
        revision = self.draft["revision"]
        bad_bodies = [
            {"revision": revision, "fingerprint": fingerprint, "confirmed": "true"},
            {"revision": revision, "fingerprint": fingerprint, "confirmed": 1},
            {"revision": revision, "fingerprint": fingerprint[:63], "confirmed": True},
            {"revision": revision, "fingerprint": fingerprint.upper(), "confirmed": True},
            {"revision": True, "fingerprint": fingerprint, "confirmed": True},
            {"revision": revision, "fingerprint": fingerprint, "confirmed": True, "path": "C:/evil.zip"},
            {"revision": revision, "confirmed": True},
        ]
        for body in bad_bodies:
            with self.subTest(body=body):
                self.assertEqual(self.save(body).status_code, 422)
        self.assertEqual(self.exported(), [])

    def test_get_export_cannot_be_turned_into_a_final_package(self) -> None:
        self.approve()
        response = self.client.get(f"/api/drafts/{self.draft['id']}/export?purpose=final_local")
        self.assertEqual(response.status_code, 200, response.text)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["package_purpose"], "interim_review")
        self.assertNotIn("final_gate", manifest)

    def test_repeated_interim_save_is_byte_identical_and_not_duplicated(self) -> None:
        first = self.save({"revision": self.draft["revision"]})
        self.assertEqual(first.status_code, 200, first.text)
        time.sleep(1.2)  # eski davranış: ZIP üye zamanı saniye çözünürlüğünde değişir
        second = self.save({"revision": self.draft["revision"]})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["sha256"], second.json()["sha256"])
        self.assertEqual(len(self.exported()), 1)

    def test_state_advertises_the_server_side_final_gate(self) -> None:
        self.assertIs(self.client.get("/api/state").json()["capabilities"].get("final_package_gate"), True)


if __name__ == "__main__":
    unittest.main()
