"""A human can delete an unused manual source, never a linked project source."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.test_content_projects import WEB_SOURCE


class SourceDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        root = Path(self.temp.name)
        self.app = create_app(root / "studio.sqlite3", root / "assets", run_jobs=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.headers = {"origin": "http://127.0.0.1"}

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def add_manual(self) -> dict:
        response = self.client.post("/api/sources/manual", headers=self.headers, json=WEB_SOURCE)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_unused_manual_source_can_be_deleted_without_creating_a_draft(self) -> None:
        source = self.add_manual()
        response = self.client.request("DELETE", f"/api/sources/{source['id']}", headers=self.headers, json={"expected_url": source["url"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"deleted": True, "id": source["id"]})
        state = self.client.get("/api/state").json()
        self.assertFalse(any(item["id"] == source["id"] for item in state["bookmarks"]))
        self.assertEqual(state["drafts"], [])
        self.assertFalse(state["x_writes_enabled"])

    def test_primary_source_of_an_existing_draft_cannot_be_deleted(self) -> None:
        source = self.add_manual()
        created = self.client.post("/api/drafts", headers=self.headers, json={"source_id": source["id"], "mode": "explainer", "objective": "Açıkla", "audience": "Okur"})
        self.assertEqual(created.status_code, 201, created.text)
        rejected = self.client.request("DELETE", f"/api/sources/{source['id']}", headers=self.headers, json={"expected_url": source["url"]})
        self.assertEqual(rejected.status_code, 422)
        self.assertIn("çalışmada", rejected.json()["error"])
        self.assertEqual(len(self.client.get("/api/state").json()["bookmarks"]), 1)

    def test_attached_source_cannot_be_deleted_even_from_history(self) -> None:
        source = self.add_manual()
        from studio.test_backend import VALID_SOURCE
        imported = self.client.post("/api/import", headers=self.headers, json={"items": [VALID_SOURCE], "source": "browser_bookmarks", "complete": False})
        self.assertEqual(imported.status_code, 200, imported.text)
        draft = self.client.post("/api/drafts", headers=self.headers, json={"source_id": VALID_SOURCE["id"], "mode": "explainer", "objective": "Açıkla", "audience": "Okur"}).json()
        attached = self.client.post(f"/api/drafts/{draft['id']}/sources", headers=self.headers, json={"revision": draft["revision"], "source_id": source["id"]})
        self.assertEqual(attached.status_code, 200, attached.text)
        rejected = self.client.request("DELETE", f"/api/sources/{source['id']}", headers=self.headers, json={"expected_url": source["url"]})
        self.assertEqual(rejected.status_code, 422)
        self.assertIn("geçmişinde", rejected.json()["error"])

    def test_imported_source_and_mismatched_url_are_not_deleted(self) -> None:
        source = self.add_manual()
        stale = self.client.request("DELETE", f"/api/sources/{source['id']}", headers=self.headers, json={"expected_url": "https://example.com/changed"})
        self.assertEqual(stale.status_code, 422)
        from studio.test_backend import VALID_SOURCE
        self.client.post("/api/import", headers=self.headers, json={"items": [VALID_SOURCE], "source": "browser_bookmarks", "complete": False})
        imported = self.client.request("DELETE", f"/api/sources/{VALID_SOURCE['id']}", headers=self.headers, json={"expected_url": VALID_SOURCE["url"]})
        self.assertEqual(imported.status_code, 422)
        self.assertEqual(len(self.client.get("/api/state").json()["bookmarks"]), 2)

    def test_origin_and_read_only_guards_reject_deletion(self) -> None:
        source = self.add_manual()
        path = f"/api/sources/{source['id']}"
        missing_origin = self.client.request("DELETE", path, json={"expected_url": source["url"]})
        self.assertEqual(missing_origin.status_code, 403)
        root = Path(self.temp.name)
        readonly = TestClient(create_app(root / "studio.sqlite3", root / "assets", run_jobs=False, read_only=True), base_url="http://127.0.0.1")
        try:
            rejected = readonly.request("DELETE", path, headers=self.headers, json={"expected_url": source["url"]})
            self.assertEqual(rejected.status_code, 403)
        finally:
            readonly.close()
        self.assertEqual(len(self.client.get("/api/state").json()["bookmarks"]), 1)

    def test_unused_source_evidence_is_removed_with_its_source(self) -> None:
        import sqlite3
        from contextlib import closing
        source = self.add_manual()
        database = Path(self.temp.name) / "studio.sqlite3"
        with closing(sqlite3.connect(database)) as conn:
            conn.execute("INSERT INTO source_evidence(source_id,source_url,data) VALUES(?,?,?)", (source["id"], source["url"], "{}"))
            conn.commit()
        removed = self.client.request("DELETE", f"/api/sources/{source['id']}", headers=self.headers, json={"expected_url": source["url"]})
        self.assertEqual(removed.status_code, 200, removed.text)
        with closing(sqlite3.connect(database)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM source_evidence WHERE source_id=?", (source["id"],)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
