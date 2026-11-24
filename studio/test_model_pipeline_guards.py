"""Model hattı korumaları: eleştirmen proje sınırını görür; aşırı uzun istem bütçe hakkı yakmaz. Sağlayıcı çağrısı yok."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.editorial import build_model_review_prompt
from studio.store import StudioStore
from studio.test_backend import VALID_SOURCE
from studio.test_editorial_fixtures import original_editorial_payload

EXTRA_URL = "https://example.com/ek-kaynak"


def _extra_source(store: StudioStore, index: int, text: str) -> str:
    return store.create_manual_source({"kind": "web", "url": f"{EXTRA_URL}-{index}", "title": f"Ek kaynak {index}", "text": text, "author": "Test"})["id"]


def _project(store: StudioStore) -> dict:
    store.import_sources([VALID_SOURCE], "manual_bookmark", False)
    created = store.create_draft(VALID_SOURCE["id"], "explainer", "Açıkla", "Okur")
    angle = store.list_angles(created["id"])["angles"][0]
    return store.update_draft(created["id"], 1, original_editorial_payload(angle))


class ModelPipelineGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_model_review_prompt_includes_explicitly_attached_sources_once(self) -> None:
        store = StudioStore(self.root / "db.sqlite3")
        draft = _project(store)
        extra_id = _extra_source(store, 1, "EK KAYNAK METNİ: yalnız bu projeye açıkça bağlandı.")
        draft = store.attach_project_source(draft["id"], draft["revision"], extra_id)
        prompt = build_model_review_prompt(draft, store.source_for_draft(draft["id"]))
        payload = json.loads(prompt.rsplit("\n", 1)[1])
        extra = payload["additional_project_sources"]
        self.assertEqual([item["url"] for item in extra], [f"{EXTRA_URL}-1"])
        self.assertIn("EK KAYNAK METNİ", prompt)
        self.assertEqual(payload["source"]["url"], VALID_SOURCE["url"])

    def test_single_source_review_prompt_has_empty_additional_sources(self) -> None:
        store = StudioStore(self.root / "db.sqlite3")
        draft = _project(store)
        payload = json.loads(build_model_review_prompt(draft, store.source_for_draft(draft["id"])).rsplit("\n", 1)[1])
        self.assertEqual(payload["additional_project_sources"], [])

    def _oversized_project(self, app) -> dict:
        store = app.state.store
        draft = _project(store)
        for index in range(3):
            draft = store.attach_project_source(draft["id"], draft["revision"], _extra_source(store, index, ("Uzun kaynak metni. " * 1000)[:19_000]))
        return draft

    def _assert_failed_before_budget(self, app, endpoint: str, body_extra: dict) -> None:
        store = app.state.store
        draft = self._oversized_project(app)
        with TestClient(app, base_url="http://127.0.0.1") as client, patch("studio.app.run_real_model") as worker:
            queued = client.post(f"/api/drafts/{draft['id']}/{endpoint}", headers={"origin": "http://127.0.0.1"}, json={"revision": draft["revision"], **body_extra})
            self.assertEqual(queued.status_code, 202, queued.text)
            app.state.job_queue.join()
        job = store.list_jobs()[-1]
        self.assertEqual(job["status"], "failed", job)
        self.assertIn("50.000", job["detail"])
        worker.assert_not_called()
        self.assertEqual(store.model_budget_summary()["used"], 0)

    def test_oversized_generation_prompt_fails_before_spending_budget(self) -> None:
        app = create_app(self.root / "db.sqlite3", self.root / "assets")
        self._assert_failed_before_budget(app, "generate", {"instruction": "Kısa bir öneri üret."})

    def test_oversized_review_prompt_fails_before_spending_budget(self) -> None:
        app = create_app(self.root / "db.sqlite3", self.root / "assets")
        self._assert_failed_before_budget(app, "model-review", {})


if __name__ == "__main__":
    unittest.main()
