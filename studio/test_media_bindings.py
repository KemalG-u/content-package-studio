"""Bindings between local media, subtitles, persistent primary media, and ZIP metadata."""
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
from studio.store import RevisionConflict, StoreError, StudioStore
from studio.test_backend import VALID_SOURCE
from studio.test_editorial_fixtures import BASELINE_REPLY, approval_payload, original_claim_map, original_editorial_payload

ROOT = Path(__file__).resolve().parent


def _file(root: Path, name: str, body: bytes) -> None:
    (root / name).write_bytes(body)


class MediaBindingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.store = StudioStore(self.root / "studio.sqlite3")
        self.store.import_sources([VALID_SOURCE], "manual_bookmark", False)
        self.first = self.store.create_draft(VALID_SOURCE["id"], "explainer", "Açıkla", "Türkiye")
        self.second = self.store.create_draft(VALID_SOURCE["id"], "explainer", "Açıkla", "Türkiye")
        self.first = self.store.update_draft(self.first["id"], self.first["revision"], original_editorial_payload(self.store.list_angles(self.first["id"])["angles"][0]))
        self.second = self.store.update_draft(self.second["id"], self.second["revision"], original_editorial_payload(self.store.list_angles(self.second["id"])["angles"][0]))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def register(self, draft: dict, files: list[dict]) -> dict:
        out_dir = self.root / "assets" / draft["id"] / f"r{draft['revision']}"
        out_dir.mkdir(parents=True, exist_ok=True)
        for item in files:
            _file(out_dir, item["name"], item.pop("body", item["name"].encode("utf-8")))
        return self.store.register_media(draft["id"], draft["revision"], out_dir, {"files": files})

    def test_subtitles_persist_explicit_target_for_each_of_two_videos(self) -> None:
        draft = self.register(self.first, [
            {"name": "one.mp4", "kind": "video", "mime": "video/mp4"},
            {"name": "two.mp4", "kind": "video", "mime": "video/mp4"},
            {"name": "one.tr.vtt", "kind": "subtitle", "mime": "text/vtt", "for_asset": "one.mp4"},
            {"name": "two.tr.vtt", "kind": "subtitle", "mime": "text/vtt", "for_asset": "two.mp4"},
        ])
        bindings = {asset["name"]: asset.get("for_asset") for asset in draft["assets"] if asset["kind"] == "subtitle"}
        self.assertEqual(bindings, {"one.tr.vtt": "one.mp4", "two.tr.vtt": "two.mp4"})

    def test_subtitle_rejects_foreign_traversal_and_obsolete_targets(self) -> None:
        self.register(self.first, [{"name": "first.mp4", "kind": "video", "mime": "video/mp4"}])
        with self.assertRaisesRegex(StoreError, "bağlı medya"):
            self.register(self.second, [{"name": "foreign.tr.vtt", "kind": "subtitle", "mime": "text/vtt", "for_asset": "first.mp4"}])
        with self.assertRaisesRegex(StoreError, "güvenli"):
            self.register(self.second, [{"name": "escape.tr.vtt", "kind": "subtitle", "mime": "text/vtt", "for_asset": "../first.mp4"}])
        changed = self.store.update_draft(self.first["id"], self.first["revision"], {"main": "Yeni revizyon."})
        with self.assertRaisesRegex(RevisionConflict, "değişti"):
            self.store.register_media(self.first["id"], 1, self.root, {"files": []})
        self.assertEqual(changed["revision"], 3)

    def test_primary_media_is_persistent_per_draft_without_new_history_revision(self) -> None:
        self.first = self.store.update_draft(self.first["id"], self.first["revision"], {"main": "İncelenecek metin.", "claim_map": original_claim_map("", "İncelenecek metin.", BASELINE_REPLY)})
        first = self.register(self.first, [
            {"name": "first.png", "kind": "photo", "mime": "image/png"},
            {"name": "first.mp4", "kind": "video", "mime": "video/mp4"},
        ])
        second = self.register(self.second, [{"name": "second.png", "kind": "image", "mime": "image/png"}])
        self.store.approve_copy(first["id"], first["revision"], approval_payload(first)["fingerprint"])
        selected = self.store.select_primary_media(first["id"], first["revision"], "first.mp4")
        self.assertEqual(selected["revision"], 3)
        self.assertEqual(selected["primary_media"]["name"], "first.mp4")
        self.assertFalse(selected["copy_approved"])
        self.assertEqual([entry["revision"] for entry in self.store.list_history(first["id"])], [3, 2, 1])
        self.assertEqual(self.store.get_draft(first["id"])["primary_media"]["name"], "first.mp4")
        self.assertIsNone(self.store.get_draft(second["id"]).get("primary_media"))
        other = self.store.select_primary_media(second["id"], second["revision"], "second.png")
        self.assertEqual(other["primary_media"]["name"], "second.png")
        approved = self.store.approve_copy(selected["id"], selected["revision"], approval_payload(selected)["fingerprint"])
        unchanged = self.store.select_primary_media(approved["id"], approved["revision"], "first.mp4")
        self.assertTrue(unchanged["copy_approved"])
        self.assertEqual(unchanged["primary_media"]["name"], "first.mp4")
        self.assertEqual([entry["revision"] for entry in self.store.list_history(first["id"])], [3, 2, 1])
        with self.assertRaisesRegex(StoreError, "görseli veya videosu"):
            self.store.select_primary_media(first["id"], first["revision"], "second.png")
        revised = self.store.update_draft(first["id"], first["revision"], {"main": "Yeni metin."})
        self.assertIsNone(revised["primary_media"])
        with self.assertRaisesRegex(StoreError, "görseli veya videosu"):
            self.store.select_primary_media(first["id"], revised["revision"], "first.mp4")


class MediaBindingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / "studio.sqlite3", self.root / "assets", run_jobs=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.headers = {"origin": "http://127.0.0.1"}
        self.client.post("/api/import", headers=self.headers, json={"items": [VALID_SOURCE], "source": "manual_bookmark", "complete": False})
        made = self.client.post("/api/drafts", headers=self.headers, json={"source_id": VALID_SOURCE["id"], "mode": "explainer", "objective": "Açıkla", "audience": "Türkiye"})
        self.assertEqual(made.status_code, 201, made.text)
        self.draft = made.json()
        angle = self.client.get(f"/api/drafts/{self.draft['id']}/angles").json()["angles"][0]
        self.draft = self.app.state.store.update_draft(self.draft["id"], self.draft["revision"], original_editorial_payload(angle))
        self.draft = self.app.state.store.update_draft(self.draft["id"], self.draft["revision"], {"main": "Paket metni.", "claim_map": original_claim_map("", "Paket metni.", BASELINE_REPLY)})

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def test_primary_route_cas_and_zip_manifest_keep_bound_subtitles_and_selected_hash(self) -> None:
        out_dir = self.root / "assets" / self.draft["id"] / f"r{self.draft['revision']}"
        out_dir.mkdir(parents=True)
        payloads = {
            "one.mp4": b"video-one", "two.mp4": b"video-two",
            "one.tr.vtt": b"WEBVTT\n\none\n", "two.tr.vtt": b"WEBVTT\n\ntwo\n",
        }
        for name, body in payloads.items():
            _file(out_dir, name, body)
        self.app.state.store.register_media(self.draft["id"], self.draft["revision"], out_dir, {"files": [
            {"name": "one.mp4", "kind": "video", "mime": "video/mp4"},
            {"name": "two.mp4", "kind": "video", "mime": "video/mp4"},
            {"name": "one.tr.vtt", "kind": "subtitle", "mime": "text/vtt", "for_asset": "one.mp4"},
            {"name": "two.tr.vtt", "kind": "subtitle", "mime": "text/vtt", "for_asset": "two.mp4"},
        ]})
        selected = self.client.post(f"/api/drafts/{self.draft['id']}/primary-media", headers=self.headers, json={"revision": self.draft["revision"], "filename": "two.mp4"})
        self.assertEqual(selected.status_code, 200, selected.text)
        self.assertEqual(selected.json()["revision"], 3)
        stale = self.client.post(f"/api/drafts/{self.draft['id']}/primary-media", headers=self.headers, json={"revision": 2, "filename": "one.mp4"})
        self.assertEqual(stale.status_code, 409, stale.text)
        exported = self.client.get(f"/api/drafts/{self.draft['id']}/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["primary_media"], {"name": "two.mp4", "revision": 3, "sha256": hashlib.sha256(b"video-two").hexdigest()})
        bindings = {asset["name"]: asset.get("for_asset") for asset in manifest["assets"] if asset["kind"] == "subtitle"}
        self.assertEqual(bindings, {"one.tr.vtt": "one.mp4", "two.tr.vtt": "two.mp4"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
