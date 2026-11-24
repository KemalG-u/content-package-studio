"""Content projects keep explicitly attached, multi-format sources isolated."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.generator import build_prompt
from studio.store import StudioStore
from studio.test_backend import VALID_SOURCE


SECOND_SOURCE = {
    **VALID_SOURCE,
    "id": "1844674407370955162",
    "url": "https://x.com/karincaai/status/1844674407370955162",
    "title": "İkinci konu",
    "text": "Başka bir içerik projesinin ayrı kaynak metni.",
}

WEB_SOURCE = {
    "kind": "web",
    "url": "https://example.com/ai-agents",
    "title": "AI ajanları için pratik rehber",
    "text": "Ajanın görevi, girdileri ve kabul ölçütü açıkça tanımlanmalıdır.",
    "author": "Örnek yazar",
}


class ContentProjectSourceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.store = StudioStore(Path(self.temp.name) / "studio.sqlite3")
        self.store.import_sources([VALID_SOURCE, SECOND_SOURCE], "manual_bookmark", False)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_attached_sources_are_frozen_and_isolated_per_content_project(self) -> None:
        first = self.store.create_draft(VALID_SOURCE["id"], "explainer", "Bir yöntemi açıkla", "Teknik olmayan çalışan")
        second = self.store.create_draft(SECOND_SOURCE["id"], "explainer", "Başka konuyu açıkla", "Teknik olmayan çalışan")
        web = self.store.create_manual_source(WEB_SOURCE)

        updated = self.store.attach_project_source(first["id"], first["revision"], web["id"])
        first_bundle = self.store.source_for_draft(first["id"])
        second_bundle = self.store.source_for_draft(second["id"])

        self.assertEqual(updated["revision"], 2)
        self.assertEqual([item["url"] for item in first_bundle["project_sources"]], [VALID_SOURCE["url"], WEB_SOURCE["url"]])
        self.assertEqual([item["url"] for item in second_bundle["project_sources"]], [SECOND_SOURCE["url"]])
        self.assertEqual(first_bundle["project_sources"][1]["kind"], "web")
        prompt = build_prompt(updated, first_bundle, "Somut ve kısa yaz.")
        self.assertIn(WEB_SOURCE["url"], prompt)
        self.assertNotIn(SECOND_SOURCE["url"], prompt)

        unchanged = self.store.attach_project_source(first["id"], updated["revision"], web["id"])
        self.assertEqual(unchanged["revision"], updated["revision"], "Aynı kaynak ikinci kez eklenince boş revizyon oluşmamalı")


class ContentProjectSourceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        root = Path(self.temp.name)
        self.app = create_app(root / "studio.sqlite3", root / "assets", run_jobs=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.headers = {"origin": "http://127.0.0.1"}
        imported = self.client.post("/api/import", headers=self.headers, json={"items": [VALID_SOURCE], "source": "manual_bookmark", "complete": False})
        self.assertEqual(imported.status_code, 200, imported.text)
        created = self.client.post("/api/drafts", headers=self.headers, json={"source_id": VALID_SOURCE["id"], "mode": "explainer", "objective": "Açıkla", "audience": "Çalışanlar"})
        self.assertEqual(created.status_code, 201, created.text)
        self.draft = created.json()

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def test_manual_web_instagram_and_youtube_transcript_sources_can_be_added_explicitly(self) -> None:
        payloads = [
            WEB_SOURCE,
            {"kind": "instagram", "url": "https://www.instagram.com/p/example/", "title": "Instagram notu", "text": "Kullanıcının taşıdığı açıklama metni.", "author": "@ornek"},
            {"kind": "youtube_transcript", "url": "https://www.youtube.com/watch?v=abcdefghijk", "title": "Video transkripti", "text": "Bu metin kullanıcı tarafından taşınan transkripttir.", "author": "Örnek kanal"},
        ]
        revision = self.draft["revision"]
        for payload in payloads:
            created = self.client.post("/api/sources/manual", headers=self.headers, json=payload)
            self.assertEqual(created.status_code, 201, created.text)
            source = created.json()
            attached = self.client.post(
                f"/api/drafts/{self.draft['id']}/sources",
                headers=self.headers,
                json={"revision": revision, "source_id": source["id"]},
            )
            self.assertEqual(attached.status_code, 200, attached.text)
            revision = attached.json()["revision"]

        state = self.client.get("/api/state").json()
        current = next(item for item in state["drafts"] if item["id"] == self.draft["id"])
        self.assertEqual([item["kind"] for item in current["project_sources"]], ["x", "web", "instagram", "youtube_transcript"])
        self.assertFalse(state["x_writes_enabled"])

    def test_manual_source_rejects_non_https_and_missing_transcript(self) -> None:
        bad_url = self.client.post("/api/sources/manual", headers=self.headers, json={**WEB_SOURCE, "url": "http://example.com/not-secure"})
        self.assertEqual(bad_url.status_code, 422)
        missing_text = self.client.post("/api/sources/manual", headers=self.headers, json={"kind": "youtube_transcript", "url": "https://youtu.be/abcdefghijk", "title": "Boş", "text": "", "author": "Kanal"})
        self.assertEqual(missing_text.status_code, 422)


if __name__ == "__main__":
    unittest.main()
