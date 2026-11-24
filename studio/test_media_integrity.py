"""Regression coverage for media integrity, provenance, and ZIP accessibility records."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import tempfile
import time
import shutil
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from studio.app import create_app
from studio.test_backend import VALID_SOURCE



NEEDS_FFMPEG = unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe not installed")


class MediaIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / "studio.sqlite3", self.root / "assets", run_jobs=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        headers = {"origin": "http://127.0.0.1"}
        imported = self.client.post("/api/import", headers=headers, json={"items": [VALID_SOURCE], "source": "manual_bookmark", "complete": False})
        self.assertEqual(imported.status_code, 200, imported.text)
        created = self.client.post("/api/drafts", headers=headers, json={"source_id": VALID_SOURCE["id"], "mode": "explainer", "objective": "Açıkla", "audience": "Türkiye"})
        self.assertEqual(created.status_code, 201, created.text)
        self.draft = created.json()

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def request(self, method: str, path: str, payload: dict):
        return self.client.request(method, path, headers={"origin": "http://127.0.0.1"}, json=payload)

    def upload_photo(self, color: str) -> dict:
        image = io.BytesIO()
        Image.new("RGB", (48, 32), color).save(image, format="PNG")
        response = self.request("POST", f"/api/drafts/{self.draft['id']}/upload", {
            "revision": self.draft["revision"], "kind": "photo", "filename": "same-name.png",
            "data_base64": base64.b64encode(image.getvalue()).decode("ascii"), "rights": "owned",
            "alt_text": f"{color} test photo",
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["asset"]

    def test_repeated_upload_filename_keeps_first_file_hash_and_database_asset(self) -> None:
        first = self.upload_photo("navy")
        first_path = self.root / "assets" / self.draft["id"] / f"r{self.draft['revision']}" / first["name"]
        before_bytes = first_path.read_bytes()
        before_hash = hashlib.sha256(before_bytes).hexdigest()

        second = self.upload_photo("maroon")

        self.assertNotEqual(second["name"], first["name"])
        self.assertEqual(first_path.read_bytes(), before_bytes)
        self.assertEqual(first["sha256"], before_hash)
        saved = self.app.state.store.get_draft(self.draft["id"])
        original = next(asset for asset in saved["assets"] if asset["name"] == first["name"])
        self.assertEqual(original["sha256"], before_hash)

    def test_upload_revision_conflict_keeps_original_file_and_removes_new_output(self) -> None:
        from studio.store import RevisionConflict

        first = self.upload_photo("navy")
        out = self.root / "assets" / self.draft["id"] / f"r{self.draft['revision']}"
        before_files = {path.name: path.read_bytes() for path in out.iterdir()}
        before_draft = self.app.state.store.get_draft(self.draft["id"])
        raw = io.BytesIO()
        Image.new("RGB", (48, 32), "maroon").save(raw, format="PNG")
        with patch.object(self.app.state.store, "register_media", side_effect=RevisionConflict("Taslak değişti")):
            response = self.request("POST", f"/api/drafts/{self.draft['id']}/upload", {
                "revision": self.draft["revision"], "kind": "photo", "filename": "same-name.png",
                "data_base64": base64.b64encode(raw.getvalue()).decode("ascii"),
                "rights": "owned", "alt_text": "İkinci test fotoğrafı",
            })
        self.assertEqual(response.status_code, 409)
        self.assertEqual({path.name: path.read_bytes() for path in out.iterdir()}, before_files)
        self.assertEqual(self.app.state.store.get_draft(self.draft["id"]), before_draft)
        self.assertEqual(hashlib.sha256((out / first['name']).read_bytes()).hexdigest(), first['sha256'])

    def test_interrupted_copy_removes_only_its_partial_output(self) -> None:
        from studio.app import _copy_as_new

        source = self.root / "input.bin"
        source.write_bytes(b"new-content")
        out = self.root / "copy-target"
        out.mkdir()
        original = out / "existing.bin"
        original.write_bytes(b"keep-me")

        def partial_copy(input_handle, output_handle):
            output_handle.write(input_handle.read(3))
            raise OSError("simulated disk failure")

        with patch("studio.app.shutil.copyfileobj", side_effect=partial_copy):
            with self.assertRaises(OSError):
                _copy_as_new(source, out, "existing.bin")
        self.assertEqual(original.read_bytes(), b"keep-me")
        self.assertEqual(list(out.iterdir()), [original], "Yarım çıktı diskte sahipsiz kalmamalıdır")

    def test_two_transcriptions_use_distinct_files_without_replacing_first_transcript(self) -> None:
        from studio.transcribe import transcribe_owned

        first_input = self.root / "first.mp3"
        second_input = self.root / "second.mp3"
        first_input.write_bytes(b"first owned audio")
        second_input.write_bytes(b"second owned audio")
        out_dir = self.root / "transcripts"

        def asr_worker(*_args, **kwargs):
            raw = base64.b64decode(json.loads(kwargs["input"].decode("utf-8"))["data"])
            data = {"source_sha256": hashlib.sha256(raw).hexdigest(), "temporary_input_removed": True,
                    "segments": [{"start": 0, "end": 1, "text": raw.decode("utf-8")}], "duration": 1}
            return subprocess.CompletedProcess([], 0, stdout=json.dumps(data).encode("utf-8"), stderr=b"")

        with patch("studio.transcribe.subprocess.run", side_effect=asr_worker):
            first = transcribe_owned(first_input, out_dir, True)
            first_srt = next(item["name"] for item in first["files"] if item["mime"] == "application/x-subrip")
            first_bytes = (out_dir / first_srt).read_bytes()
            second = transcribe_owned(second_input, out_dir, True)

        second_srt = next(item["name"] for item in second["files"] if item["mime"] == "application/x-subrip")
        self.assertNotEqual(first_srt, second_srt)
        self.assertEqual((out_dir / first_srt).read_bytes(), first_bytes)
        self.assertEqual({item["for_asset"] for item in first["files"] if item["kind"] == "subtitle"}, {"first.mp3"})
        self.assertEqual({item["for_asset"] for item in second["files"] if item["kind"] == "subtitle"}, {"second.mp3"})

    def test_same_revision_generated_cards_do_not_overwrite_sibling_asset(self) -> None:
        self.client.close()
        self.app = create_app(self.root / "studio.sqlite3", self.root / "assets", run_jobs=True)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        updated = self.request("PUT", f"/api/drafts/{self.draft['id']}", {
            "revision": self.draft["revision"], "title": "Yerel kart", "main": "Çakışmayan yerel kart testi.",
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        revision = updated.json()["revision"]

        def create_and_wait() -> dict:
            queued = self.request("POST", f"/api/drafts/{self.draft['id']}/media", {"revision": revision, "kind": "card"})
            self.assertEqual(queued.status_code, 202, queued.text)
            job_id = queued.json()["job_id"]
            for _ in range(100):
                job = next(item for item in self.app.state.store.list_jobs() if item["id"] == job_id)
                if job["status"] in {"succeeded", "failed"}:
                    self.assertEqual(job["status"], "succeeded", job)
                    return self.app.state.store.get_draft(self.draft["id"])
                time.sleep(0.03)
            self.fail("medya işi zamanında bitmedi")

        first_draft = create_and_wait()
        first = next(asset for asset in first_draft["assets"] if asset["name"] == "card.png")
        card_path = self.root / "assets" / self.draft["id"] / f"r{revision}" / first["name"]
        first_bytes = card_path.read_bytes()
        second_draft = create_and_wait()

        cards = [asset for asset in second_draft["assets"] if asset["kind"] == "image"]
        self.assertEqual(len(cards), 2)
        self.assertEqual(card_path.read_bytes(), first_bytes)
        self.assertEqual(next(asset for asset in cards if asset["name"] == "card.png")["sha256"], first["sha256"])

    @NEEDS_FFMPEG
    def test_generated_card_metadata_and_zip_alt_text_are_primary_asset_scoped(self) -> None:
        from studio.media import build_media

        draft = self.app.state.store.update_draft(self.draft["id"], self.draft["revision"], {
            "title": "Yerel kaynak notu", "main": "Bu kart yalnız yerelde üretilen açıklayıcı içeriktir.",
        })
        out_dir = self.root / "assets" / draft["id"] / f"r{draft['revision']}"
        result = build_media(draft, out_dir, "card")
        self.assertEqual(result["provenance"], "local_original_explanatory_card")
        self.assertIsNone(result["rights"])
        self.assertTrue(any("kaynak tweet görseli" in warning.lower() for warning in result["warnings"]))
        self.assertTrue(any("gerçek uygulama demosu" in warning.lower() for warning in result["warnings"]))
        self.assertFalse(any(item["kind"] == "alt" for item in result["files"]))

        video = build_media({**draft, "scene_texts": ["Birinci açıklayıcı sahne.", "İkinci açıklayıcı sahne.", "Üçüncü açıklayıcı sahne."]}, self.root / "video-job", "video")
        self.assertEqual(video["provenance"], "local_original_explanatory_video")
        self.assertIsNone(video["rights"])
        self.assertTrue(any("kaynak tweet görseli" in warning.lower() for warning in video["warnings"]))
        self.assertFalse(any(item["kind"] == "alt" for item in video["files"]))

        saved = self.app.state.store.register_media(draft["id"], draft["revision"], out_dir, result)
        card = next(asset for asset in saved["assets"] if asset["kind"] == "image")
        before_primary = self.client.get(f"/api/drafts/{draft['id']}/export")
        self.assertEqual(before_primary.status_code, 200, before_primary.text)
        with zipfile.ZipFile(io.BytesIO(before_primary.content)) as archive:
            self.assertNotIn("alt-text.txt", archive.namelist())
            self.assertNotIn("alt.tr.txt", archive.namelist())
        self.app.state.store.select_primary_media(draft["id"], draft["revision"], card["name"])
        exported = self.client.get(f"/api/drafts/{draft['id']}/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            self.assertEqual(archive.read("alt-text.txt").decode("utf-8"), card["alt_text"])
            self.assertNotIn("alt.tr.txt", archive.namelist())


if __name__ == "__main__":
    unittest.main(verbosity=2)
