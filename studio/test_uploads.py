"""Real-file contract tests for local Studio uploads and transcription jobs."""
from __future__ import annotations

import base64
import io
import json
import os
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

ROOT = Path(__file__).resolve().parent


NEEDS_FFMPEG = unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe not installed")


class UploadContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / "studio.sqlite3", self.root / "assets", run_jobs=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.draft = self._create_draft()

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def request(self, method: str, path: str, **kwargs):
        kwargs.setdefault("headers", {"origin": "http://127.0.0.1"})
        return self.client.request(method, path, **kwargs)

    def _create_draft(self) -> dict:
        imported = self.request("POST", "/api/import", json={"items": [VALID_SOURCE], "source": "manual_bookmark", "complete": True})
        self.assertEqual(imported.status_code, 200, imported.text)
        made = self.request("POST", "/api/drafts", json={"source_id": VALID_SOURCE["id"], "mode": "explainer", "objective": "Açıkla", "audience": "Türkiye"})
        self.assertEqual(made.status_code, 201, made.text)
        return made.json()

    @staticmethod
    def _b64(path: Path) -> str:
        return base64.b64encode(path.read_bytes()).decode("ascii")

    def _with_copy(self) -> dict:
        response = self.request("PUT", f"/api/drafts/{self.draft['id']}", json={"revision": self.draft["revision"], "main": "Yerel dosya test metni."})
        self.assertEqual(response.status_code, 200, response.text)
        self.draft = response.json()
        return self.draft

    def test_photo_upload_prepares_real_local_asset_and_manifest_records_rights(self):
        self._with_copy()
        image_path = self.root / "owned.png"
        Image.new("RGB", (1600, 900), "navy").save(image_path, format="PNG")
        uploaded = self.request("POST", f"/api/drafts/{self.draft['id']}/upload", json={
            "revision": self.draft["revision"], "kind": "photo", "filename": "owned.png",
            "data_base64": self._b64(image_path), "rights": "owned", "alt_text": "Lacivert yerel test fotoğrafı.",
        })
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        asset = uploaded.json()["asset"]
        self.assertEqual(asset["mime"], "image/jpeg")
        self.assertEqual(asset["rights"], "owned")
        self.assertEqual(asset["provenance"], "local_upload")
        served = self.client.get(f"/api/assets/{self.draft['id']}/{asset['name']}")
        self.assertEqual(served.status_code, 200, served.text)
        with Image.open(io.BytesIO(served.content)) as prepared:
            self.assertEqual(prepared.format, "JPEG")
            self.assertLessEqual(max(prepared.size), 1280)
        exported = self.client.get(f"/api/drafts/{self.draft['id']}/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["assets"][0]["rights"], "owned")
        self.assertEqual(manifest["assets"][0]["alt_text"], "Lacivert yerel test fotoğrafı.")
        self.assertIn("Yerel", manifest["assets"][0]["warnings"][0])
        self.assertTrue(self.client.get("/api/state").json()["import_complete"])

    @NEEDS_FFMPEG
    def test_audio_upload_runs_ffprobe_and_only_uploaded_audio_can_enter_transcription_queue(self):
        source = self.root / "tone.mp3"
        completed = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:a", "libmp3lame", str(source),
        ], capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        uploaded = self.request("POST", f"/api/drafts/{self.draft['id']}/upload", json={
            "revision": self.draft["revision"], "kind": "audio", "filename": "tone.mp3",
            "data_base64": self._b64(source), "rights": "licensed", "alt_text": "Bir saniyelik test tonu.",
        })
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        asset = uploaded.json()["asset"]
        self.assertEqual(asset["mime"], "audio/mpeg")
        self.assertEqual(asset["rights"], "licensed")
        queued = self.request("POST", f"/api/drafts/{self.draft['id']}/transcribe", json={
            "revision": self.draft["revision"], "filename": asset["name"], "vps_allowed": True,
        })
        self.assertEqual(queued.status_code, 202, queued.text)
        job = next(job for job in self.client.get("/api/state").json()["jobs"] if job["id"] == queued.json()["job_id"])
        self.assertEqual(job["kind"], "transcribe")
        self.assertEqual(job["status"], "pending")

    def test_upload_rejects_path_traversal_magic_mismatch_and_decoded_size_cap(self):
        bad_path = self.request("POST", f"/api/drafts/{self.draft['id']}/upload", json={
            "revision": self.draft["revision"], "kind": "photo", "filename": "../private.png",
            "data_base64": base64.b64encode(b"not a photo").decode(), "rights": "owned", "alt_text": "X",
        })
        self.assertEqual(bad_path.status_code, 422, bad_path.text)
        bad_magic = self.request("POST", f"/api/drafts/{self.draft['id']}/upload", json={
            "revision": self.draft["revision"], "kind": "audio", "filename": "fake.mp3",
            "data_base64": base64.b64encode(b"ID3not-really-an-mp3").decode(), "rights": "owned", "alt_text": "X",
        })
        self.assertEqual(bad_magic.status_code, 422, bad_magic.text)
        oversized = self.request("POST", f"/api/drafts/{self.draft['id']}/upload", json={
            "revision": self.draft["revision"], "kind": "audio", "filename": "large.mp3",
            "data_base64": base64.b64encode(b"x" * (6 * 1024 * 1024 + 1)).decode(), "rights": "owned", "alt_text": "X",
        })
        self.assertEqual(oversized.status_code, 422, oversized.text)

    @NEEDS_FFMPEG
    def test_video_upload_is_ffprobe_validated_and_photo_cannot_be_transcribed(self):
        source = self.root / "clip.mp4"
        completed = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ], capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        uploaded = self.request("POST", f"/api/drafts/{self.draft['id']}/upload", json={
            "revision": self.draft["revision"], "kind": "video", "filename": "clip.mp4",
            "data_base64": self._b64(source), "rights": "owned", "alt_text": "Mavi test videosu.",
        })
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.assertEqual(uploaded.json()["asset"]["mime"], "video/mp4")
        image_path = self.root / "still.png"
        Image.new("RGB", (8, 8), "blue").save(image_path)
        photo = self.request("POST", f"/api/drafts/{self.draft['id']}/upload", json={
            "revision": self.draft["revision"], "kind": "photo", "filename": "still.png",
            "data_base64": self._b64(image_path), "rights": "owned", "alt_text": "Mavi fotoğraf.",
        }).json()["asset"]
        rejected = self.request("POST", f"/api/drafts/{self.draft['id']}/transcribe", json={
            "revision": self.draft["revision"], "filename": photo["name"], "vps_allowed": True,
        })
        self.assertEqual(rejected.status_code, 422, rejected.text)

    @NEEDS_FFMPEG
    def test_transcription_job_registers_worker_outputs_without_running_network_asr(self):
        self.client.close()
        self.app = create_app(self.root / "studio.sqlite3", self.root / "assets", run_jobs=True)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        source = self.root / "speech.mp3"
        completed = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=1",
            "-c:a", "libmp3lame", str(source),
        ], capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        asset = self.request("POST", f"/api/drafts/{self.draft['id']}/upload", json={
            "revision": self.draft["revision"], "kind": "audio", "filename": "speech.mp3",
            "data_base64": self._b64(source), "rights": "owned", "alt_text": "Kısa ses testi.",
        }).json()["asset"]

        def local_worker(input_path: Path, out_dir: Path, vps_allowed: bool) -> dict:
            self.assertTrue(vps_allowed)
            self.assertTrue(input_path.is_file())
            for name, body in (("transcript.tr.srt", "1\n00:00:00,000 --> 00:00:01,000\nTest\n"), ("transcript.tr.vtt", "WEBVTT\n\n"), ("transcript.tr.txt", "Test\n"), ("transcript-evidence.json", "{}")):
                (out_dir / name).write_text(body, encoding="utf-8")
            return {"kind": "transcript", "files": [
                {"name": "transcript.tr.srt", "kind": "subtitle", "mime": "application/x-subrip"},
                {"name": "transcript.tr.vtt", "kind": "subtitle", "mime": "text/vtt"},
                {"name": "transcript.tr.txt", "kind": "transcript", "mime": "text/plain"},
                {"name": "transcript-evidence.json", "kind": "evidence", "mime": "application/json"},
            ], "alt_text": "", "warnings": ["Yerel test işçisi."]}

        with patch("studio.transcribe.transcribe_owned", side_effect=local_worker):
            queued = self.request("POST", f"/api/drafts/{self.draft['id']}/transcribe", json={
                "revision": self.draft["revision"], "filename": asset["name"], "vps_allowed": True,
            })
            self.assertEqual(queued.status_code, 202, queued.text)
            job_id = queued.json()["job_id"]
            for _ in range(30):
                job = next(item for item in self.client.get("/api/state").json()["jobs"] if item["id"] == job_id)
                if job["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.1)
        self.assertEqual(job["status"], "succeeded", job)
        draft = next(item for item in self.client.get("/api/state").json()["drafts"] if item["id"] == self.draft["id"])
        transcript = next(item for item in draft["assets"] if item["name"] == "transcript.tr.srt")
        self.assertEqual(transcript["provenance"], "owned_local_transcription")
        self.assertEqual(transcript["rights"], "owned")
        self.assertEqual(transcript["for_asset"], "speech.mp3")

    def test_streamed_json_body_cap_ignores_forged_content_length(self):
        payload = {
            "revision": self.draft["revision"], "kind": "audio", "filename": "too-big.mp3",
            "data_base64": base64.b64encode(b"x" * (7 * 1024 * 1024)).decode(), "rights": "owned", "alt_text": "X",
        }
        response = self.client.post(
            f"/api/drafts/{self.draft['id']}/upload", content=json.dumps(payload).encode("utf-8"),
            headers={"origin": "http://127.0.0.1", "content-type": "application/json", "content-length": "1"},
        )
        self.assertEqual(response.status_code, 413, response.text)

    def test_restart_marks_pending_job_interrupted_and_stale_jobs_do_not_start(self):
        job = self.app.state.store.create_job("generate", self.draft["id"], self.draft["revision"])
        changed = self.app.state.store.update_draft(self.draft["id"], self.draft["revision"], {"title": "Yeni revizyon"})
        with self.assertRaisesRegex(Exception, "değişti"):
            self.app.state.store.start_job(job["id"])
        stale = next(item for item in self.app.state.store.list_jobs() if item["id"] == job["id"])
        self.assertEqual(stale["status"], "failed")
        self.assertIn("değişti", stale["detail"])
        pending = self.app.state.store.create_job("generate", self.draft["id"], changed["revision"])
        self.client.close()
        restarted = create_app(self.root / "studio.sqlite3", self.root / "assets", run_jobs=False)
        self.client = TestClient(restarted, base_url="http://127.0.0.1")
        interrupted = next(item for item in self.client.get("/api/state").json()["jobs"] if item["id"] == pending["id"])
        self.assertEqual(interrupted["status"], "failed")
        self.assertEqual(interrupted["detail"], "failedInterrupted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
